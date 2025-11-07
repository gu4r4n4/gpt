# app/routes/translate.py
from __future__ import annotations
import os
from typing import Optional, Dict, Any
from fastapi import APIRouter, Query
from pydantic import BaseModel

# OpenAI import is optional; if it fails we still "fail-open"
try:
    from openai import OpenAI  # SDK v1+
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

router = APIRouter(prefix="/api/translate", tags=["translate"])

_client: Optional["OpenAI"] = None
DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None  # required only for direction=out

def _ensure_client() -> Optional["OpenAI"]:
    """
    Return OpenAI client if a key + import exist; else None (we fail-open).
    """
    global _client
    if not os.getenv("OPENAI_API_KEY"):
        return None
    if OpenAI is None:  # old SDK not installed / import error
        return None
    if _client is None:
        _client = OpenAI()
    return _client

def _ok_payload(*, original: str, translated_in: Optional[str]=None,
                translated_out: Optional[str]=None, error: Optional[str]=None) -> Dict[str, Any]:
    """
    FE-friendly + stable shape. Always HTTP 200; `ok` remains True even on echo fallback.
    """
    payload: Dict[str, Any] = {
        "ok": True,  # keep UI happy even if we had to echo
        "text": original or "",            # ALWAYS echo original input here
        "translatedInput": translated_in,  # set only for direction=in
        "translatedOutput": translated_out # set only for direction=out
    }
    if error:
        payload["error"] = error  # diagnostic only; UI may ignore
    return payload

@router.post("")      # /api/translate
@router.post("/")     # /api/translate/ (avoid proxy 307 weirdness)
async def translate(
    body: TranslateBody,
    direction: str = Query(..., description="in|out"),
    preserveMarkdown: Optional[bool] = Query(False),
):
    """
    POST /api/translate?direction=in|out[&preserveMarkdown=true]
    Body: { "text": "...", "targetLang": "latvian" }
    Always returns 200 with: { ok, text, translatedInput?, translatedOutput?, error? }
    """
    raw = (body.text or "").strip()
    if not raw:
        return _ok_payload(original="")

    d = (direction or "").strip().lower()
    if d not in {"in", "out"}:
        # Do not 4xx; echo so FE never blanks
        return _ok_payload(original=raw, translated_out=raw, error="invalid direction (expected 'in' or 'out')")

    # boolean query can arrive as True/False or "true"/"1"
    pm = str(preserveMarkdown).lower() in {"1", "true", "yes"}

    client = _ensure_client()
    if client is None:
        # No key or SDK import: fail-open (echo)
        if d == "in":
            return _ok_payload(original=raw, translated_in=raw, error="no OpenAI client (echo)")
        else:
            return _ok_payload(original=raw, translated_out=raw, error="no OpenAI client (echo)")

    # Build system prompt
    if d == "in":
        sys = (
            "Translate the user's message into English. "
            + ("Preserve original Markdown formatting, tables and code blocks. " if pm else "")
            + "Do not add explanations—return only the translated text."
        )
    else:
        tl = (body.targetLang or "").strip()
        if not tl:
            # Keep FE working; echo + note
            return _ok_payload(original=raw, translated_out=raw, error="targetLang is required for direction=out")
        sys = (
            f"Translate the user's message from English into {tl}. "
            + ("Preserve Markdown tables, headings and code fences. " if pm else "")
            + "Do not add explanations—return only the translated text."
        )

    # Call OpenAI, but never break the contract
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
            out = raw  # fail-open to echo

        if d == "in":
            return _ok_payload(original=raw, translated_in=out)
        else:
            return _ok_payload(original=raw, translated_out=out)

    except Exception as e:
        # Network/401/model errors → echo, but keep ok=True so FE UX doesn’t regress
        if d == "in":
            return _ok_payload(original=raw, translated_in=raw, error=f"{type(e).__name__}: {e}")
        else:
            return _ok_payload(original=raw, translated_out=raw, error=f"{type(e).__name__}: {e}")
