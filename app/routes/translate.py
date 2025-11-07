# app/routes/translate.py
from __future__ import annotations
import os
from typing import Optional, Dict, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from openai import OpenAI

router = APIRouter(prefix="/api/translate", tags=["translate"])

# Lazily create client so boot works even without a key
_client: Optional[OpenAI] = None
DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None  # only used when direction=out

def _ensure_client() -> Optional[OpenAI]:
    """Return OpenAI client if key exists; else None (we fail-open)."""
    global _client
    if not os.getenv("OPENAI_API_KEY"):
        return None
    if _client is None:
        _client = OpenAI()
    return _client

def _mk_response(*, text: str, translated_in: Optional[str]=None,
                 translated_out: Optional[str]=None, error: Optional[str]=None) -> Dict[str, Any]:
    """Stable, FE-friendly shape. Always 200 from the route."""
    payload: Dict[str, Any] = {
        "ok": error is None,
        "text": text or "",
        "translatedInput": translated_in,
        "translatedOutput": translated_out,
    }
    if error:
        payload["error"] = error
    return payload

@router.post("")
async def translate(
    body: TranslateBody,
    # DO NOT validate here; we accept anything and handle inside to avoid 422s.
    direction: str = Query(...),
    preserveMarkdown: Optional[bool] = Query(False),
):
    """
    POST /api/translate?direction=in|out[&preserveMarkdown=true]
    Body: { "text": "...", "targetLang": "latvian" }
    Always returns 200 with:
      { ok, text, translatedInput?, translatedOutput?, error? }
    """
    raw = (body.text or "").strip()
    if not raw:
        return _mk_response(text="")

    # normalize inputs (be generous)
    d = (direction or "").strip().lower()
    if d not in {"in", "out"}:
        # Don’t throw; return safe payload so FE never blanks
        return _mk_response(text=raw, translated_out=raw, error="invalid direction (expected 'in' or 'out')")

    # handle preserveMarkdown that might come as "true"/"1"/true
    pm = bool(str(preserveMarkdown).lower() in {"1", "true", "yes"})

    client = _ensure_client()
    if client is None:
        # No key configured: fail-open (echo)
        if d == "in":
            return _mk_response(text=raw, translated_in=raw)
        else:
            return _mk_response(text=raw, translated_out=raw)

    # Build a conservative system prompt
    if d == "in":
        sys = (
            "Translate the user's message into English. "
            + ("Preserve original Markdown formatting, tables and code blocks. " if pm else "")
            + "Do not add explanations—return only the translated text."
        )
    else:
        tl = (body.targetLang or "").strip()
        if not tl:
            # Keep 200; FE-safe
            return _mk_response(text=raw, translated_out=raw, error="targetLang is required for direction=out")
        sys = (
            f"Translate the user's message from English into {tl}. "
            + ("Preserve Markdown tables, headings and code fences. " if pm else "")
            + "Do not add explanations—return only the translated text."
        )

    # OpenAI call – any failure falls back to echo with an error string
    try:
        rsp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": raw},
            ],
            temperature=0,
        )
        out = (rsp.choices[0].message.content or "").strip()
        if not out:
            out = raw  # fail-open if empty

        if d == "in":
            return _mk_response(text=out, translated_in=out)
        else:
            return _mk_response(text=out, translated_out=out)

    except Exception as e:
        if d == "in":
            return _mk_response(text=raw, translated_in=raw, error=f"{type(e).__name__}: {e}")
        else:
            return _mk_response(text=raw, translated_out=raw, error=f"{type(e).__name__}: {e}")
