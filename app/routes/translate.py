# app/routes/translate.py
from __future__ import annotations
import os
from typing import Optional, Dict, Any
from fastapi import APIRouter, Query
from pydantic import BaseModel

# Optional import; route must work even if the SDK/key are missing
try:
    from openai import OpenAI  # SDK v1+
except Exception:
    OpenAI = None  # type: ignore

router = APIRouter(prefix="/api/translate", tags=["translate"])

_client: Optional["OpenAI"] = None
DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None  # required when direction=out

def _ensure_client() -> Optional["OpenAI"]:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    if OpenAI is None:
        return None
    global _client
    if _client is None:
        _client = OpenAI()
    return _client

def _ok_payload(*, original: str, translated_in: Optional[str]=None,
                translated_out: Optional[str]=None, error: Optional[str]=None) -> Dict[str, Any]:
    # Always HTTP 200; FE-safe shape
    out: Dict[str, Any] = {
        "ok": True,
        "text": original or "",
        "translatedInput": translated_in,
        "translatedOutput": translated_out,
    }
    if error:
        out["error"] = error
    return out

@router.post("")   # /api/translate
@router.post("/")  # /api/translate/ (avoid proxy/redirect quirks)
async def translate(
    body: TranslateBody,
    direction: str = Query(..., description="in|out"),
    preserveMarkdown: Optional[bool] = Query(False),
):
    raw = (body.text or "").strip()
    if not raw:
        return _ok_payload(original="")

    d = (direction or "").strip().lower()
    if d not in {"in", "out"}:
        # No 4xx; fail-open echo
        return _ok_payload(original=raw, translated_out=raw, error="invalid direction (expected 'in' or 'out')")

    pm = str(preserveMarkdown).lower() in {"1", "true", "yes"}

    client = _ensure_client()
    if client is None:
        # Echo on missing client/key/import; keep ok=True
        if d == "in":
            return _ok_payload(original=raw, translated_in=raw, error="no OpenAI client (echo)")
        else:
            return _ok_payload(original=raw, translated_out=raw, error="no OpenAI client (echo)")

    if d == "in":
        sys = (
            "Translate the user's message into English. "
            + ("Preserve original Markdown formatting, tables and code blocks. " if pm else "")
            + "Do not add explanations—return only the translated text."
        )
    else:
        tl = (body.targetLang or "").strip()
        if not tl:
            return _ok_payload(original=raw, translated_out=raw, error="targetLang is required for direction=out")
        sys = (
            f"Translate the user's message from English into {tl}. "
            + ("Preserve Markdown tables, headings and code fences. " if pm else "")
            + "Do not add explanations—return only the translated text."
        )

    try:
        rsp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": raw}],
            temperature=0,
        )
        out = (rsp.choices[0].message.content or "").strip() or raw
        if d == "in":
            return _ok_payload(original=raw, translated_in=out)
        else:
            return _ok_payload(original=raw, translated_out=out)
    except Exception as e:
        if d == "in":
            return _ok_payload(original=raw, translated_in=raw, error=f"{type(e).__name__}: {e}")
        else:
            return _ok_payload(original=raw, translated_out=raw, error=f"{type(e).__name__}: {e}")
