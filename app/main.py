# app/main.py
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

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Request, Body, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    from supabase import create_client, Client  # type: ignore
except Exception:  # pragma: no cover
    create_client = None  # type: ignore
    Client = None  # type: ignore

from app.gpt_extractor import extract_offer_from_pdf_bytes, ExtractionError
from app.routes.offers_by_documents import router as offers_by_documents_router
from app.routes.debug_db import router as debug_db_router
from app.routes.ingest import router as ingest_router

APP_NAME = "GPT Offer Extractor"
APP_VERSION = "1.0.0"

# -------------------------------
# Concurrency
# -------------------------------
EXTRACT_WORKERS = int(os.getenv("EXTRACT_WORKERS", "4"))
EXEC: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=EXTRACT_WORKERS)
_JOBS_LOCK = threading.Lock()

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.include_router(offers_by_documents_router)  # SQLAlchemy router
app.include_router(debug_db_router)
app.include_router(ingest_router)

# -------------------------------
# CORS (adjust for production)
# -------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production (e.g., ["https://vis.ongo.lv"])
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
_jobs: Dict[str, Dict[str, Any]] = {}            # job_id -> { total, done, errors, docs, timings }
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}    # doc_id -> payload (dev + supabase-fallback)
_SHARES_FALLBACK: Dict[str, Dict[str, Any]] = {} # token -> row
_INSERTED_IDS: Dict[str, List[int]] = {}         # doc_id -> [row ids]

# -------------------------------
# Request context (org & user)
# -------------------------------
def _ctx_ids(request: Optional[Request]) -> Tuple[Optional[int], Optional[int]]:
    if not request:
        return None, None
    org = request.headers.get("X-Org-Id")
    usr = request.headers.get("X-User-Id")
    try:
        org_id = int(org) if org is not None and str(org).isdigit() else None
    except Exception:
        org_id = None
    try:
        user_id = int(usr) if usr is not None and str(usr).isdigit() else None
    except Exception:
        user_id = None
    return org_id, user_id

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


# ---------- Helpers to avoid duplicate (filename, insurer, program_code) ----------
def _feature_value(x: Any) -> Any:
    if isinstance(x, dict):
        return x.get("value")
    return x

