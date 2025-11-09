"""
File upload endpoint for offer documents with vector store integration.
Makes sure every upload ends with chunks & embeddings in public.offer_chunks.
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from app.services.supabase_storage import put_pdf_and_get_path
from backend.scripts.reembed_file import reembed_file
from backend.api.routes.util import get_db_connection

router = APIRouter(prefix="/api/offers", tags=["offers"])

@router.post("/upload")
async def upload_offer(pdf: UploadFile = File(...)):
    """
    Upload a PDF offer, store in Supabase, and create chunks with embeddings.
    Flow:
    1) Store PDF in Supabase Storage (durable)
    2) Insert offer_files row (keep DB connection open)
    3) Re-embed immediately (writes chunks+embeddings into public.offer_chunks)
    """
    if not pdf.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    # 1) Durable storage
    durable_path = put_pdf_and_get_path(pdf)

    # 2) Insert and keep connection open for re-embed
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.offer_files (filename, storage_path)
                VALUES (%s, %s)
                RETURNING id
                """,
                (pdf.filename, durable_path),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Failed to create file record")
            file_id = row["id"]
        conn.commit()

        # 3) Re-embed immediately
        result = reembed_file(
            file_id,
            conn,
            chunk_size=1500,
            overlap=300,
            dry_run=False,
        )
    finally:
        conn.close()

    return JSONResponse({
        "ok": True,
        "file_id": file_id,
        "storage_path": durable_path,
        "chunks": result["chunks_created"],
        "text_length": result.get("text_length"),
    })
