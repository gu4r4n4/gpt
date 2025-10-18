import os
import json
import traceback
import time
from typing import List, Dict, Any, Optional

import psycopg2
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/qa", tags=["qa"])

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")  # keep aligned with healthz
client = OpenAI(api_key=OPENAI_API_KEY)

class QASource(BaseModel):
    file_id: int
    retrieval_file_id: str
    filename: str
    score: Optional[float] = None

class AskRequest(BaseModel):
    org_id: int = Field(..., example=1)
    batch_token: str = Field(..., example="bt_manual_test_001")
    asked_by_user_id: int = Field(..., example=1)
    question: str = Field(..., example="What's the annual premium and deductible?")

class AskResponse(BaseModel):
    answer: str = Field(..., example="The annual premium is €1,200 and the deductible is €500.")
    sources: List[QASource]
    log_id: Optional[int] = Field(None, example=42)
    batch_token: str = Field(..., example="bt_manual_test_001")
    usage: Optional[Dict[str, Any]] = Field(None, example={"input_tokens": 150, "output_tokens": 75})

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL")
    return psycopg2.connect(DATABASE_URL)

def resolve_batch_by_token(org_id: int, batch_token: str) -> Dict[str, Any]:
    """Get batch row by token and org; ensure exists and not expired/deleted."""
    sql = """
        SELECT id, org_id, token, status, expires_at
        FROM public.offer_batches
        WHERE token = %s AND org_id = %s
        LIMIT 1
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (batch_token, org_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Batch not found for this org")
        b = {
            "id": row[0],
            "org_id": row[1],
            "token": row[2],
            "status": row[3],
            "expires_at": row[4],
        }
    if b["status"] in ("expired", "deleted"):
        raise HTTPException(status_code=410, detail=f"Batch is {b['status']}")
    return b

def get_batch_retrieval_files(batch_id: int) -> List[Dict[str, Any]]:
    """
    Return files in the batch that have retrieval_file_id (ready for search).
    """
    sql = """
        SELECT id, filename, retrieval_file_id
        FROM public.offer_files
        WHERE batch_id = %s AND retrieval_file_id IS NOT NULL
        ORDER BY id ASC
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (batch_id,))
        rows = cur.fetchall() or []
    files = [
        {"id": row[0], "filename": row[1], "retrieval_file_id": row[2]}
        for row in rows
        if row and row[2]
    ]
    print(f"[qa] batch_id={batch_id} ready_files={len(files)}")
    return files

