# app/routes/translate.py
from __future__ import annotations
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from openai import OpenAI

router = APIRouter(prefix="/api/translate", tags=["translate"])

client = None
if os.getenv("OPENAI_API_KEY"):
    client = OpenAI()

DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None  # used when direction=out

def _ensure_client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
    global client
    if client is None:
        client = OpenAI()
    return client

def _translate(system: str, text: str) -> str:
    c = _ensure_client()
    rsp = c.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        temperature=0,
    )
    out = rsp.choices[0].message.content or ""
    return out.strip()

@router.post("")
async def translate(
    payload: TranslateBody,
    direction: str = Query(..., pattern="^(in|out)$"),   # pattern instead of regex
    preserveMarkdown: bool = Query(False)
):
    """
    POST /api/translate?direction=in|out[&preserveMarkdown=true]
    - direction=in  : user language -> English
    - direction=out : English -> targetLang
    Returns both shape-specific keys (translatedInput/translatedOutput) and a generic 'text'.
    """
    text = (payload.text or "").strip()
    if not text:
        return {"text": ""}

    # Fail-open (no key): just echo back
    if not os.getenv("OPENAI_API_KEY"):
        if direction == "in":
            return {"translatedInput": text, "text": text}
        else:
            return {"translatedOutput": text, "text": text}

    try:
        if direction == "in":
            sys = (
                "Translate the user's message into English. "
                + ("Preserve original Markdown formatting, tables and code blocks. " if preserveMarkdown else "")
                + "Return only the translated text."
            )
            out = _translate(sys, text)
            return {"translatedInput": out, "text": out}
        else:
            tl = (payload.targetLang or "").strip()
            if not tl:
                raise HTTPException(status_code=400, detail="targetLang is required for direction=out")
            sys = (
                f"Translate the user's message from English into {tl}. "
                + ("Preserve Markdown tables, headings and code fences. " if preserveMarkdown else "")
                + "Return only the translated text."
            )
            out = _translate(sys, text)
            return {"translatedOutput": out, "text": out}

    except HTTPException:
        raise
    except Exception as e:
        # Fail-open so UI never blanks
        print(f"[translate] error: {type(e).__name__}: {e}")
        if direction == "in":
            return {"translatedInput": text, "text": text}
        else:
            return {"translatedOutput": text, "text": text}
