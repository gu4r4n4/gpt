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
APP_VERSION = "0.6.0"

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
_jobs: Dict[str, Dict[str, Any]] = {}              # job_id -> {inquiry_id,total,done,errors}
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}      # filename -> payload (dev fallback)
_SHARES_FALLBACK: Dict[str, Dict[str, Any]] = {}   # token -> {inquiry_id,payload}

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
    """Attach meta so we can save it later."""
    payload["insurer_hint"] = insurer or payload.get("insurer_hint") or "-"
    payload["company_name"] = company or payload.get("company_name") or "-"
    payload["employee_count"] = (
        insured_count if isinstance(insured_count, int) else payload.get("employee_count")
    )
    payload["inquiry_id"] = int(inquiry_id) if str(inquiry_id).isdigit() else None

def _rows_for_offers_table(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert one normalized extractor payload (with .programs list) into
    rows for public.offers, one row per program (schema you shared).
    """
    filename = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
    inquiry_id = payload.get("inquiry_id")
    company_name = payload.get("company_name")
    employee_count = payload.get("employee_count")

    rows: List[Dict[str, Any]] = []
    for prog in payload.get("programs", []) or []:
        rows.append({
            "insurer": prog.get("insurer"),
            "company_hint": payload.get("insurer_hint") or None,  # optional helper
            "program_code": prog.get("program_code"),
            "source": "api",
            "filename": filename,
            "inquiry_id": inquiry_id,
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
    # If no programs (error case), still store one row with error/status
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
    Insert one row per program into public.offers (your schema).
    No-op if Supabase is not configured.
    """
    if not _supabase:
        # dev fallback, just keep in memory
        fname = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
        _LAST_RESULTS[fname] = payload
        return
    try:
        rows = _rows_for_offers_table(payload)
        # Supabase Python client accepts list of dicts
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
    files: List[UploadFile] = File(...),
    insurer: str = Form(""),
    company: str = Form(""),
    insured_count: int = Form(0),
    inquiry_id: str = Form(""),
):
    """Sequential (blocking) version."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    results: List[Dict[str, Any]] = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            results.append({"document_id": f.filename, "error": "Unsupported file type (only PDF)"})
            continue
        try:
            data = await f.read()
            payload = extract_offer_from_pdf_bytes(data, document_id=f.filename or "uploaded.pdf")
            _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
            save_to_supabase(payload)
            results.append(payload)

            fname = payload.get("document_id") or f.filename or "uploaded.pdf"
            _LAST_RESULTS[fname] = payload
        except ExtractionError as e:
            results.append({"document_id": f.filename, "error": str(e)})
        except Exception as e:
            results.append({"document_id": f.filename, "error": f"Unexpected error: {e}"})
    return JSONResponse(results)

@app.post("/extract/multiple-async", status_code=202)
async def extract_multiple_async(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    insurer: str = Form(""),
    company: str = Form(""),
    insured_count: int = Form(0),
    inquiry_id: str = Form(""),
):
    """
    Asynchronous version: returns a job_id immediately.
    Use GET /jobs/{job_id} for progress and GET /offers/by-inquiry/{inquiry_id} to read results as they arrive.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    job_id = str(uuid.uuid4())
    job_inquiry_id = int(inquiry_id) if str(inquiry_id).isdigit() else None
    _jobs[job_id] = {"inquiry_id": job_inquiry_id, "total": len(files), "done": 0, "errors": []}

    # Read file bytes now and enqueue background tasks
    for f in files:
        filename = f.filename or "uploaded.pdf"
        data = await f.read()
        background_tasks.add_task(
            _process_pdf_bytes,
            data, filename, insurer, company,
            int(insured_count) if insured_count else 0,
            job_inquiry_id, job_id,
        )

    return {"job_id": job_id, "accepted": len(files), "inquiry_id": job_inquiry_id}

