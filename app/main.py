from __future__ import annotations

import os
import re
import time
import uuid
import secrets
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple

from fastapi import (
    FastAPI, File, UploadFile, HTTPException, Form, Request
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    from supabase import create_client, Client  # type: ignore
except Exception:  # pragma: no cover
    create_client = None  # type: ignore
    Client = None  # type: ignore

from app.gpt_extractor import extract_offer_from_pdf_bytes, ExtractionError

APP_NAME = "GPT Offer Extractor"
APP_VERSION = "0.9.8"

# -------------------------------
# Concurrency (faster than BackgroundTasks)
# -------------------------------
EXTRACT_WORKERS = int(os.getenv("EXTRACT_WORKERS", "4"))
EXEC: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=EXTRACT_WORKERS)
_JOBS_LOCK = threading.Lock()

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# -------------------------------
# CORS (adjust for production)
# -------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
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
_jobs: Dict[str, Dict[str, Any]] = {}            # job_id -> { total, done, errors, docs: [doc_ids...], timings: {doc_id: {...}} }
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}    # doc_id -> payload (dev + supabase-fallback)
_SHARES_FALLBACK: Dict[str, Dict[str, Any]] = {} # token -> row
_INSERTED_IDS: Dict[str, List[int]] = {}         # doc_id -> [row ids] (to expose row_id even if we fall back)

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
        "workers": EXTRACT_WORKERS,
    }

@app.get("/")
def root():
    return {"ok": True}

# -------------------------------
# Filename sanitization (SAFE doc_id)
# -------------------------------
_SAFE_DOC_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

def _safe_filename(name: str) -> str:
    """Convert user filename to a safe ASCII variant so doc_ids are stable and queryable."""
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    base = os.path.basename(name or "uploaded.pdf")
    root, ext = os.path.splitext(base)
    root = _SAFE_DOC_CHARS.sub("_", root).strip("._")
    root = re.sub(r"_+", "_", root)
    root = root[:100] or "uploaded"
    ext = ext if ext else ".pdf"
    return f"{root}{ext}"

def _make_doc_id(prefix: str, idx: int, filename: str) -> str:
    """Build the unique document_id used across the system and saved in offers.filename."""
    return f"{prefix}::{idx}::{_safe_filename(filename)}"

# -------------------------------
# Utilities
# -------------------------------

def _num(v: Any) -> Optional[float]:
    """Best-effort numeric coercion. Returns None for blanks, dashes, N/A, etc."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s in {"", "-", "–", "—", "n/a", "N/A", "NA"}:
            return None
        s = s.replace(" ", "").replace(",", ".")
        if s.count('.') > 1:
            head, _, tail = s.rpartition('.')
            head = head.replace('.', '')
            s = f"{head}.{tail}"
        try:
            return float(s)
        except Exception:
            return None
    return None


def _inject_meta(payload: Dict[str, Any], *, insurer: str, company: str, insured_count: int, inquiry_id: str) -> None:
    """Attach meta to payload; inquiry_id is optional/nullable."""
    payload["insurer_hint"] = insurer or payload.get("insurer_hint") or "-"
    payload["company_name"] = company or payload.get("company_name") or "-"
    payload["employee_count"] = (
        insured_count if isinstance(insured_count, int) else payload.get("employee_count")
    )
    payload["inquiry_id"] = int(inquiry_id) if str(inquiry_id).isdigit() else None


def _rows_for_offers_table(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert one normalized extractor payload (with .programs list)
    into rows for public.offers (one row per program).
    Uses payload['document_id'] as the 'filename' (unique per batch/job).
    For "no programs", emits a single error row so the UI can show a failure card.
    """
    doc_id = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
    inquiry_id = payload.get("inquiry_id")  # may be None
    company_name = payload.get("company_name")
    employee_count = payload.get("employee_count")
    hint = payload.get("insurer_hint") or None

    rows: List[Dict[str, Any]] = []
    programs = payload.get("programs", []) or []
    if programs:
        for prog in programs:
            insurer_val = prog.get("insurer") or hint  # fallback to user-selected hint
            rows.append({
                "insurer": insurer_val,
                "company_hint": hint,             # dropdown hint
                "program_code": prog.get("program_code"),
                "source": "api",
                "filename": doc_id,               # UNIQUE per run (sanitized)
                "inquiry_id": inquiry_id,         # nullable
                "base_sum_eur": _num(prog.get("base_sum_eur")),
                "premium_eur": _num(prog.get("premium_eur")),
                "payment_method": prog.get("payment_method"),
                "features": prog.get("features") or {},
                "raw_json": payload,              # provenance/debug
                "status": "parsed",
                "error": None,
                "company_name": company_name,
                "employee_count": int(employee_count) if isinstance(employee_count, (int, float)) else None,
            })
    else:
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
            "error": payload.get("_error") or "no programs",
            "company_name": company_name,
            "employee_count": int(employee_count) if isinstance(employee_count, (int, float)) else None,
        })
    return rows


