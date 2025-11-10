# backend/api/routes/offers_upload.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Form
from typing import Dict, Any, Optional
import os, uuid, traceback
from datetime import datetime, timedelta, timezone
from psycopg2.extras import RealDictCursor
from psycopg2 import Error as PGError
from backend.api.routes.util import get_db_connection, safe_filename

try:
    from backend.api.routes.qa import _reembed_file
except Exception:
    from backend.scripts.reembed_file import reembed_file as _reembed_file

router = APIRouter(prefix="/api/offers", tags=["offers"])

def _storage_root() -> str:
    return os.getenv("STORAGE_ROOT", "/var/app/uploads")

DEFAULT_ORG_ID = int(os.getenv("DEFAULT_ORG_ID", "1"))
DEFAULT_USER_ID = int(os.getenv("DEFAULT_USER_ID", "1"))
# batch validity window (must set because offer_batches.expires_at is NOT NULL)
BATCH_TTL_DAYS = int(os.getenv("BATCH_TTL_DAYS", "180"))

def _ensure_app_user_exists(conn, user_id: int) -> None:
    # FK guard for offer_batches.created_by_user_id
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT 1 FROM public.app_users WHERE id=%s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(
                status_code=400,
                detail=f"app_users.id={user_id} does not exist (needed for offer_batches.created_by_user_id). "
                       f"Create it or set DEFAULT_USER_ID to a valid user."
            )

def _resolve_or_create_batch(conn, org_id: int, batch_token: Optional[str]) -> Dict[str, Any]:
    """
    If batch_token provided -> return its id.
    Else create new offer_batches row (requires DEFAULT_USER_ID to exist) and return (id, token).
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if batch_token:
            cur.execute("SELECT id, token FROM public.offer_batches WHERE token=%s AND org_id=%s",
                        (batch_token, org_id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=404,
                                    detail=f"Batch not found (org_id={org_id}, batch_token={batch_token})")
            return {"id": r["id"], "token": r["token"]}

    # Create new batch
    _ensure_app_user_exists(conn, DEFAULT_USER_ID)
    token = f"bt_{uuid.uuid4().hex[:24]}"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=BATCH_TTL_DAYS)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # token is UNIQUE, status defaults to 'active'
        cur.execute("""
            INSERT INTO public.offer_batches (org_id, created_by_user_id, token, expires_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id, token
        """, (org_id, DEFAULT_USER_ID, token, expires_at))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create offer_batches row")
        conn.commit()
        return {"id": row["id"], "token": row["token"]}

@router.post("/upload")
async def upload_and_chunk(
    pdf: UploadFile = File(...),

    # Accept params via query OR multipart form (frontend-friendly)
    org_id_q: Optional[int] = Query(None, description="Existing org id (query)"),
    batch_token_q: Optional[str] = Query(None, description="Existing batch token (query)"),
    org_id_f: Optional[int] = Form(None),
    batch_token_f: Optional[str] = Form(None),
) -> Dict[str, Any]:
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    # Resolve params: query has priority, then form, fallback to env for org_id
    org_id = org_id_q if org_id_q is not None else (org_id_f if org_id_f is not None else DEFAULT_ORG_ID)
    batch_token_in = batch_token_q if batch_token_q is not None else batch_token_f  # may be None (auto-create)

    # 1) Persist file under batch_token folder (use provided token if any, else temp placeholder; final path is fine)
    safe = safe_filename(pdf.filename or "uploaded.pdf")
    folder_token = batch_token_in or f"bt_{uuid.uuid4().hex[:24]}"
    batch_dir = os.path.join(_storage_root(), "offers", folder_token)
    os.makedirs(batch_dir, exist_ok=True)
    path = os.path.join(batch_dir, safe)
    data = await pdf.read()
    with open(path, "wb") as wf:
        wf.write(data)
    print(f"[offers_upload] saved: {path}")

    conn = get_db_connection()
    file_id = None
    try:
        # 2) Resolve existing batch OR create a new one (handles FK + expires_at)
        batch = _resolve_or_create_batch(conn, org_id, batch_token_in)
        batch_id, batch_token = batch["id"], batch["token"]
        print(f"[offers_upload] using batch_id={batch_id} token={batch_token}")

        # 3) Insert offer_files linked to batch/org
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO public.offer_files
                (filename, mime_type, size_bytes, storage_path, is_permanent, org_id, batch_id)
                VALUES (%s, %s, %s, %s, false, %s, %s)
                RETURNING id
            """, (safe, pdf.content_type or "application/pdf", len(data), path, org_id, batch_id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=500, detail="Failed to insert offer_files row")
            file_id = r["id"]
            conn.commit()
        print(f"[offers_upload] offer_files.id={file_id}")

        # 4) Re-embed (create chunks); if it errors, surface a warning but do not 500
        reembed_warning = None
        try:
            print(f"[offers_upload] reembed start file_id={file_id}")
            _ = _reembed_file(file_id, conn)
            print(f"[offers_upload] reembed done file_id={file_id}")
        except Exception as e:
            reembed_warning = f"_reembed_file: {e}"
            print("[offers_upload] REEMBED TRACEBACK:\n" + traceback.format_exc())

        # 5) Report chunk count
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS chunks FROM public.offer_chunks WHERE file_id=%s", (file_id,))
            c = cur.fetchone() or {}
            chunks_created = int(c.get("chunks", 0))
            conn.commit()

        resp: Dict[str, Any] = {
            "ok": True,
            "file_id": file_id,
            "org_id": org_id,
            "batch_token": batch_token,
            "storage_path": path,
            "chunks": chunks_created,
            "text_length": None,
        }
        if reembed_warning:
            resp["reembed_warning"] = reembed_warning
        return resp

    except HTTPException:
        raise
    except Exception as e:
        print("[offers_upload] ERROR TRACEBACK:\n" + traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
    finally:
        conn.close()
