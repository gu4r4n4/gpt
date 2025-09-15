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
    # Supabase client (optional, expected in prod with RLS configured)
    from supabase import create_client, Client  # type: ignore
except Exception:  # pragma: no cover
    create_client = None  # type: ignore
    Client = None  # type: ignore

from app.gpt_extractor import extract_offer_from_pdf_bytes, ExtractionError

APP_NAME = "GPT Offer Extractor"
APP_VERSION = "0.9.1"

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# -------------------------------
# CORS (adjust for production)
# -------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your frontend origin(s) in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# Supabase setup
# -------------------------------
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

# -------------------------------
# In-memory helpers (single process)
# -------------------------------
# job_id -> { total, done, errors, docs: [doc_ids...] }
_jobs: Dict[str, Dict[str, Any]] = {}
# doc_id -> payload (dev fallback if no Supabase)
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}
# token -> stored share row (dev fallback if no Supabase)
_SHARES_FALLBACK: Dict[str, Dict[str, Any]] = {}

# -------------------------------
# Health & root
# -------------------------------
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

# -------------------------------
# Utilities
# -------------------------------
def _inject_meta(payload: Dict[str, Any], *, insurer: str, company: str, insured_count: int, inquiry_id: str) -> None:
    """Attach meta to payload; inquiry_id is optional/nullable."""
    payload["insurer_hint"] = insurer or payload.get("insurer_hint") or "-"
    payload["company_name"] = company or payload.get("company_name") or "-"
    payload["employee_count"] = (
        insured_count if isinstance(insured_count, int) else payload.get("employee_count")
    )
    # may be None — NOT required anywhere
    payload["inquiry_id"] = int(inquiry_id) if str(inquiry_id).isdigit() else None