def get_org_vector_store_id(org_id: int) -> str:
    sql = "SELECT vector_store_id FROM public.org_vector_stores WHERE org_id = %s"
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (org_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None



@router.post("/ask", response_model=AskResponse, tags=["qa"], summary="Ask a question scoped to a batch")
def ask(payload: AskRequest = Body(...)):
    """
    Input:
    {
      "org_id": 1,
      "batch_token": "bt_manual_test_001",
      "asked_by_user_id": 1,
      "question": "What's the annual premium and deductible?"
    }
    """

    # 1) validate payload
    # 2) resolve batch_id by token + org_id, check status not expired/deleted
    # 3) fetch ready files in this batch => rows of {id, filename, retrieval_file_id}
    # 4) get org vector_store_id (502 if missing)
    # 5) Build Assistants v2: create thread with user message (question) and ATTACHMENTS (the batch file_ids).
    #    Attachment format for threads.create messages:
    #       {"role":"user","content":[{"type":"input_text","text":question}],
    #        "attachments":[{"file_id": rid, "tools":[{"type":"file_search"}]} for rid in retrieval_ids]}
    # 6) Create a run with model=<OPENAI_MODEL or gpt-4.1-mini>, tools=[{"type":"file_search"}],
    #    tool_resources={"file_search":{"vector_store_ids":[vs_id]}}
    # 7) Poll runs until status == "completed" or "failed"/"expired"/"cancelled" (timeout ~60s)
    # 8) On completed: list messages (limit ~10), find latest assistant message; extract answer text
    #    - Try message.content[*].text.value
    # 9) Extract citations by walking annotations in message.content[*].text.annotations[*].file_id
    #    Map OpenAI file_ids -> DB file rows from step (3)
    # 10) Insert into offer_qa_logs (question, short summary (<=240 chars), sources JSON, org_id, batch_id, asked_by_user_id)
    # 11) Return JSON with {answer, sources, log_id, batch_token, usage? (optional)}

    try:
        org_id = payload.org_id
        batch_token = payload.batch_token.strip()
        asked_by_user_id = payload.asked_by_user_id
        question = payload.question.strip()

        if not org_id or not batch_token or not asked_by_user_id or not question:
            raise HTTPException(status_code=400, detail="Missing required fields: org_id, batch_token, asked_by_user_id, question")

        # resolve batch
        batch = resolve_batch_by_token(org_id, batch_token)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found for org")
        if batch["status"] in ("expired", "deleted"):
            raise HTTPException(status_code=410, detail=f"Batch status={batch['status']}")

        batch_id = batch["id"]

        # ready files
        files = get_batch_retrieval_files(batch_id)
        print(f"[qa] batch_id={batch_id} ready_files={len(files)}")
        if not files:
            raise HTTPException(status_code=400, detail="No ready files (retrieval_file_id) found for this batch")

        retrieval_ids = [f["retrieval_file_id"] for f in files if f.get("retrieval_file_id")]
        sample_ids = retrieval_ids[:3]
        print(f"[qa] org={org_id} batch_id={batch_id} files={len(retrieval_ids)}")
        print(f"[qa] sample file_ids={sample_ids}")

        # vector store
        vs_id = get_org_vector_store_id(org_id)
        if not vs_id:
            raise HTTPException(status_code=502, detail="No vector store configured for org")

        openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        client = OpenAI()

        # ---- Assistants v2: threads + runs ----
        # create thread with user message and attached batch files
        attachments = [{"file_id": rid, "tools": [{"type": "file_search"}]} for rid in retrieval_ids]
        thread = client.beta.threads.create(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question},
                    ],
                    "attachments": attachments,
                }
            ],
            metadata={
                "org_id": str(org_id),
                "batch_token": batch_token
            }
        )

        # create run with file_search, targeting the vector store id
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            model=openai_model,
            tools=[{"type": "file_search"}],
            tool_resources={
                "file_search": {
                    "vector_store_ids": [vs_id]
                }
            }
        )

        # poll until complete (timeout ~60s)
        started = time.time()
        while True:
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run.status in ("completed", "failed", "cancelled", "expired"):
                break
            if time.time() - started > 60:
                raise HTTPException(status_code=504, detail="Run timeout")
            time.sleep(0.8)

        if run.status != "completed":
            raise HTTPException(status_code=500, detail=f"Run failed with status={run.status}")

        # fetch messages and find the newest assistant answer
        msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=10)
        answer_text = ""
        primary_msg = None
        for m in msgs.data:
            if m.role == "assistant":
                primary_msg = m
                # Extract text: m.content is a list of blocks (text, citations, etc.)
                for blk in m.content:
                    if getattr(blk, "type", None) == "output_text" or getattr(blk, "type", None) == "text":
                        # SDK types may differ; try both .text.value and .text for safety
                        txt = ""
                        try:
                            txt = getattr(blk, "text", None)
                            if hasattr(txt, "value"):
                                txt = txt.value
                            elif isinstance(txt, dict) and "value" in txt:
                                txt = txt["value"]
                        except Exception:
                            txt = None
                        if isinstance(txt, str) and txt.strip():
                            answer_text = txt.strip()
                            break
                if answer_text:
                    break

        if not answer_text:
            answer_text = "(no answer text returned)"

        # citations: walk annotations on text blocks to collect file_ids
        cited_file_ids = set()
        if primary_msg and hasattr(primary_msg, "content"):
            for blk in primary_msg.content:
                # in many SDKs blk.type == "text" and blk.text.annotations exists
                try:
                    t = getattr(blk, "text", None)
                    annotations = []
                    if hasattr(t, "annotations"):
                        annotations = t.annotations or []
                    elif isinstance(t, dict) and "annotations" in t:
                        annotations = t["annotations"] or []
                    for ann in annotations:
                        # try common fields
                        fid = getattr(ann, "file_id", None) or (ann.get("file_id") if isinstance(ann, dict) else None)
                        if fid:
                            cited_file_ids.add(fid)
                except Exception:
                    pass

        # map citations to DB rows; fallback = first 5 searched files
        id_map = {f["retrieval_file_id"]: f for f in files}
        sources = []
        for fid in cited_file_ids:
            row = id_map.get(fid)
            if row:
                sources.append({
                    "file_id": row["id"],
                    "retrieval_file_id": fid,
                    "filename": row["filename"],
                    "score": None
                })
        if not sources:
            for rid in retrieval_ids[:5]:
                row = id_map.get(rid)
                if row:
                    sources.append({
                        "file_id": row["id"],
                        "retrieval_file_id": rid,
                        "filename": row["filename"],
                        "score": None
                    })

        # usage (best-effort; Assistants v2 usage may be None in some SDKs)
        usage = {}
        try:
            if getattr(run, "usage", None):
                usage = {
                    "input_tokens": getattr(run.usage, "input_tokens", None),
                    "output_tokens": getattr(run.usage, "output_tokens", None),
                    "total_tokens": getattr(run.usage, "total_tokens", None),
                }
        except Exception:
            usage = {}

        # insert into offer_qa_logs
        answer_summary = (answer_text or "")[:240]
        log_id = None
        try:
            conn = get_db_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.offer_qa_logs
                          (batch_id, org_id, asked_by_user_id, question, answer_summary, sources)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (batch_id, org_id, asked_by_user_id, question, answer_summary, json.dumps(sources))
                    )
                    row = cur.fetchone()
                    if row:
                        log_id = row[0]
        except Exception as e:
            print("[/api/qa/ask] failed to log QA:", repr(e))
            traceback.print_exc()

        print(f"[qa] OK org={org_id} batch_id={batch_id} attachments={len(retrieval_ids)} answer_len={len(answer_text)} sources={len(sources)}")

        return AskResponse(
            answer=answer_text,
            sources=sources,        # list of dicts is fine; Pydantic will coerce
            log_id=log_id,
            batch_token=batch_token,
            usage=usage or None
        )

    except HTTPException:
        raise
    except Exception as e:
        print("[/api/qa/ask] fatal error:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Q&A failed")
