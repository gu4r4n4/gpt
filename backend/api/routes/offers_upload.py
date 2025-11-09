# backend/api/routes/offers_upload.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Dict, Any, Optional
import os
import uuid
from psycopg2.extras import RealDictCursor
from app.main import get_db_connection, _safe_filename

router = APIRouter(prefix="/api/offers", tags=["offers"])

def _storage_root() -> str:
    return os.getenv("STORAGE_ROOT", "/var/app/uploads")

@router.post("/upload")
async def upload_and_chunk(pdf: UploadFile = File(...)) -> Dict[str, Any]:
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    # 1) Persist the file to disk
    safe = _safe_filename(pdf.filename or "uploaded.pdf")
    batch_token = f"bt_{uuid.uuid4().hex[:24]}"
    batch_dir = os.path.join(_storage_root(), "offers", batch_token)
    os.makedirs(batch_dir, exist_ok=True)
    path = os.path.join(batch_dir, safe)

    data = await pdf.read()
    with open(path, "wb") as wf:
        wf.write(data)

    print("[offers_upload] saved:", path)

    # 2) Insert offer_files row and get its id (use RealDictCursor!)
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
    finally:
        conn.close()

    print("[offers_upload] offer_files.id:", file_id)

    # 3) Create chunks (call your chunker; adjust the SQL/procedure name to yours)
    # If you have a DB function/procedure that (re)chunks a file, call it here.
    chunks_created: Optional[int] = None
    text_length: Optional[int] = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Example 1: if you have a function that builds chunks
            # cur.execute("select * from reembed_file(%s)", (file_id,))

            # Example 2: if chunks are created elsewhere, skip this call.

            # Return counts regardless so you SEE something in the logs
            cur.execute("""
                select count(*) as chunks from public.offer_chunks where file_id = %s
            """, (file_id,))
            c = cur.fetchone()
            chunks_created = (c or {}).get("chunks", 0)

            # If you store raw text length per file, you can fetch it; otherwise set None
            text_length = None

            conn.commit()
    finally:
        conn.close()

    print("[offers_upload] chunks:", chunks_created)

    return {
        "ok": True,
        "file_id": file_id,
        "storage_path": path,
        "chunks": chunks_created,
        "text_length": text_length,
    }
