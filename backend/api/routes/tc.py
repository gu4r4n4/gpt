# backend/api/routes/tc.py
"""
T&C (Terms & Conditions) and Law document management
Handles upload, chunking, and retrieval of T&C and legal documents
"""

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Depends, Query
from typing import Optional, List, Dict, Any
import os
import uuid
import json
from pypdf import PdfReader
from psycopg2.extras import RealDictCursor
from datetime import datetime

router = APIRouter()

# Storage configuration
def _storage_root() -> str:
    return os.getenv("STORAGE_ROOT", "/tmp")

def safe_filename(fn: str) -> str:
    """Sanitize filename to avoid path traversal."""
    import re
    base = os.path.basename(fn or "file.pdf")
    safe = re.sub(r'[^\w\s\-\.]', '_', base)
    return safe[:200]

# Database connection (reuse pattern from qa.py)
def get_db():
    import psycopg2
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

# ------------------------------
# Text Extraction (reused from qa.py)
# ------------------------------

def _extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from PDF file."""
    try:
        reader = PdfReader(pdf_path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as e:
        raise Exception(f"Failed to extract text from PDF: {e}")

def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[dict]:
    """Split text into overlapping chunks."""
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    chunk_index = 0

    while start < len(text):
        end = start + chunk_size

        # Try to break at paragraph boundaries
        if end < len(text):
            paragraph_break = text.rfind("\n\n", start, end)
            if paragraph_break > start + chunk_size // 2:
                end = paragraph_break + 2
            else:
                # Try sentence boundaries
                sentence_break = max(
                    text.rfind(". ", start, end),
                    text.rfind("! ", start, end),
                    text.rfind("? ", start, end)
                )
                if sentence_break > start + chunk_size // 2:
                    end = sentence_break + 2

        chunk_text = text[start:end].strip()

        if chunk_text:
            chunks.append({
                "text": chunk_text,
                "metadata": {
                    "chunk_index": chunk_index,
                    "start_pos": start,
                    "end_pos": end,
                    "length": len(chunk_text)
                }
            })
            chunk_index += 1

        start = end - overlap if end < len(text) else len(text)

    return chunks

# ------------------------------
# T&C Upload Endpoint
# ------------------------------

@router.post("/upload")
async def upload_tc(
    file: UploadFile = File(...),
    insurer_name: str = Form(...),
    product_line: str = Form(...),
    effective_from: Optional[str] = Form(None),
    expires_at: Optional[str] = Form(None),
    version_label: Optional[str] = Form(None),
    org_id: Optional[int] = Form(None),
    conn = Depends(get_db)
) -> Dict[str, Any]:
    """
    Upload T&C document for an insurer.
    Creates chunks and stores in tc_files + tc_chunks tables.
    """
    print(f"[tc] upload start: insurer={insurer_name}, product={product_line}, file={file.filename}")

    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=415, detail="Only PDF files are supported")

    # Get org_id from form or environment
    if org_id is None:
        org_id = int(os.getenv("DEFAULT_ORG_ID", "1"))

    # 1) Save file to storage
    safe = safe_filename(file.filename)
    folder_name = f"tc_{insurer_name.lower()}_{product_line.lower()}_{uuid.uuid4().hex[:8]}"
    tc_dir = os.path.join(_storage_root(), "tc", folder_name)
    os.makedirs(tc_dir, exist_ok=True)
    storage_path = os.path.join(tc_dir, safe)

    file_data = await file.read()
    with open(storage_path, "wb") as wf:
        wf.write(file_data)
    print(f"[tc] saved to: {storage_path}")

    # 2) Parse dates
    eff_date = None
    exp_date = None
    try:
        if effective_from:
            eff_date = datetime.fromisoformat(effective_from).date()
    except:
        pass
    try:
        if expires_at:
            exp_date = datetime.fromisoformat(expires_at).date()
    except:
        pass

    # 3) Insert tc_files record
    file_id = None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.tc_files
                (org_id, insurer_name, product_line, effective_from, expires_at, 
                 version_label, filename, storage_path, embeddings_ready)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)
                RETURNING id
            """, (org_id, insurer_name, product_line, eff_date, exp_date,
                  version_label, safe, storage_path))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Failed to insert tc_files record")
            file_id = row["id"]
            conn.commit()
        print(f"[tc] tc_files.id={file_id}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database insert failed: {str(e)}")

    # 4) Extract text and create chunks
    try:
        print(f"[tc] extracting text from {storage_path}")
        text = _extract_text_from_pdf(storage_path)

        if not text or len(text.strip()) < 10:
            raise Exception("Extracted text is empty or too short")

        print(f"[tc] extracted {len(text)} characters")

        print(f"[tc] chunking text")
        chunks = _chunk_text(text, chunk_size=1000, overlap=200)

        if not chunks:
            raise Exception("No chunks created from text")

        print(f"[tc] created {len(chunks)} chunks")

        # 5) Insert chunks
        with conn.cursor() as cur:
            for idx, chunk in enumerate(chunks):
                cur.execute("""
                    INSERT INTO public.tc_chunks
                    (file_id, chunk_index, text)
                    VALUES (%s, %s, %s)
                """, (file_id, idx, chunk["text"]))

        conn.commit()
        print(f"[tc] inserted {len(chunks)} chunks")

        # 6) Mark embeddings as ready
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE public.tc_files
                SET embeddings_ready = true
                WHERE id = %s
            """, (file_id,))
            conn.commit()

        return {
            "ok": True,
            "file_id": file_id,
            "insurer_name": insurer_name,
            "product_line": product_line,
            "filename": safe,
            "chunks": len(chunks),
            "text_length": len(text)
        }

    except Exception as e:
        # Clean up on failure
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM public.tc_files WHERE id = %s", (file_id,))
                conn.commit()
        except:
            pass
        raise HTTPException(status_code=500, detail=f"Chunking failed: {str(e)}")

# ------------------------------
# Law Upload Endpoint
# ------------------------------

@router.post("/laws/upload")
async def upload_law(
    file: UploadFile = File(...),
    law_name: str = Form(...),
    product_line: Optional[str] = Form(None),
    effective_from: Optional[str] = Form(None),
    org_id: Optional[int] = Form(None),
    conn = Depends(get_db)
) -> Dict[str, Any]:
    """
    Upload law document.
    Creates chunks and stores in law_files + law_chunks tables.
    """
    print(f"[law] upload start: law={law_name}, product={product_line}, file={file.filename}")

    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=415, detail="Only PDF files are supported")

    # Get org_id (None for laws means available to all)
    if org_id == 0:
        org_id = None

    # 1) Save file to storage
    safe = safe_filename(file.filename)
    folder_name = f"law_{law_name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
    law_dir = os.path.join(_storage_root(), "laws", folder_name)
    os.makedirs(law_dir, exist_ok=True)
    storage_path = os.path.join(law_dir, safe)

    file_data = await file.read()
    with open(storage_path, "wb") as wf:
        wf.write(file_data)
    print(f"[law] saved to: {storage_path}")

    # 2) Parse date
    eff_date = None
    try:
        if effective_from:
            eff_date = datetime.fromisoformat(effective_from).date()
    except:
        pass

    # 3) Insert law_files record
    file_id = None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.law_files
                (org_id, law_name, product_line, effective_from, filename, 
                 storage_path, embeddings_ready)
                VALUES (%s, %s, %s, %s, %s, %s, false)
                RETURNING id
            """, (org_id, law_name, product_line, eff_date, safe, storage_path))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Failed to insert law_files record")
            file_id = row["id"]
            conn.commit()
        print(f"[law] law_files.id={file_id}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database insert failed: {str(e)}")

    # 4) Extract text and create chunks
    try:
        print(f"[law] extracting text from {storage_path}")
        text = _extract_text_from_pdf(storage_path)

        if not text or len(text.strip()) < 10:
            raise Exception("Extracted text is empty or too short")

        print(f"[law] extracted {len(text)} characters")

        print(f"[law] chunking text")
        chunks = _chunk_text(text, chunk_size=1000, overlap=200)

        if not chunks:
            raise Exception("No chunks created from text")

        print(f"[law] created {len(chunks)} chunks")

        # 5) Insert chunks
        with conn.cursor() as cur:
            for idx, chunk in enumerate(chunks):
                cur.execute("""
                    INSERT INTO public.law_chunks
                    (file_id, chunk_index, text)
                    VALUES (%s, %s, %s)
                """, (file_id, idx, chunk["text"]))

        conn.commit()
        print(f"[law] inserted {len(chunks)} chunks")

        # 6) Mark embeddings as ready
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE public.law_files
                SET embeddings_ready = true
                WHERE id = %s
            """, (file_id,))
            conn.commit()

        return {
            "ok": True,
            "file_id": file_id,
            "law_name": law_name,
            "product_line": product_line or "ALL",
            "filename": safe,
            "chunks": len(chunks),
            "text_length": len(text)
        }

    except Exception as e:
        # Clean up on failure
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM public.law_files WHERE id = %s", (file_id,))
                conn.commit()
        except:
            pass
        raise HTTPException(status_code=500, detail=f"Chunking failed: {str(e)}")

# ------------------------------
# List T&C Files
# ------------------------------

@router.get("/list")
def list_tc_files(
    org_id: int = Query(...),
    product_line: Optional[str] = Query(None),
    conn = Depends(get_db)
) -> Dict[str, Any]:
    """List all T&C files for an organization."""
    try:
        with conn.cursor() as cur:
            if product_line:
                cur.execute("""
                    SELECT id, insurer_name, product_line, effective_from, 
                           expires_at, version_label, filename, embeddings_ready, created_at
                    FROM public.tc_files
                    WHERE org_id = %s AND product_line = %s
                    ORDER BY insurer_name, created_at DESC
                """, (org_id, product_line))
            else:
                cur.execute("""
                    SELECT id, insurer_name, product_line, effective_from, 
                           expires_at, version_label, filename, embeddings_ready, created_at
                    FROM public.tc_files
                    WHERE org_id = %s
                    ORDER BY insurer_name, created_at DESC
                """, (org_id,))
            
            files = cur.fetchall()
            
            return {
                "ok": True,
                "files": files,
                "count": len(files)
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")

# ------------------------------
# List Law Files
# ------------------------------

@router.get("/laws/list")
def list_law_files(
    product_line: Optional[str] = Query(None),
    conn = Depends(get_db)
) -> Dict[str, Any]:
    """List all law files."""
    try:
        with conn.cursor() as cur:
            if product_line:
                cur.execute("""
                    SELECT id, law_name, product_line, effective_from, 
                           filename, embeddings_ready, created_at
                    FROM public.law_files
                    WHERE product_line IS NULL OR product_line = %s
                    ORDER BY created_at DESC
                """, (product_line,))
            else:
                cur.execute("""
                    SELECT id, law_name, product_line, effective_from, 
                           filename, embeddings_ready, created_at
                    FROM public.law_files
                    ORDER BY created_at DESC
                """)
            
            files = cur.fetchall()
            
            return {
                "ok": True,
                "files": files,
                "count": len(files)
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")

# ------------------------------
# Delete T&C File
# ------------------------------

@router.delete("/{file_id}")
def delete_tc_file(file_id: int, conn = Depends(get_db)) -> Dict[str, Any]:
    """Delete T&C file and its chunks."""
    try:
        with conn.cursor() as cur:
            # Chunks will be deleted automatically via CASCADE
            cur.execute("DELETE FROM public.tc_files WHERE id = %s", (file_id,))
            deleted = cur.rowcount
            conn.commit()
            
            if deleted == 0:
                raise HTTPException(status_code=404, detail="File not found")
            
            return {"ok": True, "deleted_file_id": file_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")

# ------------------------------
# Delete Law File
# ------------------------------

@router.delete("/laws/{file_id}")
def delete_law_file(file_id: int, conn = Depends(get_db)) -> Dict[str, Any]:
    """Delete law file and its chunks."""
    try:
        with conn.cursor() as cur:
            # Chunks will be deleted automatically via CASCADE
            cur.execute("DELETE FROM public.law_files WHERE id = %s", (file_id,))
            deleted = cur.rowcount
            conn.commit()
            
            if deleted == 0:
                raise HTTPException(status_code=404, detail="File not found")
            
            return {"ok": True, "deleted_file_id": file_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")

