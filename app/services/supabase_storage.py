from supabase import create_client, Client
import os
from typing import Union
from fastapi import UploadFile
import tempfile

def put_pdf_and_get_path(upload_file: UploadFile) -> str:
    """
    Store a PDF in Supabase Storage and return its durable path.
    Uses SUPABASE_URL and SUPABASE_SERVICE_KEY env vars.
    
    Args:
        upload_file: FastAPI UploadFile (PDF)
    
    Returns:
        str: Durable path like supabase://offers/batch_X/file.pdf
    """
    sb: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    bucket = "offers"
    
    # Use env BATCH_ID or "manual" if not set
    batch_id = os.getenv("BATCH_ID", "manual")
    
    # Key pattern: batch_123/filename.pdf
    key = f"batch_{batch_id}/{upload_file.filename}"
    
    # Upload with proper content type
    sb.storage.from_(bucket).upload(
        key,
        upload_file.file,
        file_options={"contentType": "application/pdf"}
    )
    
    # Return canonical durable path that our scripts can resolve
    return f"supabase://{bucket}/{key}"

def get_pdf_from_storage(durable_path: str) -> Union[str, None]:
    """
    Get a PDF from Supabase Storage to a temporary file path.
    
    Args:
        durable_path: Path like supabase://bucket/key or other valid storage path
    
    Returns:
        str: Local temporary file path where the PDF was downloaded
             None if path format not supported
    """
    # Handle supabase:// URIs (our canonical format)
    if durable_path.startswith("supabase://"):
        try:
            # Parse bucket and key
            _, path = durable_path.split("supabase://", 1)
            bucket, key = path.split("/", 1)
            
            # Get signed URL first (required for download)
            sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
            resp = sb.storage.from_(bucket).create_signed_url(key, 60)  # 60s is enough to fetch
            signed_url = resp.get("signed_url") or resp.get("signedURL") or resp["signedURL"]
            
            # Download to temp using httpx
            import httpx
            r = httpx.get(signed_url)
            r.raise_for_status()
            
            # Write to temp file
            fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as f:
                f.write(r.content)
            
            return tmp_path
            
        except Exception as e:
            print(f"[storage] Failed to get PDF from {durable_path}: {e}")
            return None
    
    # Return None for unsupported path formats
    return None