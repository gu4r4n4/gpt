# app/routes/translate.py
from __future__ import annotations
import os
from typing import Optional, Literal, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from openai import OpenAI

router = APIRouter(prefix="/api/translate", tags=["translate"])

# Client is created lazily so container boots even if key is missing
_client: Optional[OpenAI] = None
DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None  # required only when direction=out

def _ensure_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            # We don't raise here—caller handles "no key" by echoing text back
            raise RuntimeError("OPENAI_API_KEY missing")
        _client = OpenAI()
    return _client

def _safe_ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure a very stable response shape so the FE never crashes.
    """
    base = {
        "ok": True,
        "text": payload.get("text") or "",
        # Both keys below so either FE path can consume it
        "translatedInput": payload.get("translatedInput"),
        "translatedOutput": payload.get("translatedOutput"),
    }
    if "error" in payload and payload["error"]:
        base["ok"] = False
        base["error"] = str(payload["error"])
    return base

@router.post("")
async def translate(
    body: TranslateBody,
    direction: Literal["in","out"] = Query(..., pattern="^(in|out)$"),
    preserveMarkdown: bool = Query(False),
):
    """
    POST /api/translate?direction=in|out[&preserveMarkdown=true]
    - direction=in  : user language -> English
    - direction=out : English -> targetLang (body.targetLang required)

    Always returns 200 with shape:
      {
        ok: boolean,
        text: string,                 # final translated text (or original on fail-open)
        translatedInput?: string,     # set for direction=in
        translatedOutput?: string,    # set for direction=out
        error?: string
      }
    """
    raw = (body.text or "").strip()
    if not raw:
        return _safe_ok({"text": ""})

    # No key? Fail-open: echo original so UI never blanks.
    api_key_present = bool(os.getenv("OPENAI_API_KEY"))
    if not api_key_present:
        if direction == "in":
            return _safe_ok({"text": raw, "translatedInput": raw})
        else:
            return _safe_ok({"text": raw, "translatedOutput": raw})

    # Build system prompt
    if direction == "in":
        sys = (
            "Translate the user's message into English. "
            + ("Preserve original Markdown formatting, tables and code blocks. " if preserveMarkdown else "")
            + "Do not add explanations—return only the translated text."
        )
    else:
        tl = (body.targetLang or "").strip()
        if not tl:
            # Still return 200 with ok:false so FE keeps going
            return _safe_ok({"text": raw, "translatedOutput": raw, "error": "targetLang is required for direction=out"})
        sys = (
            f"Translate the user's message from English into {tl}. "
            + ("Preserve Markdown tables, headings and code fences. " if preserveMarkdown else "")
            + "Do not add explanations—return only the translated text."
        )

    # Call OpenAI – wrapped so *any* exception still returns 200 with fallback text
    try:
        client = _ensure_client()
        rsp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": raw},
            ],
            temperature=0,
            # max_tokens left unset so model can return full text (Markdown tables etc.)
        )
        out = (rsp.choices[0].message.content or "").strip()
        if not out:
            out = raw  # fail-open if model returns empty

        if direction == "in":
            return _safe_ok({"text": out, "translatedInput": out})
        else:
            return _safe_ok({"text": out, "translatedOutput": out})

    except Exception as e:
        # Fail-open on any OpenAI/network error
        if direction == "in":
            return _safe_ok({"text": raw, "translatedInput": raw, "error": f"{type(e).__name__}: {e}"})
        else:
            return _safe_ok({"text": raw, "translatedOutput": raw, "error": f"{type(e).__name__}: {e}"})
