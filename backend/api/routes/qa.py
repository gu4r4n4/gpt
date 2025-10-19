import os, json, time, traceback
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import psycopg2
from openai import OpenAI
from app.services.vectorstores import get_tc_vs, get_offer_vs

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
    product_line: str = Field(..., pattern="^[A-Za-z]+$", example="HEALTH")
    asked_by_user_id: int = Field(..., example=1)
    question: str = Field(..., example="What's the annual premium and deductible?")
    debug: Optional[int] = 0

class AskResponse(BaseModel):
    answer: str = Field(..., example="The annual premium is €1,200 and the deductible is €500.")
    sources: List[QASource]
    log_id: Optional[int] = Field(None, example=42)
    batch_token: str = Field(..., example="bt_manual_test_001")
    usage: Optional[Dict[str, Any]] = Field(None, example={"input_tokens": 150, "output_tokens": 75})
    debug: Optional[Dict[str, Any]] = None

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

def _insert_qa_log(conn, batch_id, org_id, user_id, question, answer_summary, sources_json):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.offer_qa_logs (batch_id, org_id, asked_by_user_id, question, answer_summary, sources)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                batch_id, org_id, user_id, question,
                (answer_summary or "")[:240],
                json.dumps(sources_json or [])
            ))
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
    except Exception as e:
        print("[qa] failed to insert qa log:", repr(e))
        traceback.print_exc()
        return None



@router.post("/ask", response_model=AskResponse, tags=["qa"], summary="Ask a question scoped to a batch")
def ask(payload: AskRequest = Body(...), debug: int = Query(0)):
    """
    Input:
    {
      "org_id": 1,
      "batch_token": "bt_manual_test_001",
      "product_line": "HEALTH",
      "asked_by_user_id": 1,
      "question": "What's the annual premium and deductible?",
      "debug": 0
    }
    """

    try:
        org_id = payload.org_id
        batch_token = payload.batch_token.strip()
        product_line = payload.product_line.strip()
        asked_by_user_id = payload.asked_by_user_id
        question = payload.question.strip()
        debug_flag = payload.debug or debug

        if not org_id or not batch_token or not product_line or not asked_by_user_id or not question:
            raise HTTPException(status_code=400, detail="Missing required fields: org_id, batch_token, product_line, asked_by_user_id, question")

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

        # Get both vector stores
        conn = get_db_connection()
        tc_vs = get_tc_vs(conn, org_id, product_line)
        offer_vs = get_offer_vs(conn, org_id, batch_token)
        
        if not tc_vs:
            raise HTTPException(404, detail=f"No T&C vector store for {product_line}")
        if not offer_vs:
            raise HTTPException(404, detail=f"No offer vector store for batch_token={batch_token}")
        
        vector_store_ids = [tc_vs, offer_vs]

        openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        client = OpenAI()

        # ---- Assistants v2: threads + runs ----
        # Create thread with system and user messages
        thread = client.beta.threads.create(messages=[
            {"role": "system", "content": "You are a helpful assistant that answers questions about insurance offers and terms & conditions. Use the provided documents to give accurate, detailed answers."},
            {"role": "user", "content": question}
        ])

        # Create run with file_search tool and both vector stores
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=os.getenv("ASSISTANT_ID_TOP3"),
            tool_resources={
                "file_search": { "vector_store_ids": vector_store_ids }
            }
        )

        # POLLING: make the polling block show the real failure details and time out with 504
        start = time.time()
        while True:
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run.status in ("completed", "failed", "cancelled", "expired"):
                break
            if time.time() - start > 60:
                raise HTTPException(status_code=504, detail="Q&A timed out after 60s")
            time.sleep(0.8)

        if run.status != "completed":
            # Try to surface model/tooling errors
            last_error = getattr(run, "last_error", None)
            msg = f"Run status={run.status}"
            if last_error:
                msg += f"; code={getattr(last_error, 'code', None)} message={getattr(last_error, 'message', None)}"
            print("[/api/qa/ask] run failed:", msg)
            # Still log the question with error summary
            conn = get_db_connection()
            _log_id = _insert_qa_log(conn, batch_id, org_id, asked_by_user_id, question, f"(run failed) {msg}", None)
            raise HTTPException(status_code=500, detail=msg)

        # MESSAGE EXTRACTION: fetch assistant messages and extract answer text defensively
        msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=10)
        answer_text = ""
        for m in msgs.data:
            if m.role == "assistant":
                # New SDK returns content as list of blocks; find a text block
                try:
                    parts = getattr(m, "content", []) or []
                    for p in parts:
                        if isinstance(p, dict):
                            if p.get("type") == "output_text" and "text" in p:
                                answer_text = p["text"]
                                break
                            if p.get("type") == "text" and "text" in p:
                                answer_text = p["text"]
                                break
                        else:
                            # Some SDK versions may return objects; fallback
                            t = getattr(p, "text", None)
                            if t:
                                answer_text = t
                                break
                    if answer_text:
                        break
                except Exception:
                    traceback.print_exc()
        if not answer_text:
            answer_text = "(no answer text returned)"

        # CITATIONS: walk messages to collect `file_id`s from annotations/citations if present
        cited_file_ids = set()
        try:
            for m in msgs.data:
                if m.role == "assistant":
                    parts = getattr(m, "content", []) or []
                    for p in parts:
                        if isinstance(p, dict) and p.get("type") == "text":
                            annotations = p.get("text", {}).get("annotations", [])
                            for ann in annotations:
                                if isinstance(ann, dict) and "file_id" in ann:
                                    cited_file_ids.add(ann["file_id"])
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

        # USAGE: If usage is not available from run, set usage = None (don't crash)
        usage = None
        try:
            if getattr(run, "usage", None):
                usage = {
                    "input_tokens": getattr(run.usage, "input_tokens", None),
                    "output_tokens": getattr(run.usage, "output_tokens", None),
                    "total_tokens": getattr(run.usage, "total_tokens", None),
                }
        except Exception:
            usage = None

        # ALWAYS LOG to `public.offer_qa_logs` even on success
        conn = get_db_connection()
        log_id = _insert_qa_log(conn, batch_id, org_id, asked_by_user_id, question, answer_text, sources)

        # RETURN: build AskResponse. If debug=1, include thread/run ids and first message id
        debug_payload = None
        if debug_flag:
            debug_payload = {
                "thread_id": thread.id,
                "run_id": run.id,
                "run_status": run.status,
                "tc_vector_store": tc_vs,
                "offer_vector_store": offer_vs
            }

        print(f"[qa] OK org={org_id} batch_id={batch_id} attachments={len(retrieval_ids)} answer_len={len(answer_text)} sources={len(sources)}")

        return AskResponse(
            answer=answer_text,
            sources=sources,
            log_id=log_id,
            batch_token=batch_token,
            usage=usage,
            debug=debug_payload
        )

    except HTTPException:
        raise
    except Exception as e:
        print("[/api/qa/ask] fatal error:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Q&A failed")