def _process_pdf_bytes(
    data: bytes,
    filename: str,
    insurer: str,
    company: str,
    insured_count: int,
    inquiry_id: Optional[int],
    job_id: str,
):
    try:
        payload = extract_offer_from_pdf_bytes(data, document_id=filename)
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=str(inquiry_id or ""))
        save_to_supabase(payload)
        _LAST_RESULTS[filename] = payload  # dev fallback
    except Exception as e:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec["errors"].append({"document_id": filename, "error": str(e)})
    finally:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec["done"] += 1

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job

# =========================
# Read offers (for UI & share pages)
# =========================
@app.get("/offers/by-inquiry/{inquiry_id}")
def offers_by_inquiry(inquiry_id: int):
    """
    Returns FE-friendly shape grouped by filename.
    """
    if _supabase:
        try:
            res = (
                _supabase.table(_OFFERS_TABLE)
                .select("*")
                .eq("inquiry_id", inquiry_id)
                .order("created_at", desc=False)
                .execute()
            )
            rows = res.data or []
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Supabase error: {e}")
    else:
        # dev fallback: group from in-memory last results (no true inquiry mapping)
        rows = []
        for p in _LAST_RESULTS.values():
            for r in _rows_for_offers_table(p):
                if r.get("inquiry_id") == inquiry_id:
                    rows.append(r)

    return _aggregate_offers_rows(rows)

@app.get("/public/offers/by-inquiry/{inquiry_id}")
def public_offers_by_inquiry(inquiry_id: int):
    return offers_by_inquiry(inquiry_id)

# =========================
# Share links (public.share_links)
# =========================
class ShareCreateBody(BaseModel):
    inquiry_id: int = Field(..., description="Inquiry id to share")
    title: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    expires_in_hours: Optional[int] = Field(720, description="Defaults to 30 days")

def _gen_token() -> str:
    return secrets.token_urlsafe(16)

@app.post("/shares")
def create_share(body: ShareCreateBody, request: Request):
    token = _gen_token()
    expires_at = (
        datetime.utcnow() + timedelta(hours=body.expires_in_hours or 720)
        if (body.expires_in_hours or 0) > 0
        else None
    )
    row = {
        "token": token,
        "inquiry_id": body.inquiry_id,
        "payload": body.payload or {"mode": "inquiry-live"},
        "expires_at": expires_at.isoformat() + "Z" if expires_at else None,
    }

    if _supabase:
        try:
            _supabase.table(_SHARE_TABLE).insert(row).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Share create failed: {e}")
    else:
        _SHARES_FALLBACK[token] = row

    # Build a copyable URL (prefer SHARE_BASE_URL env for your public UI)
    base = os.getenv("SHARE_BASE_URL")
    if base:
        url = f"{base.rstrip('/')}/share/{token}"
    else:
        try:
            url = str(request.url_for("get_share", token=token))
        except Exception:
            url = f"/shares/{token}"

    return {"ok": True, "token": token, "url": url, "inquiry_id": body.inquiry_id, "title": body.title}

@app.get("/shares/{token}", name="get_share")
def get_share(token: str):
    """
    Returns the share record and current offers for its inquiry.
    The viewer can poll this endpoint or /public/offers/by-inquiry/{id}.
    """
    share: Optional[Dict[str, Any]] = None
    if _supabase:
        try:
            res = _supabase.table(_SHARE_TABLE).select("*").eq("token", token).limit(1).execute()
            rows = res.data or []
            if rows:
                share = rows[0]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Share fetch failed: {e}")
    else:
        share = _SHARES_FALLBACK.get(token)

    if not share:
        raise HTTPException(status_code=404, detail="Share token not found")

    inquiry_id = share.get("inquiry_id")
    offers = offers_by_inquiry(int(inquiry_id)) if inquiry_id is not None else []

    return {"ok": True, "token": token, "inquiry_id": inquiry_id, "payload": share.get("payload") or {}, "offers": offers}
