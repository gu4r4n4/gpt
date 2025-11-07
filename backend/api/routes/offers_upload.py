"""
File upload endpoint for offer documents with vector store integration.
"""
import hashlib
import os
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import quote

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from app.services.openai_client import client
from app.services.vectorstores import ensure_offer_vs
from app.services.openai_compat import attach_file_to_vector_store

# ---- BATCH SUPPORT (add near other imports/constants) ----
DATABASE_URL = os.getenv("DATABASE_URL")
BATCH_TTL_DAYS = int(os.getenv("BATCH_TTL_DAYS", "30"))

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL)

def resolve_batch_id_by_token(token: str, org_id: int | None) -> int | None:
    """Return offer_batches.id for token+org, or None if missing."""
    if not token:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if org_id:
                    cur.execute("""
                        SELECT id FROM public.offer_batches
                        WHERE token = %s AND org_id = %s AND status = 'active'
                        LIMIT 1
                    """, (token, org_id))
                else:
                    # fallback: no org in header/form – resolve by token only
                    cur.execute("""
                        SELECT id FROM public.offer_batches
                        WHERE token = %s AND status = 'active'
                        LIMIT 1
                    """, (token,))
                row = cur.fetchone()
                return int(row[0]) if row else None
    except Exception as e:
        print("[resolve_batch_id_by_token] error:", repr(e))
        traceback.print_exc()
        return None

# Configuration
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/json",
    "text/xml",
    "application/xml",
    "text/plain",
}

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
STORAGE_ROOT = os.getenv("STORAGE_ROOT", "/tmp")
S3_BUCKET = os.getenv("S3_BUCKET")

# Initialize OpenAI client
# openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

router = APIRouter(prefix="/api/offers", tags=["offers"])


def validate_environment():
    """Validate required environment variables."""
    missing = []
    if not os.getenv("DATABASE_URL"): missing.append("DATABASE_URL")
    if not os.getenv("OPENAI_API_KEY"): missing.append("OPENAI_API_KEY")
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing environment: {', '.join(missing)}")


def safe_filename(filename: str) -> str:
    """Sanitize filename for safe storage."""
    # Remove path components and dangerous characters
    safe = os.path.basename(filename)
    # Replace unsafe characters with underscores
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in safe)
    # Limit length
    safe = safe[:100] if len(safe) > 100 else safe
    return safe or "uploaded_file"


def validate_size(file_size: int) -> None:
    """Validate file size."""
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB"
        )


def validate_mime(mime_type: str, filename: str) -> None:
    """Validate MIME type against allowlist."""
    # Try to detect MIME type from filename extension as fallback
    ext_mime_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".json": "application/json",
        ".xml": "text/xml",
        ".txt": "text/plain",
    }
    
    mime_lower = mime_type.lower() if mime_type else ""
    allowed_lower = {m.lower() for m in ALLOWED_MIME_TYPES}
    
    # Check if MIME type is allowed
    if mime_lower in allowed_lower:
        return
    
    # Fallback to extension-based detection
    ext = Path(filename).suffix.lower()
    if ext in ext_mime_map and ext_mime_map[ext].lower() in allowed_lower:
        return
    
    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file type: {mime_type}. Allowed: {', '.join(ALLOWED_MIME_TYPES)}"
    )


def ensure_dir(path: Path) -> None:
    """Ensure directory exists."""
    path.mkdir(parents=True, exist_ok=True)


def compute_sha256(content: bytes) -> str:
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(content).hexdigest()


def scan_antivirus(file_path: str) -> bool:
    """TODO: Implement antivirus scanning."""
    # Stub implementation - always return True
    return True


def scrub_pii(content: bytes) -> bytes:
    """TODO: Implement PII redaction."""
    # Stub implementation - return content unchanged
    return content