def _rows_for_offers_table(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert one normalized extractor payload (with .programs list)
    into rows for public.offers (one row per program).
    Uses payload['document_id'] as the 'filename' (unique per batch/job).
    """
    doc_id = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
    inquiry_id = payload.get("inquiry_id")  # may be None
    company_name = payload.get("company_name")
    employee_count = payload.get("employee_count")
    hint = payload.get("insurer_hint") or None

    rows: List[Dict[str, Any]] = []
    for prog in payload.get("programs", []) or []:
        insurer_val = prog.get("insurer") or hint  # fallback to user-selected hint
        rows.append({
            "insurer": insurer_val,
            "company_hint": hint,             # store the dropdown hint
            "program_code": prog.get("program_code"),
            "source": "api",
            "filename": doc_id,               # UNIQUE per run
            "inquiry_id": inquiry_id,         # nullable
            "base_sum_eur": prog.get("base_sum_eur"),
            "premium_eur": prog.get("premium_eur"),
            "payment_method": prog.get("payment_method"),
            "features": prog.get("features") or {},
            "raw_json": payload,              # provenance/debug
            "status": "parsed",
            "error": None,
            "company_name": company_name,
            "employee_count": employee_count,
        })
    # If no programs, store an error row so the FE can show something
    if not rows:
        rows.append({
            "insurer": hint,
            "company_hint": hint,
            "program_code": None,
            "source": "api",
            "filename": doc_id,
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
    No-op if Supabase is not configured (stores to in-memory fallback).
    """
    if not _supabase:
        doc_id = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
        _LAST_RESULTS[doc_id] = payload
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

def _offers_by_document_ids(doc_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Resolve offers by exact document_id list and return FE-friendly grouped shape.
    We store document_id into 'filename' column, so we filter by filename IN ...
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
        rows = []
        for p in _LAST_RESULTS.values():
            for r in _rows_for_offers_table(p):
                if r.get("filename") in doc_ids:
                    rows.append(r)
    return _aggregate_offers_rows(rows)

# -------------------------------
# Extract endpoints
# -------------------------------
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

    # For single-file, generate a unique doc_id too (no collisions with prior runs)
    batch_id = str(uuid.uuid4())
    doc_id = f"{batch_id}::1::{file.filename or 'uploaded.pdf'}"

    data = await file.read()
    t0 = time.monotonic()
    try:
        payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
        save_to_supabase(payload)

        _LAST_RESULTS[doc_id] = payload  # dev fallback

        payload["_timings"] = {"total_s": round(time.monotonic() - t0, 3)}
        # Return doc_id so FE can read via /offers/by-documents if it wants
        return JSONResponse({"document_id": doc_id, "result": payload})
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

@app.post("/extract/multiple")
async def extract_multiple(
    request: Request,
    # make files optional to avoid FastAPI's pre-validation 422
    files: Optional[List[UploadFile]] = File(None),
    insurers: Optional[List[str]] = Form(None),  # repeated field aligned to files
    company: str = Form(""),
    insured_count: int = Form(0),
    inquiry_id: str = Form(""),
    insurer: str = Form(""),  # fallback single hint (if FE still sends only one)
):
    """Sequential (blocking) version — uses unique doc_ids and per-file insurer hints."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # Fallback for legacy FE: read file_{i}_insurer if needed
    if insurers is None:
        form = await request.form()
        insurers = []
        for i, _ in enumerate(files):
            insurers.append(form.get(f"file_{i}_insurer", ""))

    # Normalize insurers list to files length (fallback to single 'insurer' if provided)
    insurers = insurers or []
    if len(insurers) < len(files):
        pad_with = insurer or ""
        insurers += [pad_with] * (len(files) - len(insurers))
    elif len(insurers) > len(files):
        insurers = insurers[:len(files)]

    batch_id = str(uuid.uuid4())
    results: List[Dict[str, Any]] = []
    doc_ids: List[str] = []

    for idx, f in enumerate(files, start=1):
        if not f.filename.lower().endswith(".pdf"):
            results.append({"document_id": f.filename, "error": "Unsupported file type (only PDF)"})
            continue
        try:
            doc_id = f"{batch_id}::{idx}::{f.filename or 'uploaded.pdf'}"
            data = await f.read()
            payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
            _inject_meta(payload, insurer=insurers[idx - 1] or "", company=company, insured_count=insured_count, inquiry_id=inquiry_id)
            save_to_supabase(payload)
            results.append(payload)
            doc_ids.append(doc_id)
            _LAST_RESULTS[doc_id] = payload
        except ExtractionError as e:
            results.append({"document_id": f.filename, "error": str(e)})
        except Exception as e:
            results.append({"document_id": f.filename, "error": f"Unexpected error: {e}"})
    # Return both the legacy results and the doc_ids (so FE can switch to doc-based polling)
    return JSONResponse({"documents": doc_ids, "results": results})

@app.post("/extract/multiple-async", status_code=202)
async def extract_multiple_async(
    request: Request,
    background_tasks: BackgroundTasks,
    # make files optional to avoid FastAPI's pre-validation 422
    files: Optional[List[UploadFile]] = File(None),
    insurers: Optional[List[str]] = Form(None),  # repeated field aligned to files
    company: str = Form(""),
    insured_count: int = Form(0),
    inquiry_id: str = Form(""),
    insurer: str = Form(""),  # fallback single hint (if FE still sends only one)
):
    """
    Asynchronous version: returns a job_id and a list of unique document_ids immediately.
    FE should poll:
      - GET /jobs/{job_id} for progress (optional; may 404 if the process restarts), and
      - POST /offers/by-documents with the returned document_ids to read results as they arrive.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # Fallback for legacy FE: read file_{i}_insurer if needed
    if insurers is None:
        form = await request.form()
        insurers = []
        for i, _ in enumerate(files):
            insurers.append(form.get(f"file_{i}_insurer", ""))

    # Normalize insurers list to files length (fallback to single 'insurer' if provided)
    insurers = insurers or []
    if len(insurers) < len(files):
        pad_with = insurer or ""
        insurers += [pad_with] * (len(files) - len(insurers))
    elif len(insurers) > len(files):
        insurers = insurers[:len(files)]

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"total": len(files), "done": 0, "errors": [], "docs": []}

    # Pre-assign unique doc_ids and enqueue background tasks
    doc_ids: List[str] = []
    for idx, f in enumerate(files, start=1):
        filename = f.filename or "uploaded.pdf"
        doc_id = f"{job_id}::{idx}::{filename}"
        doc_ids.append(doc_id)
        data = await f.read()
        background_tasks.add_task(
            _process_pdf_bytes,
            data=data,
            doc_id=doc_id,
            insurer=insurers[idx - 1] or "",
            company=company,
            insured_count=int(insured_count) if insured_count else 0,
            job_id=job_id,
            inquiry_id_raw=str(inquiry_id or ""),  # optional, may be empty
        )

    _jobs[job_id]["docs"] = doc_ids
    return {"job_id": job_id, "accepted": len(files), "documents": doc_ids}

def _process_pdf_bytes(
    data: bytes,
    doc_id: str,
    insurer: str,
    company: str,
    insured_count: int,
    job_id: str,
    inquiry_id_raw: str,
):
    try:
        payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id_raw)
        save_to_supabase(payload)
        _LAST_RESULTS[doc_id] = payload  # dev fallback
    except Exception as e:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec["errors"].append({"document_id": doc_id, "error": str(e)})
    finally:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec["done"] += 1

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        # On multi-worker or restarts this can be missing. FE should keep polling by documents.
        raise HTTPException(status_code=404, detail="job not found")
    return job

