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
    sql = "SELECT vector_store_id FROM public.org_vector_stores WHERE org_id = %s LIMIT 1"
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (org_id,))
        row = cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=500, detail="No vector store configured for org")
    return row[0]

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

def _extract_answer_and_sources(resp: Any, file_map: Dict[str, Dict[str, Any]]) -> (str, List[Dict[str, Any]]):
    """
    Best-effort extractor for Responses API:
    - Prefer resp.output_text for the answer.
    - Walk through any annotations for file citations (if present).
    - Map OpenAI file_id -> our file row (via file_map keyed by retrieval_file_id).
    """
    answer_text = ""
    sources: List[Dict[str, Any]] = []

    # answer
    try:
        # new SDKs provide .output_text
        answer_text = getattr(resp, "output_text", "") or ""
    except Exception:
        pass

    if not answer_text:
        # Fallback: try to mine text from the output array
        try:
            if hasattr(resp, "output") and isinstance(resp.output, list):
                chunks = []
                for item in resp.output:
                    # each item may have .content (list) with .text.value
                    content = getattr(item, "content", [])
                    for c in content:
                        text = getattr(getattr(c, "text", None), "value", "")
                        if text:
                            chunks.append(text)
                answer_text = "\n".join(chunks).strip()
        except Exception:
            pass

    # citations
    # Some SDKs expose annotations under content[].text.annotations or under output_annotations
    # We collect all file_ids we see and map them back to our DB rows.
    seen_oai_file_ids = set()
    try:
        # Try the common path content[].text.annotations
        if hasattr(resp, "output") and isinstance(resp.output, list):
            for item in resp.output:
                content = getattr(item, "content", [])
                for c in content:
                    txt = getattr(c, "text", None)
                    annotations = getattr(txt, "annotations", []) if txt else []
                    for ann in annotations:
                        # Expect a .file_citation or .file_path with .file_id
                        file_id = getattr(getattr(ann, "file_citation", None), "file_id", None) \
                                  or getattr(getattr(ann, "file_path", None), "file_id", None)
                        if file_id and file_id not in seen_oai_file_ids:
                            seen_oai_file_ids.add(file_id)
    except Exception:
        traceback.print_exc()

    # Build sources with our metadata
    for fid in seen_oai_file_ids:
        meta = file_map.get(fid)
        if meta:
            sources.append({
                "file_id": meta["id"],
                "retrieval_file_id": fid,
                "filename": meta["filename"],
            })
        else:
            sources.append({
                "file_id": None,
                "retrieval_file_id": fid,
                "filename": None,
            })

    return (answer_text.strip(), sources)

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

        # Get files scoped to this batch
        files = get_batch_retrieval_files(batch_id)
        if not files:
            print(f"[qa] no ready files for batch_id={batch_id}, token={batch_token}")
            raise HTTPException(status_code=400, detail="No ready files (retrieval_file_id) found for this batch")

        retrieval_ids = [f["retrieval_file_id"] for f in files]
        file_map = {f["retrieval_file_id"]: f for f in files}

        # Get vector store
        vs_id = get_org_vector_store_id(org_id)

        # Build the Responses API call (file_search tool, restricted to this batch's files)
        # NOTE: The exact SDK surface can vary across versions. This follows the current openai==2.2.0 client.
        resp = client.responses.create(
            model=DEFAULT_MODEL,
            input=question,
            tools=[{"type": "file_search"}],
            tool_resources={
                "file_search": {
                    "vector_store_ids": [vs_id],
                    # Restrict the search to this batch's files only:
                    "filters": {
                        "file_ids": retrieval_ids
                    }
                }
            },
            metadata={
                "org_id": str(org_id),
                "batch_token": batch_token,
                "asked_by_user_id": str(asked_by_user_id),
                "scope": "batch"
            }
        )

        answer_text, sources = _extract_answer_and_sources(resp, file_map)
        if not answer_text:
            answer_text = "I couldn't generate an answer."

        # Persist QA log
        summary = answer_text[:1000]  # avoid huge payloads
        log_id = insert_qa_log(batch_id, org_id, asked_by_user_id, question, summary, sources)

        # Try to pull usage if present
        usage = {}
        try:
            u = getattr(resp, "usage", None)
            if u:
                # various SDKs expose dict-like usage
                usage = dict(u) if isinstance(u, dict) else {k: getattr(u, k) for k in dir(u) if not k.startswith("_")}
        except Exception:
            pass

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
        print("[/api/qa/ask] error:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Q&A error: {str(e)}")
