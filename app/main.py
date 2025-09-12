# app/main.py
from __future__ import annotations
import os
import time
import secrets
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    # Optional Supabase client
    from supabase import create_client, Client  # type: ignore
except Exception:  # pragma: no cover
    create_client = None  # type: ignore
    Client = None  # type: ignore

from app.gpt_extractor import extract_offer_from_pdf_bytes, ExtractionError

APP_NAME = "GPT Offer Extractor"
APP_VERSION = "0.5.0"

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# --- CORS (adjust in production) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your frontend origin(s) in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Supabase helpers (optional) ---
_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
_SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "offers")
_supabase: Optional[Client] = None

if _SUPABASE_URL and _SUPABASE_KEY and create_client is not None:
    try:
        _supabase = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    except Exception:
        _supabase = None

def save_to_supabase(payload: Dict[str, Any]) -> None:
    """Upsert normalized payload into Supabase. No-op if client not configured."""
    if not _supabase:
        return
    try:
        _supabase.table(_SUPABASE_TABLE).insert({
            "document_id": payload.get("document_id"),
            "insurer_code": payload.get("insurer_code"),
            "programs": payload.get("programs"),
            "warnings": payload.get("warnings", []),
            # user-supplied meta (optional)
            "insurer": payload.get("insurer"),
            "company": payload.get("company"),
            "insured_count": payload.get("insured_count"),
            "inquiry_id": payload.get("inquiry_id"),
            # share_token can be set later by /share
        }).execute()
    except Exception as e:
        print(f"[warn] Supabase insert failed: {e}")

# --- Health & root ---
@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "model": os.getenv("GPT_MODEL", "gpt-4o-mini"),
        "supabase": bool(_supabase),
    }

@app.get("/")  # optional, avoids 404 on "/"
def root():
    return {"ok": True}

# --- meta helper ---
def _inject_meta(payload: Dict[str, Any], *, insurer: str, company: str, insured_count: int, inquiry_id: str) -> None:
    payload["insurer"] = insurer if insurer else payload.get("insurer", "-")
    payload["company"] = company if company else payload.get("company", "-")
    payload["insured_count"] = insured_count if isinstance(insured_count, int) else payload.get("insured_count", "-")
    payload["inquiry_id"] = inquiry_id if inquiry_id else payload.get("inquiry_id", "-")

# --- Local dev fallback store (no Supabase) ---
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}   # document_id -> payload
_SHARES: Dict[str, List[str]] = {}              # token -> [document_id]

# --- Single PDF extraction ---
@app.post("/extract/pdf")
async def extract_pdf(
    file: UploadFile = File(...),
    insurer: str = Form(""),
    company: str = Form(""),
    insured_count: int = Form(0),
    inquiry_id: str = Form(""),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    data = await file.read()
    t0 = time.monotonic()
    try:
        t_extract0 = time.monotonic()
        payload = extract_offer_from_pdf_bytes(data, document_id=file.filename or "uploaded.pdf")
        t_extract1 = time.monotonic()

        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
        save_to_supabase(payload)

        # local fallback cache
        doc_id = payload.get("document_id") or file.filename or "uploaded.pdf"
        _LAST_RESULTS[doc_id] = payload

        timings = {
            "total_s": round(time.monotonic() - t0, 3),
            "extract_s": round(t_extract1 - t_extract0, 3),
            "save_s": 0.0,
        }
        payload["_timings"] = timings
        return JSONResponse(payload)
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

# --- Multiple PDFs extraction (sequential) ---
@app.post("/extract/multiple")
async def extract_multiple(
    files: List[UploadFile] = File(...),
    insurer: str = Form(""),
    company: str = Form(""),
    insured_count: int = Form(0),
    inquiry_id: str = Form(""),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    results: List[Dict[str, Any]] = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            results.append({
                "document_id": f.filename,
                "error": "Unsupported file type (only PDF)",
            })
            continue
        try:
            data = await f.read()
            payload = extract_offer_from_pdf_bytes(data, document_id=f.filename or "uploaded.pdf")
            _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
            save_to_supabase(payload)
            results.append(payload)

            # local fallback cache
            doc_id = payload.get("document_id") or f.filename or "uploaded.pdf"
            _LAST_RESULTS[doc_id] = payload
        except ExtractionError as e:
            results.append({"document_id": f.filename, "error": str(e)})
        except Exception as e:
            results.append({"document_id": f.filename, "error": f"Unexpected error: {e}"})

    return JSONResponse(results)

# =========================
# Sharing API
# =========================

class ShareCreateBody(BaseModel):
    document_ids: List[str]
    title: Optional[str] = None  # optional, if you want to label a share

def _gen_token() -> str:
    return secrets.token_urlsafe(12)

@app.post("/share")
async def create_share(body: ShareCreateBody, request: Request):
    """
    Given a list of document_ids already saved in the offers table,
    assign a new share_token to them and return a link.
    """
    if not body.document_ids:
        raise HTTPException(status_code=400, detail="document_ids required")

    token = _gen_token()

    if _supabase:
        try:
            # attach token to all listed docs
            _supabase.table(_SUPABASE_TABLE).update({"share_token": token}).in_("document_id", body.document_ids).execute()
            # fetch the grouped items back
            data = _supabase.table(_SUPABASE_TABLE).select("*").eq("share_token", token).execute()
            items = data.data or []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Share create failed: {e}")
    else:
        # local fallback (non-persistent)
        _SHARES[token] = body.document_ids
        items = [ _LAST_RESULTS[d] for d in body.document_ids if d in _LAST_RESULTS ]

    # Build a copyable URL to this API. If you have a public UI for shares, set SHARE_BASE_URL.
    base = os.getenv("SHARE_BASE_URL")
    if base:
        url = f"{base.rstrip('/')}/share/{token}"
    else:
        # falls back to API URL
        try:
            url = str(request.url_for("get_share", token=token))
        except Exception:
            url = f"/share/{token}"

    return {
        "ok": True,
        "token": token,
        "count": len(items),
        "url": url,
        "title": body.title,
        "items_preview": items[:3],  # small preview to confirm
    }

@app.get("/share/{token}", name="get_share")
def get_share(token: str):
    """
    Return all offers linked to this share token.
    """
    if _supabase:
        try:
            data = _supabase.table(_SUPABASE_TABLE).select("*").eq("share_token", token).execute()
            items = data.data or []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Share fetch failed: {e}")
    else:
        # local fallback
        doc_ids = _SHARES.get(token, [])
        items = [ _LAST_RESULTS[d] for d in doc_ids if d in _LAST_RESULTS ]

    if not items:
        raise HTTPException(status_code=404, detail="Share token not found")

    return {
        "ok": True,
        "token": token,
        "items": items,
    }