def _disambiguate_duplicate_program_codes(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    For the same (filename, insurer, program_code) appearing more than once,
    append a meaningful suffix so it becomes unique (e.g., '— stacionārs 750 EUR'
    or '— prēmija 282'). Falls back to '— variants #n'.
    """
    groups: Dict[Tuple[str, str, str], List[int]] = {}
    for i, r in enumerate(rows):
        key = (
            (r.get("filename") or "-"),
            (r.get("insurer") or r.get("company_hint") or "-").strip(),
            (r.get("program_code") or "-").strip().lower(),
        )
        groups.setdefault(key, []).append(i)

    for _, idxs in groups.items():
        if len(idxs) <= 1:
            continue

        base_label = rows[idxs[0]].get("program_code") or "-"
        used_labels: set = set()

        for n, idx in enumerate(idxs, start=1):
            r = rows[idx]
            f = r.get("features") or {}

            stac = (
                _feature_value(f.get("Maksas stacionārie pakalpojumi, limits EUR"))
                or _feature_value(f.get("Maksas stacionārie pakalpojumi, limits EUR (pp)"))
            )
            prem = r.get("premium_eur")
            base_sum = r.get("base_sum_eur")

            suffix = None
            if stac not in (None, "", "-"):
                if isinstance(stac, (int, float)) or (isinstance(stac, str) and stac.replace('.', '', 1).isdigit()):
                    suffix = f"stacionārs {stac} EUR"
                else:
                    suffix = str(stac)
            elif prem not in (None, "", "-"):
                suffix = f"prēmija {prem}"
            elif base_sum not in (None, "", "-"):
                suffix = f"b/s {base_sum}"

            label = f"{base_label} — {suffix}" if suffix else f"{base_label} — variants {n}"

            uniq = label
            k = 2
            while uniq in used_labels:
                uniq = f"{label} #{k}"
                k += 1
            used_labels.add(uniq)
            rows[idx]["program_code"] = uniq

    return rows
# --------------------------------------------------------------------------------------


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
    org_id = payload.get("_org_id")
    created_by_user_id = payload.get("_user_id")

    rows: List[Dict[str, Any]] = []
    programs = payload.get("programs", []) or []
    if programs:
        for prog in programs:
            insurer_val = prog.get("insurer") or hint
            rows.append({
                "insurer": insurer_val,
                "company_hint": hint,
                "program_code": prog.get("program_code"),
                "source": "api",
                "filename": doc_id,
                "inquiry_id": inquiry_id,
                "base_sum_eur": _num(prog.get("base_sum_eur")),
                "premium_eur": _num(prog.get("premium_eur")),
                "payment_method": prog.get("payment_method"),
                "features": prog.get("features") or {},
                "raw_json": payload,
                "status": "parsed",
                "error": None,
                "company_name": company_name,
                "employee_count": int(employee_count) if isinstance(employee_count, (int, float)) else None,
                "org_id": org_id,
                "created_by_user_id": created_by_user_id,
            })
        rows = _disambiguate_duplicate_program_codes(rows)
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
            "org_id": org_id,
            "created_by_user_id": created_by_user_id,
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
                "insurer_hint": r.get("insurer_hint") or r.get("insurer") or r.get("company_hint"),
                "company_name": r.get("company_name"),
                "employee_count": r.get("employee_count"),
            }
        if (r.get("status") or "parsed") != "error":
            grouped[src]["programs"].append({
                "row_id": r.get("id"),
                "insurer": r.get("insurer"),
                "program_code": r.get("program_code"),
                "base_sum_eur": r.get("base_sum_eur"),
                "premium_eur": r.get("premium_eur"),
                "payment_method": r.get("payment_method"),
                "features": r.get("features") or {},
            })
        if grouped[src]["status"] == "error" and (r.get("status") or "parsed") == "parsed":
            grouped[src]["status"] = "parsed"
            grouped[src]["error"] = None

    for g in grouped.values():
        if not g["programs"] and not g.get("error"):
            g["status"] = "error"
            g["error"] = "no programs"
    return list(grouped.values())


def save_to_supabase(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Insert one row per program into public.offers (or keep in memory if no Supabase).
    Compatible with older supabase-py that doesn't support .insert(...).select(...).
    """
    doc_id = payload.get("document_id") or payload.get("source_file") or "uploaded.pdf"
    _LAST_RESULTS[doc_id] = payload

    if not _supabase:
        return True, None

    try:
        rows = _rows_for_offers_table(payload)

        _supabase.table(_OFFERS_TABLE).insert(rows).execute()

        try:
            q = (
                _supabase.table(_OFFERS_TABLE)
                .select("id")
                .eq("filename", doc_id)
                .order("id", desc=False)
                .execute()
            )
            ids = [r["id"] for r in (q.data or []) if isinstance(r, dict) and "id" in r]
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
                .execute()
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
    request: Request,
    file: UploadFile = File(...),
    insurer: str = Form(""),
    company: str = Form(""),
    insured_count: int = Form(0),
    inquiry_id: str = Form(""),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF required")

    org_id, user_id = _ctx_ids(request)

    batch_id = str(uuid.uuid4())
    doc_id = _make_doc_id(batch_id, 1, file.filename or "uploaded.pdf")
    original_name = file.filename or "uploaded.pdf"

    data = await file.read()
    t0 = time.monotonic()
    try:
        payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
        payload["original_filename"] = original_name
        payload["_org_id"] = org_id
        payload["_user_id"] = user_id

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
            "_org_id": org_id,
            "_user_id": user_id,
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
    org_id, user_id = _ctx_ids(request)

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
            payload["_org_id"] = org_id
            payload["_user_id"] = user_id
            _inject_meta(payload, insurer=insurers[idx - 1] or "", company=company, insured_count=insured_count, inquiry_id=inquiry_id)
            ok, err = save_to_supabase(payload)
            payload["_persist"] = "supabase" if ok else f"fallback: {err}"
            payload["_timings"] = {"total_s": round(time.monotonic() - t0, 3)}
            results.append(payload)
            doc_ids.append(doc_id)
        except ExtractionError as e:
            # doc_id may be undefined if error happened before creation; use filename as safe fallback
            err_doc_id = locals().get("doc_id", f"{batch_id}::{idx}::{_safe_filename(filename)}")
            payload = {
                "document_id": err_doc_id,
                "original_filename": filename,
                "programs": [],
                "_error": f"ExtractionError: {e}",
                "_org_id": org_id,
                "_user_id": user_id,
            }
            _inject_meta(payload, insurer=insurers[idx - 1] or "", company=company, insured_count=insured_count, inquiry_id=inquiry_id)
            _LAST_RESULTS[err_doc_id] = payload
            results.append(payload)
        except Exception as e:
            results.append({"document_id": filename, "error": f"Unexpected error: {e}"})

    return JSONResponse({"documents": doc_ids, "results": results})


@app.post("/extract/multiple-async", status_code=202)
async def extract_multiple_async(request: Request):
    org_id, user_id = _ctx_ids(request)

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
            org_id=org_id,
            user_id=user_id,
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
    org_id: Optional[int],
    user_id: Optional[int],
):
    t_start = time.monotonic()
    with _JOBS_LOCK:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec.setdefault("timings", {})
            rec["timings"].setdefault(doc_id, {})["queue_s"] = round(t_start - float(enq_ts), 3)

    try:
        t_llm0 = time.monotonic()
        payload = extract_offer_from_pdf_bytes(data, document_id=doc_id)
        t_llm = time.monotonic() - t_llm0

        t_db0 = time.monotonic()
        payload["original_filename"] = original_name
        payload["_org_id"] = org_id
        payload["_user_id"] = user_id
        _inject_meta(payload, insurer=insurer, company=company, insured_count=insured_count, inquiry_id=inquiry_id_raw)
        ok, err = save_to_supabase(payload)
        t_db = time.monotonic() - t_db0

        payload["_timings"] = {
            "llm_s": round(t_llm, 3),
            "db_s": round(t_db, 3),
            "total_s": round(time.monotonic() - t_start, 3),
        }
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
        payload = {
            "document_id": doc_id,
            "original_filename": original_name,
            "programs": [],
            "_error": f"extract: {e}",
            "_timings": {"total_s": round(time.monotonic() - t_start, 3)},
            "_org_id": org_id,
            "_user_id": user_id,
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
# Templates API (create/list/instantiate)
# -------------------------------
@app.post("/templates")
def create_template(
    request: Request,
    insurer: str = Form(""),
    program_code: str = Form(""),
    label: str = Form(""),
    employees_bucket: int = Form(0),
    defaults: Dict[str, Any] = Body({}, embed=True),  # { premium_eur, base_sum_eur, payment_method, features:{} }
):
    org_id, user_id = _ctx_ids(request)
    if not _supabase:
        raise HTTPException(status_code=503, detail="DB not configured")

    row = {
        "org_id": org_id,
        "created_by_user_id": user_id,
        "insurer": insurer or None,
        "program_code": program_code or None,
        "label": label or None,
        "employees_bucket": int(employees_bucket) if employees_bucket else None,
        "defaults": defaults or {},
    }
    res = _supabase.table("offer_templates").insert(row).execute()
    return {"ok": True, "template": (res.data or [row])[0]}


@app.get("/templates")
def list_templates(request: Request, insurer: str = "", employees_bucket: int = 0, limit: int = 20):
    org_id, _ = _ctx_ids(request)
    if not _supabase:
        return []
    q = _supabase.table("offer_templates").select("*").eq("org_id", org_id)
    if insurer:
        q = q.eq("insurer", insurer)
    if employees_bucket:
        q = q.eq("employees_bucket", employees_bucket)
    q = q.order("usage_count", desc=True).limit(limit)
    res = q.execute()
    return res.data or []


@app.post("/templates/{template_id}/instantiate")
def instantiate_template(template_id: int, request: Request, company: str = Form(""), insured_count: int = Form(0)):
    """
    Create an 'offers' group from a template (source='template', status='draft').
    Returns a 'document_id' (so the FE can use offers/by-documents exactly like PDFs).
    """
    org_id, user_id = _ctx_ids(request)
    if not _supabase:
        raise HTTPException(status_code=503, detail="DB not configured")

    t = _supabase.table("offer_templates").select("*").eq("id", template_id).limit(1).execute()
    if not t.data:
        raise HTTPException(status_code=404, detail="template not found")
    tpl = t.data[0]

    batch_id = str(uuid.uuid4())
    doc_id = _make_doc_id(
        batch_id,
        1,
        f"{tpl.get('insurer','template')}-{tpl.get('program_code') or 'DRAFT'}.tmpl.json"
    )
    defaults = tpl.get("defaults") or {}

    row = {
        "insurer": tpl.get("insurer"),
        "company_hint": tpl.get("insurer"),
        "program_code": tpl.get("program_code"),
        "source": "template",
        "filename": doc_id,
        "inquiry_id": None,
        "base_sum_eur": _num(defaults.get("base_sum_eur")),
        "premium_eur": _num(defaults.get("premium_eur")),
        "payment_method": defaults.get("payment_method"),
        "features": defaults.get("features") or {},
        "raw_json": {"template_id": template_id, "template_label": tpl.get("label")},
        "status": "draft",
        "error": None,
        "company_name": company or "-",
        "employee_count": int(insured_count) if insured_count else None,
        "org_id": org_id,
        "created_by_user_id": user_id,
    }
    _supabase.table(_OFFERS_TABLE).insert(row).execute()

    try:
        _supabase.rpc("increment_template_usage", {"t_id": template_id}).execute()
    except Exception:
        pass

    return {"ok": True, "document_id": doc_id}

# -------------------------------
# Read/Update offers
# -------------------------------
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

# ---------- Share token auth helpers for edit/delete ----------
def _load_share_record(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    if _supabase:
        try:
            res = _supabase.table(_SHARE_TABLE).select("*").eq("token", token).limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception:
            return None
    return _SHARES_FALLBACK.get(token)

def _parse_iso_utc(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Accept "Z" suffix or offsetless ISO
        if s.endswith("Z"):
            s = s[:-1]
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _ensure_share_editable(share_token: Optional[str]) -> None:
    if not share_token:
        return
    rec = _load_share_record(share_token)
    if not rec:
        raise HTTPException(status_code=403, detail="Invalid share token")
    exp = _parse_iso_utc(rec.get("expires_at"))
    if exp and datetime.utcnow() > exp:
        raise HTTPException(status_code=403, detail="Share token expired")
    payload = rec.get("payload") or {}
    if not bool(payload.get("editable")):
        raise HTTPException(status_code=403, detail="Share is read-only")

@app.delete("/offers/{offer_id}")
def delete_offer(offer_id: int, x_share_token: Optional[str] = Header(default=None, alias="X-Share-Token")):
    # If a share token is provided, require it to be editable
    _ensure_share_editable(x_share_token)

    if not _supabase:
        raise HTTPException(status_code=503, detail="DB not configured")
    try:
        _supabase.table(_OFFERS_TABLE).delete().eq("id", offer_id).execute()
        for doc_id, ids in list(_INSERTED_IDS.items()):
            _INSERTED_IDS[doc_id] = [i for i in ids if i != offer_id]
        return {"ok": True, "deleted": offer_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")

@app.patch("/offers/{offer_id}")
def update_offer(offer_id: int, body: OfferUpdateBody, x_share_token: Optional[str] = Header(default=None, alias="X-Share-Token")):
    # If a share token is provided, require it to be editable
    _ensure_share_editable(x_share_token)

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
                .execute()
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
    editable: Optional[bool] = None
    role: Optional[str] = None
    allow_edit_fields: Optional[List[str]] = None
    insurer_only: Optional[str] = None
    # FE view preferences (column order, hidden rows, etc.)
    view_prefs: Optional[Dict[str, Any]] = None

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
        "editable": body.editable,
        "role": body.role,
        "allow_edit_fields": body.allow_edit_fields,
        "insurer_only": body.insurer_only,
        "view_prefs": body.view_prefs or {},
    }

    expires_at = None
    if (body.expires_in_hours or 0) > 0:
        expires_at = (datetime.utcnow() + timedelta(hours=body.expires_in_hours or 720)).isoformat() + "Z"

    row = {
        "token": token,
        "inquiry_id": None,
        "payload": payload,
        "expires_at": expires_at,
        "view_prefs": body.view_prefs or {},  # stored in dedicated column too
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

    # Returning view_prefs here is harmless, but FE uses GET for reading payload
    return {"ok": True, "token": token, "url": url, "title": body.title, "view_prefs": body.view_prefs or {}}


@app.get("/shares/{token}", name="get_share_token_only")
def get_share_token_only(token: str):
    """Return snapshot results or dynamic offers for a token (with optional insurer-only filtering)."""
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

    # Expiry check
    exp = _parse_iso_utc(share.get("expires_at"))
    if exp and datetime.utcnow() > exp:
        raise HTTPException(status_code=404, detail="Share token expired")

    payload = share.get("payload") or {}
    mode = payload.get("mode") or "snapshot"

    # Build offers (snapshot or dynamic)
    if mode == "snapshot" and payload.get("results"):
        offers = payload["results"]
    elif mode == "by-documents":
        doc_ids = payload.get("document_ids") or []
        offers = _offers_by_document_ids(doc_ids)
    else:
        offers = []

    # Optional: filter by a single insurer (insurer confirmation links)
    def _norm(s: Optional[str]) -> str:
        return (s or "").strip().lower()

    insurer_only = _norm(payload.get("insurer_only"))
    if insurer_only:
        filtered = []
        for g in offers or []:
            progs = [p for p in (g.get("programs") or []) if _norm(p.get("insurer")) == insurer_only]
            if progs:
                ng = dict(g)
                ng["programs"] = progs
                filtered.append(ng)
        offers = filtered

    # Shape EXACTLY as Share.tsx expects:
    response_payload = {
        "company_name": payload.get("company_name"),
        "employees_count": payload.get("employees_count"),
        "editable": bool(payload.get("editable")),
        "role": payload.get("role") or "broker",
        "allow_edit_fields": payload.get("allow_edit_fields") or [],
        "view_prefs": share.get("view_prefs") or payload.get("view_prefs") or {},
    }

    return {
        "ok": True,
        "token": token,
        "payload": response_payload,   # <-- FE reads editable & view_prefs from here
        "offers": offers,
    }

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
