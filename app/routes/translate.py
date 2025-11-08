# app/routes/translate.py
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
import os, asyncio
from openai import OpenAI, APIError, RateLimitError, APITimeoutError

router = APIRouter(prefix="/api/translate", tags=["translate"])
_client: Optional[OpenAI] = None
DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None

def _client_ok() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client

async def _safe_translate(system: str, text: str, *, timeout_s: float = 20.0) -> str:
    if not _client_ok() or not text.strip():
        return text
    client = _get_client()

    async def _call():
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role":"system","content":system},{"role":"user","content":text}],
            temperature=0,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or text

    for attempt in range(2):
        try:
            return await asyncio.wait_for(_call(), timeout=timeout_s)
        except (RateLimitError, APITimeoutError, APIError):
            if attempt == 0:
                await asyncio.sleep(0.6)
            else:
                return text
        except Exception:
            return text
    return text

def _detect_lang(text: str) -> str:
    """Use a simple heuristic for Latvian detection - presence of diacritics."""
    latvian_chars = set('āčēģīķļņōŗšūžĀČĒĢĪĶĻŅŌŖŠŪŽ')
    text_chars = set(text)
    # If text contains Latvian diacritics, consider it Latvian
    if any(c in latvian_chars for c in text_chars):
        return "lv"
    return "unknown"

@router.post("")
async def translate(
    payload: TranslateBody,
    direction: str = Query(..., pattern="^(in|out)$"),
    preserveMarkdown: bool = Query(False),
):
    text = (payload.text or "").strip()

    # Auto-detect Latvian and bypass translation
    detected_lang = _detect_lang(text)
    if detected_lang == "lv":
        if direction == "in":
            return {"translatedInput": text, "detected_lang": "lv", "translated": False}
        # For outbound, if target is also Latvian, bypass
        if (payload.targetLang or "").lower() in ("lv", "lav", "latvian"):
            return {"translatedOutput": text, "detected_lang": "lv", "translated": False}

    # No key? Fail-open echo (keeps your FE logic working)
    if not _client_ok():
        return {"translatedInput": text} if direction == "in" else {"translatedOutput": text}

    if direction == "in":
        sys = "Translate into English. " + ("Preserve Markdown tables/code. " if preserveMarkdown else "") + "Return only translated text."
        out = await _safe_translate(sys, text)
        return {"translatedInput": out}

    # out: English -> target
    tl = (payload.targetLang or "").strip()
    if not tl:
        # never throw; handshake stays stable
        return {"translatedOutput": text}
    sys = f"Translate from English into {tl}. " + ("Preserve Markdown tables/headings/code fences. " if preserveMarkdown else "") + "Return only translated text."
    out = await _safe_translate(sys, text)
    return {"translatedOutput": out}