def _aggregate_offers_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate DB rows to the FE shape grouped by source_file (document_id)."""
    grouped: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        src = r.get("filename") or "-"
        if src not in grouped:
            grouped[src] = {
                "source_file": src,
                "programs": [],
                "inquiry_id": r.get("inquiry_id"),
                "status": r.get("status") or "parsed",
                "error": r.get("error"),
                "company_hint": r.get("company_hint"),
                # prefer explicit insurer_hint (if present), then parsed insurer, then company_hint
                "insurer_hint": r.get("insurer_hint") or r.get("insurer") or r.get("company_hint"),
                "company_name": r.get("company_name"),
                "employee_count": r.get("employee_count"),
            }
        # If this row has a program, collect it
        if (r.get("status") or "parsed") != "error":
            grouped[src]["programs"].append({
                "row_id": r.get("id"),  # <-- needed for edits
                "insurer": r.get("insurer"),
                "program_code": r.get("program_code"),
                "base_sum_eur": r.get("base_sum_eur"),
                "premium_eur": r.get("premium_eur"),
                "payment_method": r.get("payment_method"),
                "features": r.get("features") or {},
            })
        # Prefer parsed status if any row parsed successfully
        if grouped[src]["status"] == "error" and (r.get("status") or "parsed") == "parsed":
            grouped[src]["status"] = "parsed"
            grouped[src]["error"] = None

    # If a group has 0 programs and no explicit error, flag it
    for g in grouped.values():
        if not g["programs"] and not g.get("error"):
            g["status"] = "error"
            g["error"] = "no programs"
    return list(grouped.values())


def save_to_supabase(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Insert one row per program into public.offers (or keep in memory if no Supabase)."""
    doc_id = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
    _LAST_RESULTS[doc_id] = payload  # keep a fallback copy regardless of DB

    if not _supabase:
        return True, None

    try:
        rows = _rows_for_offers_table(payload)
        # Ask PostgREST to return the inserted IDs explicitly
        res = _supabase.table(_OFFERS_TABLE).insert(rows).select("id").execute()
        # Remember inserted ids so fallback can still expose row_id
        try:
            ids = [r["id"] for r in (res.data or []) if isinstance(r, dict) and "id" in r]
            if not ids:
                # Fallback: fetch by filename if needed
                q = _supabase.table(_OFFERS_TABLE).select("id").eq("filename", doc_id).execute()
                ids = [r["id"] for r in (q.data or []) if "id" in r]
            if ids:
                _INSERTED_IDS[doc_id] = ids
        except Exception:
            pass
        return True, None
    except Exception as e:
        payload["_error"] = f"supabase_insert: {e}"
        _LAST_RESULTS[doc_id] = payload
        print(f"[warn] Supabase insert failed for {doc_id}: {e}")
        return False, str(e)


