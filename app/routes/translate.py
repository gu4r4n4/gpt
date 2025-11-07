# app/routes/translate.py
from __future__ import annotations
import os
from typing import Optional, Dict, Any
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from openai import APIStatusError, APIConnectionError, RateLimitError
from app.services.openai_client import client

router = APIRouter(prefix="/api/translate", tags=["translate"])

DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None  # required for direction=out

@router.post("")
async def translate(
    body: TranslateBody,
    direction: str = Query(..., pattern="^(in|out)$"),
    preserveMarkdown: Optional[bool] = Query(False),
):
    raw = (body.text or "").strip()
    if not raw:
        return {"ok": True, "text": "", "translatedInput": None, "translatedOutput": None}

    if direction == "out":
        tl = (body.targetLang or "").strip()
        if not tl:
            raise HTTPException(status_code=400, detail="targetLang is required for direction=out")

    pm = str(preserveMarkdown).lower() in {"1", "true", "yes"}

    if direction == "in":
        sys = (
            "Translate the user's message into English. "
            + ("Preserve original Markdown formatting, tables and code blocks. " if pm else "")
            + "Return only the translated text."
        )
    else:
        sys = (
            f"Translate the user's message from English into {body.targetLang}. "
            + ("Preserve Markdown tables, headings and code fences. " if pm else "")
            + "Return only the translated text."
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fail-open by returning original content unchanged
        if direction == "in":
            return {"ok": True, "text": raw, "translatedInput": raw, "translatedOutput": None}
        return {"ok": True, "text": raw, "translatedInput": None, "translatedOutput": raw}

    try:
        rsp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role":"system","content":sys},{"role":"user","content":raw}],
            temperature=0,
        )
        first_choice = rsp.choices[0]
        message_content = first_choice.message.content if first_choice.message else ""
        out = (message_content or "").strip()
        if not out:
            raise HTTPException(status_code=502, detail="Empty translation from model")
        if direction == "in":
            return {"ok": True, "text": out, "translatedInput": out, "translatedOutput": None}
        else:
            return {"ok": True, "text": out, "translatedInput": None, "translatedOutput": out}
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=f"OpenAI rate limit: {e}") from e
    except APIConnectionError as e:
        raise HTTPException(status_code=502, detail=f"OpenAI connection error: {e}") from e
    except APIStatusError as e:
        raise HTTPException(status_code=e.status_code or 502, detail=f"OpenAI API error: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Translation failed: {e}") from e
