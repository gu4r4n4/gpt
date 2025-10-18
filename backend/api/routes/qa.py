import os
import json
import traceback
from typing import List, Dict, Any, Optional

import psycopg2
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import JSONResponse
from openai import OpenAI

router = APIRouter(prefix="/api/qa", tags=["qa"])

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")  # keep aligned with healthz
client = OpenAI(api_key=OPENAI_API_KEY)

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL")
    return psycopg2.connect(DATABASE_URL)

def resolve_batch(org_id: int, batch_token: str) -> Dict[str, Any]:
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

def insert_qa_log(batch_id: int, org_id: int, asked_by_user_id: int, question: str,
                  answer_summary: str, sources: List[Dict[str, Any]]) -> int:
    sql = """
        INSERT INTO public.offer_qa_logs (batch_id, org_id, asked_by_user_id, question, answer_summary, sources)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (batch_id, org_id, asked_by_user_id, question, answer_summary, json.dumps(sources)))
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


@router.post("/ask")
def ask(
    payload: Dict[str, Any] = Body(...)
):
    """
    POST /api/qa/ask
    {
      "org_id": 1,
      "batch_token": "bt_manual_test_001",
      "asked_by_user_id": 1,
      "question": "What's the annual premium and deductible?"
    }
    """
    try:
        org_id = int(payload.get("org_id", 0))
        batch_token = (payload.get("batch_token") or "").strip()
        asked_by_user_id = int(payload.get("asked_by_user_id", 0))
        question = (payload.get("question") or "").strip()

        if not org_id or not batch_token or not asked_by_user_id or not question:
            raise HTTPException(status_code=400, detail="org_id, batch_token, asked_by_user_id and question are required")

        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="Missing OPENAI_API_KEY")

        # Resolve batch
        batch = resolve_batch(org_id, batch_token)
        batch_id = batch["id"]

        # Get vector store ID
        vs_id = get_org_vector_store_id(org_id)
        if not vs_id:
            raise HTTPException(status_code=502, detail="No vector store configured for org")

        # Get files scoped to this batch
        files = get_batch_retrieval_files(batch_id)
        if not files:
            print(f"[qa] no ready files for batch_id={batch_id}, token={batch_token}")
            raise HTTPException(status_code=400, detail="No ready files (retrieval_file_id) found for this batch")

        # Basic envelope log
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        retrieval_ids = [f["retrieval_file_id"] for f in files]
        print(f"[qa] org={org_id} batch_id={batch_id} files={len(files)} model={openai_model} vs={vs_id}")
        print(f"[qa] building responses.create with {len(retrieval_ids)} file_ids; sample={retrieval_ids[:3]}")

        # Build the Responses API call (file_search tool, restricted to this batch's files)
        resp = client.responses.create(
            model=openai_model,
            input=question,
            tools=[{"type": "file_search"}],
            tool_resources={
                "file_search": {
                    "vector_store_ids": [vs_id],
                    "filters": {"file_ids": retrieval_ids},
                }
            },
        )

        # Robustly extract answer text and citations from the Responses API result
        answer_text = None
        try:
            answer_text = getattr(resp, "output_text", None)
            if not answer_text:
                # Fallback: traverse top-level messages if available
                msg = (getattr(resp, "output", None) or
                       getattr(resp, "message", None) or
                       None)
                # If SDK shape differs, fall back to stringifying
                answer_text = str(resp) if not answer_text else answer_text
        except Exception as e:
            print("[qa] answer extraction error:", repr(e))
            answer_text = str(resp)

        if not answer_text:
            answer_text = "(no answer text returned)"

        # Source extraction (best-effort)
        sources = []
        # Try to read any citations/cited_file_ids if available in resp
        cited = set()
        try:
            # Many SDKs expose tool outputs or annotations. Best-effort scan:
            # Convert to dict and look for "file_ids" arrays
            import json
            resp_dict = json.loads(resp.model_dump_json()) if hasattr(resp, "model_dump_json") else json.loads(str(resp))
            def walk(obj):
                if isinstance(obj, dict):
                    for k,v in obj.items():
                        if k in ("file_ids", "cited_file_ids") and isinstance(v, list):
                            for fid in v:
                                if isinstance(fid, str):
                                    cited.add(fid)
                        walk(v)
                elif isinstance(obj, list):
                    for it in obj:
                        walk(it)
            walk(resp_dict)
        except Exception as e:
            print("[qa] citation parse warning:", repr(e))

        # Build file map
        by_retrieval = {f["retrieval_file_id"]: f for f in files}
        if cited:
            for rid in cited:
                if rid in by_retrieval:
                    f = by_retrieval[rid]
                    sources.append({
                        "file_id": f["id"],
                        "retrieval_file_id": rid,
                        "filename": f["filename"],
                    })

        # As fallback, if no citations came back, include the searched files (capped)
        if not sources:
            for f in files[:5]:
                sources.append({
                    "file_id": f["id"],
                    "retrieval_file_id": f["retrieval_file_id"],
                    "filename": f["filename"],
                })

        # Usage parsing (defensive)
        usage = {}
        try:
            # Some SDKs expose usage as resp.usage or under meta
            u = getattr(resp, "usage", None)
            if u and isinstance(u, dict):
                usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
        except Exception as e:
            print("[qa] usage parse warning:", repr(e))

        # Insert into public.offer_qa_logs AFTER we have a non-empty answer_text
        summary = answer_text[:240].strip()  # first 240 chars
        log_id = insert_qa_log(batch_id, org_id, asked_by_user_id, question, summary, sources)

        print(f"[qa] OK org={org_id} batch_id={batch_id} answer_len={len(answer_text)} sources={len(sources)}")

        return JSONResponse({
            "answer": answer_text,
            "sources": sources,
            "log_id": log_id,
            "batch_token": batch_token,
            "usage": usage
        })

    except HTTPException:
        raise
    except Exception as e:
        print("[/api/qa/ask] fatal error:", repr(e))
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"detail": "QA internal error"})