def _rows_from_fallback(doc_ids: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for doc_id in doc_ids:
        p = _LAST_RESULTS.get(doc_id)
        if not p:
            continue
        rs = _rows_for_offers_table(p)
        # Stitch DB ids we cached at insert time (best-effort order)
        ids = _INSERTED_IDS.get(doc_id) or []
        if ids:
            k = 0
            for r in rs:
                if r.get("status") != "error" and k < len(ids):
                    r["id"] = ids[k]
                    k += 1
        rows.extend(rs)
    return rows


def _offers_by_document_ids(doc_ids: List[str]) -> List[Dict[str, Any]]:
    """Query offers by exact document_id list (stored in 'filename'). Falls back to memory if empty/error."""
    if not doc_ids:
        return []
    rows: List[Dict[str, Any]] = []
    used_fallback = False
    if _supabase:
        try:
            res = (
                _supabase.table(_OFFERS_TABLE)
                .select("*")
                .in_("filename", doc_ids)
                .execute()  # removed ORDER BY created_at for speed
            )
            rows = res.data or []
        except Exception as e:
            print(f"[warn] Supabase select failed: {e}")
            used_fallback = True
    if not rows:
        fb_rows = _rows_from_fallback(doc_ids)
        if fb_rows:
            rows = fb_rows
            used_fallback = True
    agg = _aggregate_offers_rows(rows)
    for obj in agg:
        obj["_source"] = "fallback" if used_fallback else "supabase"
    return agg

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
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    batch_id = str(uuid.uuid4())
    doc_id = _make_doc_id(batch_id, 1, file.filename or "uploaded.pdf")
    original_name = file.filename or "uploaded.pdf"

    data = await file.read()
    t0 = time.monotonic()
    try:
        payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
        payload["original_filename"] = original_name
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
        ok, err = save_to_supabase(payload)
        payload["_timings"] = {"total_s": round(time.monotonic() - t0, 3)}
        payload["_persist"] = "supabase" if ok else f"fallback: {err}"
        return JSONResponse({"document_id": doc_id, "result": payload})
    except ExtractionError as e:
        payload = {
            "document_id": doc_id,
            "original_filename": original_name,
            "programs": [],
            "_error": f"ExtractionError: {e}",
        }
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id)
        _LAST_RESULTS[doc_id] = payload
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

# ---- Helper to parse multipart safely (no Pydantic validation for files) ----
async def _parse_upload_form(request: Request) -> Dict[str, Any]:
    form = await request.form()

    files: List[UploadFile] = form.getlist("files")
    if not files:
        single = form.get("files")
        if isinstance(single, UploadFile):
            files = [single]
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    company = (form.get("company") or "").strip()
    insured_raw = (form.get("insured_count") or "0").strip()
    try:
        insured_cnt = int(insured_raw)
    except Exception:
        insured_cnt = 0
    inquiry_id = (form.get("inquiry_id") or "").strip()

    insurers: List[str] = [str(x or "").strip() for x in form.getlist("insurers")]
    if not insurers:
        tmp: List[str] = []
        for i in range(len(files)):
            tmp.append(str(form.get(f"file_{i}_insurer", "") or "").strip())
        insurers = tmp
    if not insurers or len([x for x in insurers if x]) == 0:
        single_ins = str(form.get("insurer", "") or "").strip()
        insurers = [single_ins for _ in range(len(files))]

    if len(insurers) < len(files):
        insurers += [""] * (len(files) - len(insurers))
    elif len(insurers) > len(files):
        insurers = insurers[:len(files)]

    return {
        "files": files,
        "insurers": insurers,
        "company": company,
        "insured_count": insured_cnt,
        "inquiry_id": inquiry_id,
    }