def check_duplicate(org_id: int, sha256: str) -> Optional[Dict[str, Any]]:
    """Check if file with same org_id and sha256 already exists."""
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, filename, retrieval_file_id, vector_store_id, storage_path
                FROM public.offer_files
                WHERE org_id = %s AND sha256 = %s
                LIMIT 1
                """,
                (org_id, sha256)
            )
            result = cur.fetchone()
            return dict(result) if result else None


def get_org_vector_store(org_id: int) -> str:
    """Get vector store ID for organization."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vector_store_id FROM public.org_vector_stores WHERE org_id = %s",
                (org_id,)
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(
                    status_code=400,
                    detail=f"No vector store found for org_id {org_id}"
                )
            return result[0]


def save_to_storage(content: bytes, org_id: int, filename: str) -> str:
    """Save file to storage (local or S3)."""
    now = datetime.now()
    year, month = now.year, now.month
    file_uuid = str(uuid.uuid4())
    safe_name = safe_filename(filename)
    final_filename = f"{file_uuid}-{safe_name}"
    
    if S3_BUCKET:
        # S3 storage
        import boto3
        s3_client = boto3.client('s3')
        s3_key = f"offers/{org_id}/{year}/{month:02d}/{final_filename}"
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=content,
            ContentType='application/octet-stream'
        )
        
        storage_path = f"s3://{S3_BUCKET}/{s3_key}"
        print(f"Saved to S3: {storage_path}")
    else:
        # Local storage
        storage_dir = Path(STORAGE_ROOT) / "offers" / str(org_id) / str(year) / f"{month:02d}"
        ensure_dir(storage_dir)
        storage_path = storage_dir / final_filename
        
        storage_path.write_bytes(content)
        storage_path = str(storage_path)
        print(f"Saved to local storage: {storage_path}")
    
    return storage_path


def upload_to_openai(content: bytes, filename: str) -> str:
    """Upload file to OpenAI and return file ID."""
    # Create a temporary file for OpenAI upload
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp_file:
        tmp_file.write(content)
        tmp_file.flush()
        
        try:
            with open(tmp_file.name, 'rb') as f:
                file_obj = client.files.create(
                    file=f,
                    purpose="assistants"
                )
            return file_obj.id
        finally:
            os.unlink(tmp_file.name)