# -------------------------------
# Read offers (document-id based)
# -------------------------------
class DocsBody(BaseModel):
    document_ids: List[str]

@app.post("/offers/by-documents")
def offers_by_documents(body: DocsBody):
    return _offers_by_document_ids(body.document_ids)

@app.get("/offers/by-job/{job_id}")
def offers_by_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    doc_ids = job.get("docs") or []
    return _offers_by_document_ids(doc_ids)

# (Optional legacy; still available)
@app.get("/offers/by-inquiry/{inquiry_id}")
def offers_by_inquiry(inquiry_id: int):
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
        rows = []
        for p in _LAST_RESULTS.values():
            for r in _rows_for_offers_table(p):
                if r.get("inquiry_id") == inquiry_id:
                    rows.append(r)
    return _aggregate_offers_rows(rows)

# -------------------------------
# Token-only Share links (public.share_links)
# -------------------------------
class ShareCreateBody(BaseModel):
    """
    Create a token that represents either:
      A) a frozen snapshot ('results'), or
      B) a dynamic view by document_ids ('document_ids').
    If both provided, snapshot takes precedence.
    """
    title: Optional[str] = None
    company_name: Optional[str] = None
    employees_count: Optional[int] = None
    document_ids: Optional[List[str]] = None
    results: Optional[List[Dict[str, Any]]] = None
    expires_in_hours: Optional[int] = Field(720, ge=0, description="0 = never expires")

def _gen_token() -> str:
    return secrets.token_urlsafe(16)

@app.post("/shares")
def create_share_token_only(body: ShareCreateBody, request: Request):
    token = _gen_token()

    mode = "snapshot" if (body.results and len(body.results) > 0) else "by-documents"
    payload = {
        "mode": mode,
        "title": body.title,
        "company_name": body.company_name,
        "employees_count": body.employees_count,
        "document_ids": body.document_ids or [],
        "results": body.results if mode == "snapshot" else None,
    }

    expires_at = None
    if (body.expires_in_hours or 0) > 0:
        expires_at = (datetime.utcnow() + timedelta(hours=body.expires_in_hours or 720)).isoformat() + "Z"

    row = {
        "token": token,
        "inquiry_id": None,  # explicitly NULL — we do not rely on it
        "payload": payload,
        "expires_at": expires_at,
    }

    if _supabase:
        try:
            _supabase.table(_SHARE_TABLE).insert(row).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Share create failed: {e}")
    else:
        _SHARES_FALLBACK[token] = row

    base = os.getenv("SHARE_BASE_URL")
    if base:
        url = f"{base.rstrip('/')}/share/{token}"
    else:
        try:
            url = str(request.url_for("get_share_token_only", token=token))
        except Exception:
            url = f"/shares/{token}"

    return {"ok": True, "token": token, "url": url, "title": body.title}

@app.get("/shares/{token}", name="get_share_token_only")
def get_share_token_only(token: str):
    """
    Returns {ok, token, payload, offers?}
    - If payload.mode == 'snapshot' -> returns payload.results (frozen at share time).
    - If payload.mode == 'by-documents' -> resolves current offers by payload.document_ids.
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

    payload = share.get("payload") or {}
    mode = payload.get("mode") or "snapshot"
    resp: Dict[str, Any] = {
        "ok": True,
        "token": token,
        "payload": payload,
    }

    if mode == "snapshot" and payload.get("results"):
        resp["offers"] = payload["results"]
    elif mode == "by-documents":
        doc_ids = payload.get("document_ids") or []
        resp["offers"] = _offers_by_document_ids(doc_ids)
    else:
        resp["offers"] = []

    return resp
