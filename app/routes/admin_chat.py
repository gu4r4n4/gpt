"""
Admin Chat API Endpoint
Secure bridge between Admin UI and n8n AI workflow.
"""

from __future__ import annotations

import os
import json
import traceback
from typing import Optional, Dict, Any

import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel

router = APIRouter(prefix="/api/admin/chat", tags=["admin-chat"])


def get_db():
    """Database connection dependency. Never swallows errors."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise HTTPException(
            status_code=503,
            detail="Database not configured"
        )
    try:
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        try:
            yield conn
        finally:
            conn.close()
    except psycopg2.Error as e:
        error_msg = str(e)
        traceback.print_exc()
        raise HTTPException(
            status_code=503,
            detail=f"Database error: {error_msg}"
        )
    except Exception as e:
        error_msg = str(e)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Database connection error: {error_msg}"
        )


class ChatRequest(BaseModel):
    """Request body from frontend."""
    message: str


class ChatResponse(BaseModel):
    """Response to frontend."""
    role: str
    content: str


def _get_user_from_db(conn, user_id: int) -> Optional[Dict[str, Any]]:
    """Fetch user from app_users table."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, org_id, email, full_name, role
                FROM public.app_users
                WHERE id = %s
                LIMIT 1
                """,
                (int(user_id),)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except psycopg2.Error as e:
        error_msg = str(e)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Database query error (get_user): {error_msg}"
        )


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
    try:
        org_id_int = int(org_id)
        user_id_int = int(user_id)
        
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
                (org_id_int, user_id_int)
            )
            row = cur.fetchone()
            
            if row:
                session_id = int(row["id"])
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
                    (org_id_int, user_id_int)
                )
                result = cur.fetchone()
                if not result:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to create chat session: no ID returned"
                    )
                session_id = int(result["id"])
                conn.commit()
                return session_id
    except HTTPException:
        raise
    except psycopg2.Error as e:
        error_msg = str(e)
        traceback.print_exc()
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error (session): {error_msg}"
        )
    except Exception as e:
        error_msg = str(e)
        traceback.print_exc()
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error managing session: {error_msg}"
        )


def _save_message(conn, session_id: int, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Save a chat message to the database. Never fails silently."""
    try:
        session_id_int = int(session_id)
        metadata_json = json.dumps(metadata or {})
        
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.admin_chat_messages (session_id, role, content, metadata)
                VALUES (%s, %s, %s, %s)
                """,
                (session_id_int, str(role), str(content), metadata_json)
            )
            conn.commit()
    except psycopg2.Error as e:
        error_msg = str(e)
        traceback.print_exc()
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error (save_message): {error_msg}"
        )
    except Exception as e:
        error_msg = str(e)
        traceback.print_exc()
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error saving message: {error_msg}"
        )


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    conn = Depends(get_db)
):
    """
    Admin chat endpoint - secure bridge to n8n AI workflow.
    
    Requires:
    - X-Org-Id header (integer)
    - X-User-Id header (integer)
    - User must have role 'admin' or 'owner'
    """
    try:
        # Explicitly cast headers to int, reject with 400 if cast fails
        if not x_org_id:
            raise HTTPException(
                status_code=400,
                detail="Missing X-Org-Id header"
            )
        if not x_user_id:
            raise HTTPException(
                status_code=400,
                detail="Missing X-User-Id header"
            )
        
        try:
            org_id = int(x_org_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid X-Org-Id header: must be integer, got '{x_org_id}'"
            )
        
        try:
            user_id = int(x_user_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid X-User-Id header: must be integer, got '{x_user_id}'"
            )
        
        # Debug log before session lookup
        print(f"[admin_chat] DEBUG: org_id={org_id}, user_id={user_id}, type(org_id)={type(org_id)}, type(user_id)={type(user_id)}")
        
        # Fetch user from database
        user = _get_user_from_db(conn, user_id)
        if not user:
            raise HTTPException(
                status_code=401,
                detail="User not found"
            )
        
        # Verify user belongs to the org
        user_org_id = int(user.get("org_id")) if user.get("org_id") is not None else None
        if user_org_id != org_id:
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
        
        # Return dummy response (DO NOT CALL n8n yet)
        assistant_response = "OK"
        
        # Save assistant response
        _save_message(conn, session_id, "assistant", assistant_response)
        
        # Return response to frontend
        return ChatResponse(
            role="assistant",
            content=assistant_response
        )
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {error_msg}"
        )
