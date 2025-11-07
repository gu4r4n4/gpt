from __future__ import annotations
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from hashlib import sha256 as _sha256
from app.services.openai_client import client
from app.services.openai_compat import ensure_vector_store, add_file_to_store

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url)

def compute_sha256(b: bytes) -> str:
    """Compute SHA-256 hex digest of bytes."""
    return _sha256(b).hexdigest()

def ensure_batch_vector_store(org_id: int, batch_token: str) -> str:
    """
    Ensure a vector store exists for the given org_id and batch_token.
    Creates one if it doesn't exist and persists the mapping.
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check if vector store already exists
            cur.execute("""
                SELECT vector_store_id 
                FROM public.org_batch_vector_stores
                WHERE org_id = %s AND batch_token = %s
            """, (org_id, batch_token))
            row = cur.fetchone()
            
            if row:
                return row["vector_store_id"]
            
            # Create new vector store
            vs = ensure_vector_store(
                client, 
                store_hint=f"org:{org_id}:batch:{batch_token}"
            )
            vs_id = vs.id
            
            # Persist the mapping
            cur.execute("""
                INSERT INTO public.org_batch_vector_stores(org_id, batch_token, vector_store_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (org_id, batch_token) DO UPDATE 
                SET vector_store_id = EXCLUDED.vector_store_id
            """, (org_id, batch_token, vs_id))
            conn.commit()
            
            return vs_id

def add_file_to_batch_vs(vector_store_id: str, file_bytes: bytes, filename: str) -> str:
    """
    Upload file bytes to OpenAI and attach to the vector store.
    Returns the retrieval file ID.
    """
    file_obj = add_file_to_store(client, vector_store_id, file_bytes, filename)
    return file_obj.id

def resolve_batch_vector_store(org_id: int, batch_token: str) -> str | None:
    """
    Look up vector store ID for a batch.
    Returns None if not found.
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT vector_store_id 
                FROM public.org_batch_vector_stores
                WHERE org_id = %s AND batch_token = %s
            """, (org_id, batch_token))
            row = cur.fetchone()
            return row["vector_store_id"] if row else None
