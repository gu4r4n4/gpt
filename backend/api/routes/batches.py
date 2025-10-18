"""
Offer batch management endpoints.
"""
import os
import secrets
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Form, Body
from pydantic import BaseModel

# Configuration
BATCH_TTL_DAYS = int(os.getenv("BATCH_TTL_DAYS", "30"))
DATABASE_URL = os.getenv("DATABASE_URL")

router = APIRouter(prefix="/api/batches", tags=["batches"])


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(DATABASE_URL)


def generate_batch_token() -> str:
    """Generate a secure random batch token."""
    return secrets.token_urlsafe(16)


def log_qa(batch_id: int, org_id: int, user_id: int, question: str, answer_summary: str, sources_json: str):
    """Log QA interaction to offer_qa_logs."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.offer_qa_logs
                (batch_id, org_id, user_id, question, answer_summary, sources_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                """,
                (batch_id, org_id, user_id, question, answer_summary, sources_json)
            )
            conn.commit()


class BatchCreateBody(BaseModel):
    title: Optional[str] = None


class BatchShareBody(BaseModel):
    expires_in_hours: Optional[int] = 720  # 30 days default
    editable: Optional[bool] = True


@router.post("")
def create_batch(body: BatchCreateBody):
    """Create an empty batch."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    
    batch_token = generate_batch_token()
    expires_at = datetime.utcnow() + timedelta(days=BATCH_TTL_DAYS)
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.offer_batches (batch_token, title, expires_at, status)
                VALUES (%s, %s, %s, 'active')
                RETURNING id
                """,
                (batch_token, body.title, expires_at)
            )
            batch_id = cur.fetchone()[0]
            conn.commit()
    
    return {
        "batch_token": batch_token,
        "expires_at": expires_at.isoformat() + "Z",
        "status": "active"
    }


@router.get("/{token}")
def get_batch(token: str):
    """Get batch info and files."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get batch info
            cur.execute(
                "SELECT * FROM public.offer_batches WHERE batch_token = %s",
                (token,)
            )
            batch = cur.fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")
            
            # Get files for this batch
            cur.execute(
                """
                SELECT id, filename, size_bytes, retrieval_file_id, vector_store_id, created_at
                FROM public.offer_files
                WHERE batch_id = %s
                ORDER BY created_at
                """,
                (batch["id"],)
            )
            files = cur.fetchall()
    
    return {
        "token": token,
        "title": batch["title"],
        "status": batch["status"],
        "expires_at": batch["expires_at"].isoformat() + "Z" if batch["expires_at"] else None,
        "files": [dict(f) for f in files]
    }


@router.post("/{token}/share")
def share_batch(token: str, body: BatchShareBody):
    """Create share link for batch files."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    
    # Get batch and its files
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM public.offer_batches WHERE batch_token = %s",
                (token,)
            )
            batch = cur.fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="Batch not found")
            
            cur.execute(
                "SELECT filename FROM public.offer_files WHERE batch_id = %s",
                (batch["id"],)
            )
            files = cur.fetchall()
    
    if not files:
        raise HTTPException(status_code=400, detail="No files in batch to share")
    
    # Create share using existing shares flow
    from app.main import create_share_token_only, ShareCreateBody
    
    share_body = ShareCreateBody(
        title=f"Batch: {token}",
        document_ids=[f["filename"] for f in files],
        expires_in_hours=body.expires_in_hours,
        editable=body.editable
    )
    
    # Mock request object for existing function
    class MockRequest:
        def url_for(self, name, **kwargs):
            return f"/shares/{kwargs['token']}"
    
    result = create_share_token_only(share_body, MockRequest())
    return {
        "ok": True,
        "token": result["token"],
        "url": result["url"]
    }


@router.post("/{token}/expire")
def expire_batch(token: str):
    """Set batch status to expired."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.offer_batches
                SET status = 'expired', expires_at = now()
                WHERE batch_token = %s
                RETURNING id
                """,
                (token,)
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Batch not found")
            conn.commit()
    
    return {"ok": True, "status": "expired"}
