"""
File upload endpoint for offer documents with vector store integration.
Makes sure every upload ends with chunks & embeddings in public.offer_chunks.
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor
from app.services.supabase_storage import put_pdf_and_get_path
from backend.scripts.reembed_file import reembed_file
from backend.api.routes.util import get_db_connection

router = APIRouter(prefix="/api/offers", tags=["offers"])

print("[offers_upload] router mounted at /api/offers")

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

    print(f"[offers_upload] received: {pdf.filename}")
    durable_path = put_pdf_and_get_path(pdf)
    print(f"[offers_upload] stored at: {durable_path}")

    # 2) Insert and keep connection open for re-embed
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            print("[offers_upload] inserting:", pdf.filename, durable_path)
            cur.execute("""
                INSERT INTO public.offer_files (filename, storage_path)
                VALUES (%s, %s)
                RETURNING id
            """, (pdf.filename, durable_path))
            row = cur.fetchone()
            print("[offers_upload] fetchone type:", type(row), "value:", row)
            if not row:
                print("[offers_upload] ERROR: no row")
                raise HTTPException(500, "Failed to create file record")
            file_id = row["id"]
        conn.commit()
        print(f"[offers_upload] offer_files.id={file_id}")
        result = reembed_file(file_id, conn, chunk_size=1500, overlap=300, dry_run=False)
        print("[offers_upload] reembed result:", result)
        print(f"[offers_upload] reembed OK: file_id={file_id} chunks={result.get('chunks_created')} len={result.get('text_length')}")
    finally:
        conn.close()

    return JSONResponse({
        "ok": True,
        "file_id": file_id,
        "storage_path": durable_path,
        "chunks": result["chunks_created"],
        "text_length": result.get("text_length"),
    })
