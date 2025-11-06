# app/routes/translate.py
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import os
from openai import OpenAI

router = APIRouter(prefix="/api/translate", tags=["translate"])

client = None
if os.getenv("OPENAI_API_KEY"):
    client = OpenAI()

DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")

class TranslateBody(BaseModel):
    text: str
    targetLang: Optional[str] = None  # for direction=out

def _ensure_client():
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
    global client
    if client is None:
        client = OpenAI()
    return client

def _translate(system: str, text: str) -> str:
    c = _ensure_client()
    # Use a small fast model; we just need plain text back
    # Responses API is the current SDK path; the text output is easy to get.
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
    direction: str = Query(..., regex="^(in|out)$"),
    preserveMarkdown: bool = Query(False)
):
    """
    direction=in  : user language -> English   (payload.text)
    direction=out : English -> target language (payload.text, payload.targetLang)
    preserveMarkdown is a hint; we instruct the model accordingly.
    Returns JSON with { translatedInput?/translatedOutput?/text } for flexible FE parsing.
    """
    text = (payload.text or "").strip()
    if not text:
        return {"text": ""}

    # Fail-open if no key: just echo back (prevents FE crash/blank)
    if not os.getenv("OPENAI_API_KEY"):
        # keep the shapes your FE accepts
        if direction == "in":
            return {"translatedInput": text}
        else:
            return {"translatedOutput": text}

    try:
        if direction == "in":
            # to English
            if preserveMarkdown:
                sys = (
                    "Translate the user's message into English. "
                    "Preserve original Markdown formatting, tables and code blocks. "
                    "Do not add explanations—return only the translated text."
                )
            else:
                sys = (
                    "Translate the user's message into English. "
                    "Return only the translated text."
                )
            out = _translate(sys, text)
            return {"translatedInput": out}

        else:
            # out: to target
            tl = (payload.targetLang or "").strip().lower()
            if not tl:
                raise HTTPException(status_code=400, detail="targetLang is required for direction=out")

            md_clause = "Preserve Markdown tables, headings and code fences. " if preserveMarkdown else ""
            sys = (
                f"Translate the user's message from English into {tl}. "
                f"{md_clause}"
                "Do not add explanations—return only the translated text."
            )
            out = _translate(sys, text)
            return {"translatedOutput": out}

    except HTTPException:
        raise
    except Exception as e:
        # Fail-open on errors — return original text so UI never blanks
        print(f"[translate] error: {type(e).__name__}: {e}")
        if direction == "in":
            return {"translatedInput": text}
        else:
            return {"translatedOutput": text}