@app.post("/extract/multiple")
async def extract_multiple(request: Request):
    parsed = await _parse_upload_form(request)
    files: List[UploadFile] = parsed["files"]
    insurers: List[str] = parsed["insurers"]
    company: str = parsed["company"]
    insured_count: int = parsed["insured_count"]
    inquiry_id: str = parsed["inquiry_id"]

    batch_id = str(uuid.uuid4())
    results: List[Dict[str, Any]] = []
    doc_ids: List[str] = []

    for idx, f in enumerate(files, start=1):
        filename = f.filename or "uploaded.pdf"
        if not filename.lower().endswith(".pdf"):
            results.append({"document_id": filename, "error": "Unsupported file type (only PDF)"})
            continue
        try:
            doc_id = _make_doc_id(batch_id, idx, filename)
            original_name = filename
            data = await f.read()
            t0 = time.monotonic()
            payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
            payload["original_filename"] = original_name
            _inject_meta(payload, insurer=insurers[idx - 1] or "", company=company, insured_count=insured_count, inquiry_id=inquiry_id)
            ok, err = save_to_supabase(payload)
            payload["_persist"] = "supabase" if ok else f"fallback: {err}"
            payload["_timings"] = {"total_s": round(time.monotonic() - t0, 3)}
            results.append(payload)
            doc_ids.append(doc_id)
        except ExtractionError as e:
            payload = {
                "document_id": filename,
                "original_filename": filename,
                "programs": [],
                "_error": f"ExtractionError: {e}",
            }
            _inject_meta(payload, insurer=insurers[idx - 1] or "", company=company, insured_count=insured_count, inquiry_id=inquiry_id)
            _LAST_RESULTS[doc_id] = payload
            results.append(payload)
        except Exception as e:
            results.append({"document_id": filename, "error": f"Unexpected error: {e}"})

    return JSONResponse({"documents": doc_ids, "results": results})


@app.post("/extract/multiple-async", status_code=202)
async def extract_multiple_async(request: Request):
    parsed = await _parse_upload_form(request)
    files: List[UploadFile] = parsed["files"]
    insurers: List[str] = parsed["insurers"]
    company: str = parsed["company"]
    insured_count: int = parsed["insured_count"]
    inquiry_id: str = parsed["inquiry_id"]

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _jobs[job_id] = {"total": len(files), "done": 0, "errors": [], "docs": [], "timings": {}}

    doc_ids: List[str] = []
    # Enqueue each file to thread-pool workers
    for idx, f in enumerate(files, start=1):
        filename = f.filename or "uploaded.pdf"
        doc_id = _make_doc_id(job_id, idx, filename)
        doc_ids.append(doc_id)
        data = await f.read()
        EXEC.submit(
            _process_pdf_bytes,
            data=data,
            doc_id=doc_id,
            insurer=insurers[idx - 1] or "",
            company=company,
            insured_count=insured_count,
            job_id=job_id,
            inquiry_id_raw=inquiry_id,
            original_name=filename,
            enq_ts=time.monotonic(),
        )

    with _JOBS_LOCK:
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
    original_name: str,
    enq_ts: float,
):
    t_start = time.monotonic()
    with _JOBS_LOCK:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec.setdefault("timings", {})
            rec["timings"].setdefault(doc_id, {})["queue_s"] = round(t_start - float(enq_ts), 3)

    try:
        # LLM extraction timing
        t_llm0 = time.monotonic()
        payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
        t_llm = time.monotonic() - t_llm0

        # DB timing (including meta injection)
        t_db0 = time.monotonic()
        payload["original_filename"] = original_name
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id_raw)
        ok, err = save_to_supabase(payload)
        t_db = time.monotonic() - t_db0

        payload["_timings"] = {
            "llm_s": round(t_llm, 3),
            "db_s": round(t_db, 3),
            "total_s": round(time.monotonic() - t_start, 3),
        }
        # Keep a latest copy for debug/fallback
        _LAST_RESULTS[doc_id] = payload

        with _JOBS_LOCK:
            rec = _jobs.get(job_id)
            if rec is not None:
                rec["timings"].setdefault(doc_id, {}).update(payload["_timings"]) 
                if not ok:
                    rec["errors"].append({"document_id": doc_id, "error": f"supabase_insert: {err}"})
    except Exception as e:
        with _JOBS_LOCK:
            rec = _jobs.get(job_id)
            if rec is not None:
                rec["errors"].append({"document_id": doc_id, "error": f"extract: {e}"})
                rec["timings"].setdefault(doc_id, {})["total_s"] = round(time.monotonic() - t_start, 3)
        # store minimal error payload for fallback visibility
        payload = {
            "document_id": doc_id,
            "original_filename": original_name,
            "programs": [],
            "_error": f"extract: {e}",
            "_timings": {"total_s": round(time.monotonic() - t_start, 3)},
        }
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id_raw)
        _LAST_RESULTS[doc_id] = payload
    finally:
        with _JOBS_LOCK:
            rec = _jobs.get(job_id)
            if rec is not None:
                rec["done"] += 1


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    with _JOBS_LOCK:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job

