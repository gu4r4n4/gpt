import os, uuid, traceback
import psycopg2
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta, timezone

DATABASE_URL = os.getenv("DATABASE_URL")
BATCH_TTL_DAYS = int(os.getenv("BATCH_TTL_DAYS", "30"))

router = APIRouter(prefix="/api/batches", tags=["batches"])

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL)

def _gen_token() -> str:
    return f"bt_{uuid.uuid4().hex[:24]}"


@router.post("", summary="Create a new offer batch")
def create_batch(payload: dict = Body(...)):
    org_id = int(payload.get("org_id") or 0)
    user_id = int(payload.get("created_by_user_id") or 0)
    title = (payload.get("title") or "").strip() or None
    if not org_id or not user_id:
        raise HTTPException(status_code=400, detail="org_id and created_by_user_id are required")
    token = _gen_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=BATCH_TTL_DAYS)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.offer_batches (org_id, created_by_user_id, token, title, status, expires_at)
                    VALUES (%s, %s, %s, %s, 'active', %s)
                    RETURNING id, token, expires_at, status
                """, (org_id, user_id, token, title, expires_at))
                row = cur.fetchone()
                return {"batch_id": row[0], "batch_token": row[1], "expires_at": row[2].isoformat(), "status": row[3]}
    except Exception as e:
        print("[/api/batches] error:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"batches.create failed: {e}")


@router.get("/{token}")
def get_batch(token: str):
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, org_id, token, title, status, created_at, expires_at
                FROM public.offer_batches
                WHERE token = %s
                LIMIT 1
            """, (token,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Batch not found")
            batch_id = row[0]
            cur.execute("""
                SELECT id, filename, retrieval_file_id, embeddings_ready
                FROM public.offer_files
                WHERE batch_id = %s
                ORDER BY id DESC
            """, (batch_id,))
            files = [
                {"id": r[0], "filename": r[1], "retrieval_file_id": r[2], "embeddings_ready": bool(r[3])}
                for r in cur.fetchall()
            ]
            return JSONResponse({
                "batch": {
                    "id": row[0], "org_id": row[1], "token": row[2], "title": row[3],
                    "status": row[4], "created_at": row[5], "expires_at": row[6],
                },
                "files": files
            })
    except HTTPException:
        raise
    except Exception as e:
        print("[GET /api/batches/{token}] error:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

