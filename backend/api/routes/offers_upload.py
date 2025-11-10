# backend/api/routes/offers_upload.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Dict, Any, Optional
import os
import uuid
from psycopg2.extras import RealDictCursor
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

@router.post("/upload")
async def upload_and_chunk(pdf: UploadFile = File(...)) -> Dict[str, Any]:
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    # 1️⃣ Save file to disk
    safe = safe_filename(pdf.filename or "uploaded.pdf")
    batch_token = f"bt_{uuid.uuid4().hex[:24]}"
    batch_dir = os.path.join(_storage_root(), "offers", batch_token)
    os.makedirs(batch_dir, exist_ok=True)
    path = os.path.join(batch_dir, safe)

    data = await pdf.read()
    with open(path, "wb") as wf:
        wf.write(data)
    print("[offers_upload] saved:", path)

    # 2️⃣ Create or fetch batch_id
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO public.offer_batches (org_id, created_by_user_id, token, status)
                VALUES (%s, %s, %s, 'active')
                ON CONFLICT DO NOTHING
                RETURNING id
            """, (DEFAULT_ORG_ID, DEFAULT_USER_ID, batch_token))
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT id FROM public.offer_batches WHERE org_id=%s AND token=%s",
                            (DEFAULT_ORG_ID, batch_token))
                row = cur.fetchone()
            batch_id = row["id"]
            print(f"[offers_upload] linked to offer_batches.id={batch_id}")

            # 3️⃣ Insert offer_files row linked to batch and org
            cur.execute("""
                INSERT INTO public.offer_files
                (filename, mime_type, size_bytes, storage_path, is_permanent, org_id, batch_id)
                VALUES (%s, %s, %s, %s, false, %s, %s)
                RETURNING id
            """, (safe, pdf.content_type or "application/pdf", len(data), path, DEFAULT_ORG_ID, batch_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Failed to insert offer_files row")
            file_id = row["id"]
            conn.commit()

        # 4️⃣ Create chunks immediately
        print(f"[offers_upload] chunking file_id={file_id}")
        _ = _reembed_file(file_id, conn)

        # 5️⃣ Return counts
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS chunks FROM public.offer_chunks WHERE file_id=%s", (file_id,))
            c = cur.fetchone()
            chunks_created = (c or {}).get("chunks", 0)
            conn.commit()

    finally:
        conn.close()

    print(f"[offers_upload] offer_files.id={file_id}, chunks={chunks_created}")

    return {
        "ok": True,
        "file_id": file_id,
        "batch_token": batch_token,
        "storage_path": path,
        "chunks": chunks_created,
    }
