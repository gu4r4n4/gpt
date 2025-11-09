"""Utility functions for API routes"""
import os
from typing import Any, Dict
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

def safe_filename(filename: str) -> str:
    """Sanitize filename for safe storage"""
    # Remove path components and dangerous characters
    safe = os.path.basename(filename)
    # Replace unsafe characters with underscores
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in safe)
    # Limit length
    safe = safe[:100] if len(safe) > 100 else safe
    return safe or "uploaded_file"

def get_db_connection():
    """
    Prefer DATABASE_URL (single var, matches the rest of the app).
    Fallback to PG* envs for local/dev if DATABASE_URL is not present.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url, cursor_factory=RealDictCursor)

    # Fallback (dev)
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE"),
        user=os.environ.get("PGUSER"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST"),
        port=os.environ.get("PGPORT"),
        cursor_factory=RealDictCursor
    )

def validate_mime(mime_type: str, filename: str) -> None:
    """Validate that the file is a PDF based on mime type and extension"""
    if not mime_type or not mime_type.lower() == 'application/pdf':
        raise ValueError(f"Invalid mime type: {mime_type}")
    if not filename or not filename.lower().endswith('.pdf'):
        raise ValueError(f"Invalid filename extension: {filename}")

def ensure_offer_vs(conn: Any, org_id: int, batch_token: str | None = None) -> str:
    """Ensure vector store exists for the organization"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM vector_stores WHERE org_id = %s AND is_default = true",
            (org_id,)
        )
        result = cur.fetchone()
        if result:
            return result["id"]
        
        # Create new vector store if needed
        cur.execute(
            """INSERT INTO vector_stores (org_id, is_default, name) 
            VALUES (%s, true, %s) RETURNING id""",
            (org_id, f"Default Store for Org {org_id}")
        )
        return cur.fetchone()["id"]

def attach_file_to_vector_store(client: Any, store_id: str, file_id: str) -> None:
    """Attach a file to a vector store using the OpenAI client"""
    client.files.update(file_id=file_id, purpose="assistants")
    client.beta.assistants.files.create(
        assistant_id=store_id,
        file_id=file_id
    )