# -------------------------------
# Read/Update offers
# -------------------------------
class DocsBody(BaseModel):
    document_ids: List[str]


@app.post("/offers/by-documents")
def offers_by_documents(body: DocsBody):
    return _offers_by_document_ids(body.document_ids)


@app.get("/offers/by-job/{job_id}")
def offers_by_job(job_id: str):
    with _JOBS_LOCK:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    doc_ids = job.get("docs") or []
    return _offers_by_document_ids(doc_ids)


class OfferUpdateBody(BaseModel):
    premium_eur: Optional[Any] = None
    base_sum_eur: Optional[Any] = None
    payment_method: Optional[str] = None
    features: Optional[Dict[str, Any]] = None
    insurer: Optional[str] = None
    program_code: Optional[str] = None


@app.patch("/offers/{offer_id}")
def update_offer(offer_id: int, body: OfferUpdateBody):
    if not _supabase:
        raise HTTPException(status_code=503, detail="DB not configured")

    updates: Dict[str, Any] = {}
    if body.premium_eur is not None:
        v = _num(body.premium_eur)
        if v is None:
            raise HTTPException(status_code=400, detail="premium_eur must be numeric")
        updates["premium_eur"] = v
    if body.base_sum_eur is not None:
        v = _num(body.base_sum_eur)
        if v is None:
            raise HTTPException(status_code=400, detail="base_sum_eur must be numeric")
        updates["base_sum_eur"] = v
    if body.payment_method is not None:
        updates["payment_method"] = body.payment_method
    if body.features is not None:
        updates["features"] = body.features
    if body.insurer is not None:
        updates["insurer"] = body.insurer
    if body.program_code is not None:
        updates["program_code"] = body.program_code

    if not updates:
        raise HTTPException(status_code=400, detail="no changes provided")

    try:
        res = _supabase.table(_OFFERS_TABLE).update(updates).eq("id", offer_id).execute()
        rows = res.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="offer not found")
        return {"ok": True, "offer": rows[0]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"update failed: {e}")


# (legacy — still available)
@app.get("/offers/by-inquiry/{inquiry_id}")
def offers_by_inquiry(inquiry_id: int):
    if _supabase:
        try:
            res = (
                _supabase.table(_OFFERS_TABLE)
                .select("*")
                .eq("inquiry_id", inquiry_id)
                .execute()  # removed ORDER BY created_at for speed
            )
            rows = res.data or []
        except Exception as e:
            print(f"[warn] Supabase by-inquiry failed: {e}")
            rows = []
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
    """Create a token that represents a snapshot or a dynamic by-documents view."""
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
        "inquiry_id": None,
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
    """Return snapshot results or dynamic offers for a token."""
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
    resp: Dict[str, Any] = {"ok": True, "token": token, "payload": payload}

    if mode == "snapshot" and payload.get("results"):
        resp["offers"] = payload["results"]
    elif mode == "by-documents":
        doc_ids = payload.get("document_ids") or []
        resp["offers"] = _offers_by_document_ids(doc_ids)
    else:
        resp["offers"] = []

    return resp

# -------------------------------
# Debug helpers
# -------------------------------
@app.get("/debug/last-results")
def debug_last_results():
    out = []
    for doc_id, p in _LAST_RESULTS.items():
        status = "parsed" if (p.get("programs") or []) else "error"
        out.append({
            "document_id": doc_id,
            "original_filename": p.get("original_filename"),
            "status": status,
            "error": p.get("_error"),
            "insurer_hint": p.get("insurer_hint"),
            "company_name": p.get("company_name"),
            "employee_count": p.get("employee_count"),
        })
    return out


@app.get("/debug/doc/{doc_id}")
def debug_doc(doc_id: str):
    p = _LAST_RESULTS.get(doc_id)
    if not p:
        raise HTTPException(status_code=404, detail="not found")
    return p
