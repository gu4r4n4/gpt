from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional
import os, json, psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from datetime import datetime
from app.services.vectorstores import get_tc_vs, get_offer_vs

# Compat: pydantic v1 uses @validator, v2 uses @field_validator
try:
    from pydantic import field_validator as _validator
except ImportError:  # pydantic v1
    from pydantic import validator as _validator  # type: ignore

router = APIRouter(prefix="/api/qa", tags=["qa"])
client = OpenAI()

def get_db():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    try: yield conn
    finally: conn.close()

class RankedInsurer(BaseModel):
    insurer_code: str = Field(..., description="e.g. BALTA, BTA")
    score: float = Field(..., ge=0, le=1)
    reason: str
    sources: List[str]  # validate length via validator below

    @_validator("sources")
    def _min_one_source(cls, v):
        if not v or len(v) < 1:
            raise ValueError("at least one source is required")
        return v


class Top3Response(BaseModel):
    product_line: str
    top3: List[RankedInsurer]
    notes: Optional[str] = None

    @_validator("product_line")
    def _upcase_pl(cls, v):
        return (v or "").upper()

    @_validator("top3")
    def _exact_three(cls, v):
        if not isinstance(v, list) or len(v) != 3:
            raise ValueError("top3 must contain exactly 3 items")
        return v

class AskRequest(BaseModel):
    org_id: int
    batch_token: str
    product_line: str = Field(..., regex="^[A-Za-z]+$")
    asked_by_user_id: int
    question: str
    debug: Optional[int] = 0

SYSTEM_INSTRUCTIONS = """You are an insurance underwriting analyst.
Return STRICT JSON only, no prose. Output format:
{
  "product_line": "<UPPER>",
  "top3": [
    {"insurer_code":"<CODE>","score":0.00,"reason":"...","sources":["<filename or retrieval_id>", "..."]},
    {"insurer_code":"<CODE>","score":0.00,"reason":"...","sources":["..."]},
    {"insurer_code":"<CODE>","score":0.00,"reason":"...","sources":["..."]}
  ],
  "notes":"optional"
}
Scoring must be 0..1. Use the attached documents (offer batch + T&C). Cite 2–5 sources per item (filenames or file ids).
If uncertain, still output valid JSON with your best effort and mention uncertainty in notes."""

@router.post("/ask")
def ask(req: AskRequest, conn = Depends(get_db)):
    tc_vs = get_tc_vs(conn, req.org_id, req.product_line)
    offer_vs = get_offer_vs(conn, req.org_id, req.batch_token)
    if not tc_vs:
        raise HTTPException(status_code=404, detail=f"No T&C vector store for {req.product_line}")
    if not offer_vs:
        raise HTTPException(status_code=404, detail=f"No offer vector store for batch_token={req.batch_token}")

    # 1) create thread with user message
    thread = client.beta.threads.create(messages=[{"role":"system","content":SYSTEM_INSTRUCTIONS},
                                                  {"role":"user","content":req.question}])

    # 2) run with both vector stores
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=os.getenv("ASSISTANT_ID_TOP3"),
        tool_resources={"file_search": {"vector_store_ids": [tc_vs, offer_vs]}}
    )

    # 3) poll until completed/failed
    while True:
        r = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if r.status in ("completed","failed","cancelled","expired","requires_action"): break

    if r.status != "completed":
        raise HTTPException(status_code=502, detail=f"Run failed: {r.status}")

    # 4) read last message, parse JSON
    msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1)
    content = ""
    for part in msgs.data[0].content:
        if part.type == "text": content += part.text.value
    content = content.strip()

    # be strict: JSON only
    try:
        parsed = json.loads(content)
        top3 = Top3Response(**parsed)  # validation
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Model did not return valid Top-3 JSON: {e}")

    # 5) log
    with conn.cursor() as cur:
        cur.execute("""
          INSERT INTO public.offer_qa_logs (batch_id, org_id, asked_by_user_id, question, answer_summary, sources)
          SELECT b.id, %s, %s, %s, %s, %s
          FROM public.offer_batches b
          WHERE b.token=%s AND b.org_id=%s
          RETURNING id
        """, (req.org_id, req.asked_by_user_id, req.question,
              json.dumps({"product_line": top3.product_line, "top3": [i.dict() for i in top3.top3]}),
              json.dumps({"source_files": [s for i in top3.top3 for s in i.sources]}),
              req.batch_token, req.org_id))
        row = cur.fetchone()
        conn.commit()
        log_id = row["id"] if row else None

    resp = top3.dict()
    if req.debug:
        resp["_debug"] = {
            "thread_id": thread.id,
            "run_id": run.id,
            "tc_vector_store": tc_vs,
            "offer_vector_store": offer_vs,
        }
        resp["_log_id"] = log_id
    return resp

class LogsQuery(BaseModel):
    org_id: int
    batch_token: Optional[str] = None
    limit: int = 25
    offset: int = 0

@router.get("/logs")
def logs(org_id: int, batch_token: Optional[str] = None, limit: int = 25, offset: int = 0, conn = Depends(get_db)):
    cond = ["org_id=%s"]
    params = [org_id]
    if batch_token:
        cond.append("batch_id = (SELECT id FROM public.offer_batches WHERE token=%s AND org_id=%s)")
        params.extend([batch_token, org_id])
    where = " AND ".join(cond)
    with conn.cursor() as cur:
        cur.execute(f"""
          SELECT id, batch_id, org_id, asked_by_user_id, question, answer_summary, sources, created_at
          FROM public.offer_qa_logs
          WHERE {where}
          ORDER BY created_at DESC
          LIMIT %s OFFSET %s
        """, (*params, limit, offset))
        return {"items": cur.fetchall(), "next_offset": offset + limit}