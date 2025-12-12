"""
Admin Chat API Endpoint
Secure bridge between Admin UI and n8n AI workflow.
"""

from __future__ import annotations

import os
import json
from typing import Optional, Dict, Any

import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel

router = APIRouter(prefix="/api/admin/chat", tags=["admin-chat"])


def get_db():
    """Database connection dependency."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


class ChatRequest(BaseModel):
    """Request body from frontend."""
    message: str


class ChatResponse(BaseModel):
    """Response to frontend."""
    role: str
    content: str


def _get_user_from_db(conn, user_id: int) -> Optional[Dict[str, Any]]:
    """Fetch user from app_users table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, org_id, email, full_name, role
            FROM public.app_users
            WHERE id = %s
            LIMIT 1
            """,
            (user_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _check_user_role(user: Dict[str, Any]) -> None:
    """Verify user has admin or owner role."""
    role = (user.get("role") or "").lower()
    if role not in ("admin", "owner"):
        raise HTTPException(
            status_code=403,
            detail="Unauthorized: admin or owner role required"
        )


def _get_or_create_session(conn, org_id: int, user_id: int) -> int:
    """Get existing session or create a new one for the user."""
    with conn.cursor() as cur:
        # Try to find existing session
        cur.execute(
            """
            SELECT id
            FROM public.admin_chat_sessions
            WHERE org_id = %s AND created_by_user_id = %s AND source = 'admin_ui'
            ORDER BY last_activity_at DESC
            LIMIT 1
            """,
            (org_id, user_id)
        )
        row = cur.fetchone()
        
        if row:
            session_id = row["id"]
            # Update last_activity_at
            cur.execute(
                """
                UPDATE public.admin_chat_sessions
                SET last_activity_at = NOW()
                WHERE id = %s
                """,
                (session_id,)
            )
            conn.commit()
            return session_id
        else:
            # Create new session
            cur.execute(
                """
                INSERT INTO public.admin_chat_sessions (org_id, created_by_user_id, source, last_activity_at)
                VALUES (%s, %s, 'admin_ui', NOW())
                RETURNING id
                """,
                (org_id, user_id)
            )
            session_id = cur.fetchone()["id"]
            conn.commit()
            return session_id


def _save_message(conn, session_id: int, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Save a chat message to the database."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.admin_chat_messages (session_id, role, content, metadata)
            VALUES (%s, %s, %s, %s)
            """,
            (session_id, role, content, json.dumps(metadata or {}))
        )
        conn.commit()


def _call_n8n_webhook(payload: Dict[str, Any]) -> str:
    """Call n8n webhook and extract assistant response."""
    webhook_url = os.getenv("N8N_ADMIN_CHAT_WEBHOOK_URL")
    if not webhook_url:
        raise HTTPException(
            status_code=500,
            detail="N8N webhook URL not configured"
        )
    
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        
        # Try to extract response from various possible locations
        data = response.json() if response.content else {}
        
        # Check common response patterns
        assistant_text = (
            data.get("output") or
            data.get("text") or
            data.get("message") or
            data.get("response") or
            ""
        )
        
        # If response is a dict, try to get text from it
        if isinstance(assistant_text, dict):
            assistant_text = (
                assistant_text.get("text") or
                assistant_text.get("content") or
                assistant_text.get("message") or
                ""
            )
        
        if not assistant_text or not str(assistant_text).strip():
            raise HTTPException(
                status_code=502,
                detail="Empty response from n8n workflow"
            )
        
        return str(assistant_text).strip()
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to reach n8n webhook"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error processing n8n response"
        )


@router.post("", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    x_org_id: Optional[int] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[int] = Header(None, alias="X-User-Id"),
    conn = Depends(get_db)
):
    """
    Admin chat endpoint - secure bridge to n8n AI workflow.
    
    Requires:
    - X-Org-Id header
    - X-User-Id header
    - User must have role 'admin' or 'owner'
    """
    # Extract user context from headers
    org_id = x_org_id
    user_id = x_user_id
    
    if not org_id or not user_id:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Org-Id or X-User-Id headers"
        )
    
    # Fetch user from database
    user = _get_user_from_db(conn, user_id)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found"
        )
    
    # Verify user belongs to the org
    if user.get("org_id") != org_id:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to the specified organization"
        )
    
    # Check role (admin or owner)
    _check_user_role(user)
    
    # Get or create chat session
    session_id = _get_or_create_session(conn, org_id, user_id)
    
    # Save user message
    _save_message(conn, session_id, "user", request.message)
    
    # Build n8n-compatible payload
    n8n_payload = {
        "text": request.message,
        "sessionId": f"admin_{user_id}",
        "user_id": user_id,
        "username": user.get("email") or "",
        "first_name": user.get("full_name") or "",
        "source": "admin_ui"
    }
    
    # Call n8n webhook
    try:
        assistant_response = _call_n8n_webhook(n8n_payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to get response from AI workflow"
        )
    
    # Save assistant response
    _save_message(conn, session_id, "assistant", assistant_response)
    
    # Return response to frontend
    return ChatResponse(
        role="assistant",
        content=assistant_response
    )

