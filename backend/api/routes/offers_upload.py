# backend/api/routes/offers_upload.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Dict, Any, Optional
import os
import uuid
from psycopg2.extras import RealDictCursor
from backend.api.routes.util import get_db_connection, safe_filename
from backend.api.routes.qa import _reembed_file  # local chunker (no embeddings)

router = APIRouter(prefix="/api/offers", tags=["offers"])

def _storage_root() -> str:
    return os.getenv("STORAGE_ROOT", "/var/app/uploads")

@router.post("/upload")
async def upload_and_chunk(pdf: UploadFile = File(...)) -> Dict[str, Any]:
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    # 1) Persist the file to disk
    safe = safe_filename(pdf.filename or "uploaded.pdf")
    batch_token = f"bt_{uuid.uuid4().hex[:24]}"
    batch_dir = os.path.join(_storage_root(), "offers", batch_token)
    os.makedirs(batch_dir, exist_ok=True)
    path = os.path.join(batch_dir, safe)

    data = await pdf.read()
    with open(path, "wb") as wf:
        wf.write(data)

    print("[offers_upload] saved:", path)

    # 2) Insert offer_files row and get its id
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO public.offer_files
                (filename, mime_type, size_bytes, storage_path, is_permanent)
                VALUES (%s, %s, %s, %s, false)
                RETURNING id
            """, (safe, pdf.content_type or "application/pdf", len(data), path))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Failed to insert offer_files row")
            file_id = row["id"]
            conn.commit()

        # 3) Create chunks immediately (text-only chunks, fast & local)
        #    _reembed_file uses the same DB; it replaces chunks for this file.
        _ = _reembed_file(file_id, conn)

        # 4) Return counts
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("select count(*) as chunks from public.offer_chunks where file_id = %s", (file_id,))
            c = cur.fetchone()
            chunks_created = (c or {}).get("chunks", 0)
            text_length = None
            conn.commit()
    finally:
        conn.close()

    print("[offers_upload] offer_files.id:", file_id)

    print(f"[offers_upload] chunks: {chunks_created}")

    return {
        "ok": True,
        "file_id": file_id,
        "storage_path": path,
        "chunks": chunks_created,
        "text_length": text_length,
    }
