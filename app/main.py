# app/main.py
from __future__ import annotations
import os
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    # Optional Supabase client
    from supabase import create_client, Client  # type: ignore
except Exception:  # pragma: no cover
    create_client = None  # type: ignore
    Client = None  # type: ignore

from app.gpt_extractor import extract_offer_from_pdf_bytes, ExtractionError

APP_NAME = "GPT Offer Extractor"
APP_VERSION = "0.4.1"

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# --- CORS (adjust as needed for lovable.dev) ---
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
        }).execute()
    except Exception as e:
        print(f"[warn] Supabase insert failed: {e}")


# --- Health ---
@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "model": os.getenv("GPT_MODEL", "gpt-4o-mini"),
        "supabase": bool(_supabase),
    }


def _inject_meta(payload: Dict[str, Any], *, insurer: str, company: str, insured_count: int, inquiry_id: str) -> None:
    """Add meta fields to the normalized payload without changing the extractor signature."""
    payload["insurer"] = insurer if insurer else payload.get("insurer", "-")
    payload["company"] = company if company else payload.get("company", "-")
    payload["insured_count"] = insured_count if isinstance(insured_count, int) else payload.get("insured_count", "-")
    payload["inquiry_id"] = inquiry_id if inquiry_id else payload.get("inquiry_id", "-")


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
    try:
        payload = extract_offer_from_pdf_bytes(data, document_id=file.filename or "uploaded.pdf")
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
        save_to_supabase(payload)
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
        except ExtractionError as e:
            results.append({"document_id": f.filename, "error": str(e)})
        except Exception as e:
            results.append({"document_id": f.filename, "error": f"Unexpected error: {e}"})

    return JSONResponse(results)
