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


def _get_n8n_webhook_url() -> str:
    """Get n8n webhook URL, supporting both test and production modes."""
    base_url = os.getenv("N8N_ADMIN_CHAT_WEBHOOK_URL", "").rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=500,
            detail="N8N_ADMIN_CHAT_WEBHOOK_URL environment variable not configured"
        )
    
    use_test = os.getenv("N8N_USE_TEST_WEBHOOK", "false").lower() in ("true", "1", "yes")
    webhook_path = "admin-chat"
    
    if "/webhook-test/" in base_url or "/webhook-test" in base_url:
        if not base_url.endswith(webhook_path):
            if not base_url.endswith("/"):
                base_url += "/"
            base_url += webhook_path
        return base_url
    elif "/webhook/" in base_url or "/webhook" in base_url:
        if not base_url.endswith(webhook_path):
            if not base_url.endswith("/"):
                base_url += "/"
            base_url += webhook_path
        return base_url
    else:
        if use_test:
            webhook_type = "webhook-test"
        else:
            webhook_type = "webhook"
        
        if not base_url.endswith("/"):
            base_url += "/"
        return f"{base_url}{webhook_type}/{webhook_path}"


def _call_n8n_webhook(payload: Dict[str, Any]) -> str:
    """Call n8n webhook and extract assistant response."""
    try:
        webhook_url = _get_n8n_webhook_url()
    except HTTPException:
        raise
    
    print(f"[admin_chat] n8n webhook URL: {webhook_url}")
    print(f"[admin_chat] n8n payload: {json.dumps(payload)}")
    
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        print(f"[admin_chat] n8n response status: {response.status_code}")
        print(f"[admin_chat] n8n response headers: {dict(response.headers)}")
        
        if response.status_code == 404:
            error_msg = f"n8n webhook not found (404): {webhook_url}. Check if workflow is active and webhook path is 'admin-chat'"
            traceback.print_exc()
            raise HTTPException(
                status_code=502,
                detail=error_msg
            )
        
        if response.status_code >= 500:
            error_msg = f"n8n webhook server error ({response.status_code}): {response.text[:200]}"
            traceback.print_exc()
            raise HTTPException(
                status_code=502,
                detail=error_msg
            )
        
        response.raise_for_status()
        
        if not response.content:
            error_msg = "Empty response body from n8n workflow"
            print(f"[admin_chat] ERROR: {error_msg}")
            raise HTTPException(
                status_code=502,
                detail=error_msg
            )
        
        try:
            data = response.json()
        except ValueError as e:
            error_msg = f"Invalid JSON response from n8n workflow: {str(e)}. Response body: {response.text[:200]}"
            print(f"[admin_chat] ERROR: {error_msg}")
            raise HTTPException(
                status_code=502,
                detail=error_msg
            )
        
        print(f"[admin_chat] n8n response data: {json.dumps(data)}")
        
        assistant_text = (
            data.get("text") or
            data.get("output") or
            data.get("message") or
            ""
        )
        
        if isinstance(assistant_text, dict):
            assistant_text = (
                assistant_text.get("text") or
                assistant_text.get("content") or
                assistant_text.get("message") or
                ""
            )
        
        if not assistant_text or not str(assistant_text).strip():
            error_msg = "Empty response from n8n workflow: no text/output/message field found in response"
            print(f"[admin_chat] ERROR: {error_msg}. Full response: {json.dumps(data)}")
            raise HTTPException(
                status_code=502,
                detail=error_msg
            )
        
        return str(assistant_text).strip()
        
    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        error_msg = f"n8n webhook timeout after 30 seconds: {webhook_url}"
        print(f"[admin_chat] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(
            status_code=504,
            detail=error_msg
        )
    except requests.exceptions.ConnectionError as e:
        error_msg = f"Cannot connect to n8n webhook: {webhook_url}. Error: {str(e)}"
        print(f"[admin_chat] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(
            status_code=502,
            detail=error_msg
        )
    except requests.exceptions.HTTPError as e:
        error_msg = f"n8n webhook HTTP error: {e.response.status_code} - {e.response.text[:200] if hasattr(e, 'response') else str(e)}"
        print(f"[admin_chat] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(
            status_code=502,
            detail=error_msg
        )
    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to reach n8n webhook {webhook_url}: {str(e)}"
        print(f"[admin_chat] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=error_msg
        )
    except Exception as e:
        error_msg = f"Error processing n8n response: {str(e)}"
        print(f"[admin_chat] ERROR: {error_msg}")
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=error_msg
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
        
        # Build n8n-compatible payload
        n8n_payload = {
            "text": request.message,
            "sessionId": f"admin_{user_id}",
            "user_id": user_id,
            "username": user.get("email") or "",
            "source": "admin_ui"
        }
        
        # Call n8n webhook
        assistant_response = _call_n8n_webhook(n8n_payload)
        
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
