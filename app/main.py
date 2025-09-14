# app/main.py
from __future__ import annotations

import os
import time
import uuid
import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import (
    FastAPI, File, UploadFile, HTTPException, Form, Request, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    # Supabase client (optional, but expected in prod)
    from supabase import create_client, Client  # type: ignore
except Exception:  # pragma: no cover
    create_client = None  # type: ignore
    Client = None  # type: ignore

from app.gpt_extractor import extract_offer_from_pdf_bytes, ExtractionError

APP_NAME = "GPT Offer Extractor"
APP_VERSION = "0.7.0"

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# --- CORS (adjust in production) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your frontend origin(s) in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Supabase setup ---
_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
_OFFERS_TABLE = os.getenv("SUPABASE_TABLE", "offers")
_SHARE_TABLE = os.getenv("SUPABASE_SHARE_TABLE", "share_links")

_supabase: Optional[Client] = None
if _SUPABASE_URL and _SUPABASE_KEY and create_client is not None:
    try:
        _supabase = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    except Exception:
        _supabase = None

# --- In-memory helpers (okay on single dyno/Render instance) ---
_jobs: Dict[str, Dict[str, Any]] = {}              # job_id -> {total, done, errors}
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}      # filename -> payload (dev fallback)
_SHARES_FALLBACK: Dict[str, Dict[str, Any]] = {}   # token -> {payload, expires_at}

# =========================
# Health & root
# =========================
@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "model": os.getenv("GPT_MODEL", "gpt-4o-mini"),
        "supabase": bool(_supabase),
        "offers_table": _OFFERS_TABLE,
        "share_table": _SHARE_TABLE,
    }

@app.get("/")
def root():
    return {"ok": True}

# =========================
# Utilities
# =========================
def _inject_meta(payload: Dict[str, Any], *, insurer: str, company: str, insured_count: int, inquiry_id: str) -> None:
    """Attach meta so we can save it later (inquiry_id is optional and may be None)."""
    payload["insurer_hint"] = insurer or payload.get("insurer_hint") or "-"
    payload["company_name"] = company or payload.get("company_name") or "-"
    payload["employee_count"] = (
        insured_count if isinstance(insured_count, int) else payload.get("employee_count")
    )
    payload["inquiry_id"] = int(inquiry_id) if str(inquiry_id).isdigit() else None

def _rows_for_offers_table(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert one normalized extractor payload (with .programs list) into
    rows for public.offers, one row per program (your schema).
    """
    filename = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
    inquiry_id = payload.get("inquiry_id")
    company_name = payload.get("company_name")
    employee_count = payload.get("employee_count")

    rows: List[Dict[str, Any]] = []
    for prog in payload.get("programs", []) or []:
        rows.append({
            "insurer": prog.get("insurer"),
            "company_hint": payload.get("insurer_hint") or None,
            "program_code": prog.get("program_code"),
            "source": "api",
            "filename": filename,
            "inquiry_id": inquiry_id,  # may be NULL; that's fine
            "base_sum_eur": prog.get("base_sum_eur"),
            "premium_eur": prog.get("premium_eur"),
            "payment_method": prog.get("payment_method"),
            "features": prog.get("features") or {},
            "raw_json": payload,  # full payload for provenance/debug
            "status": "parsed",
            "error": None,
            "company_name": company_name,
            "employee_count": employee_count,
        })
    if not rows:
        rows.append({
            "insurer": payload.get("insurer_hint"),
            "company_hint": payload.get("insurer_hint"),
            "program_code": None,
            "source": "api",
            "filename": filename,
            "inquiry_id": inquiry_id,
            "base_sum_eur": None,
            "premium_eur": None,
            "payment_method": None,
            "features": {},
            "raw_json": payload,
            "status": "error",
            "error": "no programs",
            "company_name": company_name,
            "employee_count": employee_count,
        })
    return rows

def save_to_supabase(payload: Dict[str, Any]) -> None:
    """
    Insert one row per program into public.offers.
    No-op if Supabase is not configured.
    """
    if not _supabase:
        fname = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
        _LAST_RESULTS[fname] = payload
        return
    try:
        rows = _rows_for_offers_table(payload)
        _supabase.table(_OFFERS_TABLE).insert(rows).execute()
    except Exception as e:
        print(f"[warn] Supabase insert failed: {e}")

def _aggregate_offers_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build the FE shape:
    [{ source_file, programs: [{insurer, program_code, base_sum_eur, premium_eur, payment_method, features}, ...] }]
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        source_file = r.get("filename") or "-"
        program = {
            "insurer": r.get("insurer"),
            "program_code": r.get("program_code"),
            "base_sum_eur": r.get("base_sum_eur"),
            "premium_eur": r.get("premium_eur"),
            "payment_method": r.get("payment_method"),
            "features": r.get("features") or {},
        }
        if source_file not in grouped:
            grouped[source_file] = {
                "source_file": source_file,
                "programs": [],
                "inquiry_id": r.get("inquiry_id"),
            }
        grouped[source_file]["programs"].append(program)
    return list(grouped.values())

def _offers_by_filenames(doc_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Query offers by filename list and return FE-friendly grouped shape.
    """
    if not doc_ids:
        return []
    if _supabase:
        try:
            res = (
                _supabase.table(_OFFERS_TABLE)
                .select("*")
                .in_("filename", doc_ids)
                .order("created_at", desc=False)
                .execute()
            )
            rows = res.data or []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Supabase error: {e}")
    else:
        # dev fallback: group from memory
        rows = []
        for p in _LAST_RESULTS.values():
            for r in _rows_for_offers_table(p):
                if r.get("filename") in doc_ids:
                    rows.append(r)
    return _aggregate_offers_rows(rows)

# =========================
# Extract endpoints
# =========================
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
        payload = extract_offer_from_pdf_bytes(data, document_id=file.filename or "uploaded.pdf")
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
        save_to_supabase(payload)

        fname = payload.get("document_id") or file.filename or "uploaded.pdf"
        _LAST_RESULTS[fname] = payload  # dev fallback

        payload["_timings"] = {"total_s": round(time.monotonic() - t0, 3)}
        return JSONResponse(payload)
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

@app.post("/extract/multiple")
async def extract_multiple(
    files: List[Upload