@router.post("/upload")
async def upload_offer_file(
    request: Request,
    org_id: int = Form(...),
    created_by_user_id: int = Form(...),
    file: UploadFile = File(...),
    offer_id: Optional[int] = Form(None),
    batch_token: Optional[str] = Form(None),
):
    """Upload offer file and create vector store attachment."""
    validate_environment()
    
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Validate size and MIME type
    validate_size(file_size)
    validate_mime(file.content_type, file.filename)
    
    # Compute SHA-256
    sha256 = compute_sha256(content)
    
    # Check for duplicates
    existing = check_duplicate(org_id, sha256)
    if existing:
        try:
            # existing is a dict-like row (id, filename, storage_path, vector_store_id, retrieval_file_id, batch_id, ...)
            existing_id = existing["id"]
            existing_vs_id = existing.get("vector_store_id")
            existing_retrieval_id = existing.get("retrieval_file_id")
            existing_batch_id = existing.get("batch_id")

            # 1) If batch_token provided, resolve and link the existing file to that batch (idempotent)
            resolved_batch_id = None
            if batch_token:
                try:
                    resolved_batch_id = resolve_batch_id_by_token(batch_token, org_id)
                except HTTPException as e:
                    # bubble the 404/400 batch errors
                    raise

                if existing_batch_id is None or existing_batch_id != resolved_batch_id:
                    with get_db_connection() as conn, conn.cursor() as cur:
                        cur.execute(
                            "UPDATE public.offer_files SET batch_id = %s WHERE id = %s",
                            (resolved_batch_id, existing_id),
                        )
                        conn.commit()
                    existing_batch_id = resolved_batch_id

            # 2) Ensure the file is attached to the org's vector store.
            #    If retrieval_file_id is missing, create + attach now (idempotent).
            #    Always rely on org mapping (org_vector_stores) for the current vector_store_id.
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT vector_store_id FROM public.org_vector_stores WHERE org_id = %s",
                    (org_id,),
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    raise HTTPException(status_code=502, detail="No vector store configured for this org.")
                vector_store_id = row[0]

            if not existing_retrieval_id:
                # Prefer disk read; if missing, self-heal from in-memory 'content'
                storage_path = existing.get("storage_path")
                fbytes = None

                try:
                    if storage_path:
                        with open(storage_path, "rb") as fh:
                            fbytes = fh.read()
                except FileNotFoundError:
                    print(f"[duplicate reattach] storage file missing at {storage_path}; will self-heal")
                except Exception as e:
                    print("[duplicate reattach] error reading storage file:", storage_path, repr(e))
                    traceback.print_exc()

                # If we couldn't read from disk, fall back to the just-uploaded in-memory bytes
                if not fbytes:
                    try:
                        # Ensure we have bytes from the current request
                        # 'content' was already read to compute sha256; use it here
                        if not content:
                            # Extremely unlikely, but be defensive
                            content = await file.read()
                        # Re-save to current storage root and update DB path
                        healed_storage_path = save_to_storage(content, org_id, existing["filename"])
                        with get_db_connection() as conn, conn.cursor() as cur:
                            cur.execute(
                                "UPDATE public.offer_files SET storage_path = %s WHERE id = %s",
                                (healed_storage_path, existing_id),
                            )
                            conn.commit()
                        storage_path = healed_storage_path
                        fbytes = content
                        print(f"[duplicate reattach] self-healed storage_path -> {healed_storage_path}")
                    except Exception as e:
                        print("[duplicate reattach] self-heal failed:", repr(e))
                        traceback.print_exc()
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to recover missing storage file for duplicate attach: {str(e)}"
                        )

                try:
                    # Create file in OpenAI
                    mime = existing.get("mime_type") or (file.content_type if 'file' in locals() else "application/octet-stream")
                    created_file = client.files.create(
                        file=(existing["filename"], fbytes, mime),
                        purpose="assistants",
                    )
                    retrieval_file_id = created_file.id if hasattr(created_file, "id") else created_file["id"]

                    # Attach to vector store (stable first, then fallback)
                    attach_file_to_vector_store(client, vector_store_id, retrieval_file_id)

                    # Update DB with retrieval_file_id + vector_store_id + embeddings_ready
                    with get_db_connection() as conn, conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE public.offer_files
                               SET retrieval_file_id = %s,
                                   vector_store_id    = %s,
                                   embeddings_ready   = TRUE
                             WHERE id = %s
                            """,
                            (retrieval_file_id, vector_store_id, existing_id),
                        )
                        conn.commit()

                    existing_retrieval_id = retrieval_file_id
                    existing_vs_id = vector_store_id

                except Exception as e:
                    print("[duplicate reattach] OpenAI error:", repr(e))
                    traceback.print_exc()
                    raise HTTPException(status_code=502, detail=f"OpenAI error re-attaching duplicate file: {str(e)}")

            # 3) Re-select and return the full, up-to-date row
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, org_id, created_by_user_id, offer_id, batch_id,
                           filename, mime_type, size_bytes, sha256, storage_path,
                           extracted_text, raw_json, embeddings_ready, created_at,
                           vector_store_id, retrieval_file_id
                      FROM public.offer_files
                     WHERE id = %s
                    """,
                    (existing_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=500, detail="Duplicate file row not found after update.")

            payload = {
                "id": row[0],
                "org_id": row[1],
                "created_by_user_id": row[2],
                "offer_id": row[3],
                "batch_id": row[4],
                "filename": row[5],
                "mime_type": row[6],
                "size_bytes": row[7],
                "sha256": row[8],
                "storage_path": row[9],
                "extracted_text": row[10],
                "raw_json": row[11],
                "embeddings_ready": row[12],
                "created_at": row[13].isoformat() if row[13] else None,
                "vector_store_id": row[14],
                "retrieval_file_id": row[15],
                "duplicate": True,
            }
            if batch_token:
                payload["batch_token"] = batch_token

            print(f"[upload] duplicate hit -> id={existing_id}, batch_token={batch_token}")
            print(f"[upload] duplicate had retrieval? {bool(existing_retrieval_id)}; ensured vs={existing_vs_id}")

            return JSONResponse(status_code=200, content=payload)

        except HTTPException:
            raise
        except Exception as e:
            print("[duplicate handling] unexpected error:", repr(e))
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))
    
    # ---- batch resolution (required) ----
    if not batch_token:
        raise HTTPException(status_code=422, detail="batch_token is required for offer uploads")
    
    batch_id: Optional[int] = None
    batch_id = resolve_batch_id_by_token(batch_token, org_id)
    if batch_id is None:
        # Surface a clear error instead of generic 500s
        raise HTTPException(
            status_code=404,
            detail=f"batch_token '{batch_token}' not found or not active for org_id={org_id}"
        )
    
    # TODO: Antivirus scan
    if not scan_antivirus(file.filename):
        raise HTTPException(status_code=400, detail="File failed antivirus scan")
    
    # TODO: PII redaction
    content = scrub_pii(content)
    
    # Get vector store ID for offer batch
    with get_db_connection() as conn:
        vector_store_id = ensure_offer_vs(conn, org_id, batch_token)
    
    # Save to storage
    try:
        storage_path = save_to_storage(content, org_id, file.filename)
    except Exception as e:
        print("[save_to_storage] error:", repr(e))
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Storage error: {str(e)}. If S3_BUCKET is set, ensure AWS credentials & region are configured, or unset S3_BUCKET to use local /tmp."
        )
    
    # Insert into database
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO public.offer_files
                    (filename, mime_type, size_bytes, sha256, storage_path, 
                     org_id, created_by_user_id, offer_id, batch_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (file.filename, file.content_type, file_size, sha256, storage_path,
                     org_id, created_by_user_id, offer_id, batch_id)
                )
                file_record_id = cur.fetchone()["id"]
                conn.commit()
        
        print(f"Inserted offer_file record: {file_record_id}")
    except Exception as e:
        print("[upload_offer_file] DB insert error:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"DB insert failed: {e}")
    
    try:
        # Upload to OpenAI
        retrieval_file_id = upload_to_openai(content, file.filename)
        print(f"Uploaded to OpenAI: {retrieval_file_id}")
        
        # Attach to vector store
        attach_file_to_vector_store(client, vector_store_id, retrieval_file_id)
        print(f"[upload] attached file {retrieval_file_id} to vector store {vector_store_id}")
        print(f"Attached to vector store: {vector_store_id}")
        
        # Update database with OpenAI IDs
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.offer_files
                    SET retrieval_file_id = %s, vector_store_id = %s, embeddings_ready = true
                    WHERE id = %s
                    """,
                    (retrieval_file_id, vector_store_id, file_record_id)
                )
                conn.commit()
        
        print(f"Updated database with OpenAI IDs for record: {file_record_id}")
        
    except Exception as e:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE public.offer_files SET embeddings_ready = false WHERE id = %s", (file_record_id,))
                conn.commit()
        print("[upload_offer_file] OpenAI error:", repr(e))
        traceback.print_exc()
        # Surface a concise reason; common cases: invalid OPENAI_API_KEY, bad vector_store_id
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI error during upload/attach: {str(e)}"
        )
    
    payload = {
        "id": file_record_id,
        "filename": file.filename,
        "retrieval_file_id": retrieval_file_id if 'retrieval_file_id' in locals() else None,
        "vector_store_id": vector_store_id,
        "storage_path": storage_path,
        "sha256": sha256,
        "size_bytes": file_size,
        "embeddings_ready": 'retrieval_file_id' in locals()
    }
    if batch_token:
        payload["batch_token"] = batch_token

    return JSONResponse(payload)
