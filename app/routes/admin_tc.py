from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Query, status
from pydantic import BaseModel
from typing import List, Optional
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
import os, json, psycopg2, traceback
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from app.services.openai_compat import attach_file_to_vector_store, create_vector_store

router = APIRouter(prefix="/api/admin/tc", tags=["admin-tc"])
client = OpenAI()

# ✅ use a writable default on Render
UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "/tmp/uploads")

def get_db():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    try: yield conn
    finally: conn.close()

def safe_name(name: str) -> str:
    # very basic filename hardening
    return name.replace("..", "").replace("\\", "/").split("/")[-1]

def parse_dt(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    try:
        if len(s) == 10 and s.count("-") == 2:
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt.isoformat()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD or ISO-8601 (e.g. 2025-10-22T10:21:20Z).")

def ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)

# --- Vector store management: one per org × product_line ---
def ensure_vs(conn, org_id: int, product_line: str) -> str:
    """
    Return vector_store_id for org×product_line.
    Creates table/column/row as needed. Fully idempotent; no PK re-add attempts.
    """
    with conn.cursor() as cur:
        # 1) Ensure table + product_line column (idempotent)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.org_vector_stores (
              org_id BIGINT NOT NULL,
              vector_store_id TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.commit()

        # add column if missing
        cur.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='org_vector_stores' AND column_name='product_line'
        """)
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE public.org_vector_stores ADD COLUMN product_line TEXT")
            conn.commit()

        # 2) If table has **no** primary key yet, add the composite PK
        cur.execute("""
            SELECT 1
            FROM pg_constraint
            WHERE conrelid = 'public.org_vector_stores'::regclass
              AND contype = 'p'
        """)
        has_pk = cur.fetchone() is not None
        if not has_pk:
            cur.execute("""
                ALTER TABLE public.org_vector_stores
                ADD CONSTRAINT org_vector_stores_pkey PRIMARY KEY (org_id, product_line)
            """)
            conn.commit()

        # 3) Try fetch an existing vector_store_id
        cur.execute("""
            SELECT vector_store_id
            FROM public.org_vector_stores
            WHERE org_id=%s AND product_line=%s
        """, (org_id, product_line))
        row = cur.fetchone()
        if row:
            return row["vector_store_id"]

        # 4) Create a new vector store and persist mapping
        vs_id = create_vector_store(client, name=f"org_{org_id}_{product_line}".lower())
        cur.execute("""
            INSERT INTO public.org_vector_stores (org_id, product_line, vector_store_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (org_id, product_line) DO UPDATE
            SET vector_store_id = EXCLUDED.vector_store_id
        """, (org_id, product_line, vs_id))
        conn.commit()
        return vs_id

def push_file(vector_store_id: str, local_path: str, attributes: dict | None = None) -> str:
    try:
        with open(local_path, "rb") as f:
            up = client.files.create(file=f, purpose="assistants")
        # attributes not guaranteed in all SDKs; ignore for compatibility
        attach_file_to_vector_store(client, vector_store_id, up.id)
        return up.id
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail=f"Vector-store push failed: {e}")

class TcItem(BaseModel):
    id: int
    org_id: int
    filename: str
    insurer_code: str
    product_line: str
    effective_from: Optional[str]
    expires_at: Optional[str]
    version_label: Optional[str]
    size_bytes: int
    is_permanent: bool

class TcList(BaseModel):
    items: List[TcItem]
    next_offset: int

@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_tc(
    org_id: int = Form(...),
    insurer_code: str = Form(...),
    product_line: str = Form(...),
    expires_at: Optional[str] = Form(None),
    effective_from: Optional[str] = Form(None),
    version_label: Optional[str] = Form(None),
    created_by_user_id: Optional[int] = Form(None),
    files: List[UploadFile] = File(...),
    debug: Optional[int] = Form(0),
    conn = Depends(get_db),
):
    try:
        insurer_code = insurer_code.upper()
        product_line = product_line.upper()

        # 1) insurer exists?
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM public.insurers WHERE org_id=%s AND code=%s", (org_id, insurer_code))
            if not cur.fetchone():
                raise HTTPException(422, detail="Unknown insurer_code for this org")

        # 2) parse dates
        eff = parse_dt(effective_from)
        exp = parse_dt(expires_at)

        # 3) ensure vector store per org×product_line
        vs_id = ensure_vs(conn, org_id, product_line)

        out = []
        # 4) disk base
        datedir = datetime.utcnow().strftime("%Y/%m/%d")
        base_dir = os.path.join(UPLOAD_ROOT, f"org_{org_id}", "tc", product_line, datedir)
        ensure_dir(base_dir)

        for uf in files:
            # a) read + hash
            raw = await uf.read()
            if not raw:
                raise HTTPException(422, detail=f"Empty file: {uf.filename}")
            file_sha = sha256(raw).hexdigest()
            filename = safe_name(uf.filename)
            local_path = os.path.join(base_dir, filename)

            # b) dedupe
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, retrieval_file_id, storage_path
                    FROM public.offer_files
                    WHERE org_id=%s AND sha256=%s AND product_line=%s AND insurer_code=%s
                    LIMIT 1
                """, (org_id, file_sha, product_line, insurer_code))
                existing = cur.fetchone()

            if existing:
                # ensure file exists on disk
                try:
                    if not os.path.exists(existing["storage_path"]):
                        with open(local_path, "wb") as outfp: outfp.write(raw)
                    retrieval_file_id = existing["retrieval_file_id"]
                except Exception as e:
                    traceback.print_exc()
                    raise HTTPException(500, detail=f"Local restore failed: {e}")
            else:
                # c) write file
                try:
                    with open(local_path, "wb") as outfp:
                        outfp.write(raw)
                except Exception as e:
                    traceback.print_exc()
                    raise HTTPException(500, detail=f"Disk write failed (check UPLOAD_ROOT): {e}")

                # d) push to vector store (can be toggled off for diagnostics)
                if os.getenv("TC_UPLOAD_SKIP_OPENAI", "0") == "1":
                    retrieval_file_id = f"skipped_{file_sha[:8]}"
                else:
                    retrieval_file_id = push_file(vs_id, local_path)

                # e) db insert
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO public.offer_files
                              (org_id, filename, mime_type, size_bytes, sha256, storage_path,
                               vector_store_id, retrieval_file_id, is_permanent,
                               insurer_code, product_line, effective_from, expires_at, version_label, created_by_user_id)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s)
                            RETURNING id
                        """, (org_id, filename, uf.content_type or "application/pdf", len(raw), file_sha, local_path,
                              vs_id, retrieval_file_id, insurer_code, product_line,
                              eff, exp, version_label, created_by_user_id))
                        new_id = cur.fetchone()["id"]
                        conn.commit()
                except Exception as e:
                    traceback.print_exc()
                    raise HTTPException(500, detail=f"DB insert failed: {e}")

            out.append({"filename": filename, "retrieval_file_id": retrieval_file_id})

        return {"ok": True, "files": out, "vector_store_id": vs_id}

    except HTTPException:
        raise
    except Exception as e:
        # last-resort safety net (with optional echo)
        traceback.print_exc()
        msg = f"Unexpected error: {e}"
        if debug:
            msg += " (see server logs for traceback)"
        raise HTTPException(500, detail=msg)

@router.get("", response_model=TcList)
def list_tc(
    org_id: int,
    product_line: Optional[str] = None,
    insurer_code: Optional[str] = None,
    status_filter: Optional[str] = Query(None, regex="^(active|expired)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn = Depends(get_db),
):
    cond, params = ["org_id=%s", "is_permanent=true"], [org_id]
    if product_line: cond.append("product_line=%s"); params.append(product_line.upper())
    if insurer_code: cond.append("insurer_code=%s"); params.append(insurer_code.upper())
    if status_filter == "active": cond.append("(expires_at IS NULL OR expires_at > now())")
    if status_filter == "expired": cond.append("(expires_at IS NOT NULL AND expires_at <= now())")
    where = " AND ".join(cond)

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, org_id, filename, insurer_code, product_line,
                   to_char(effective_from, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as effective_from,
                   to_char(expires_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as expires_at,
                   version_label, size_bytes, is_permanent
            FROM public.offer_files
            WHERE {where}
            ORDER BY COALESCE(expires_at, '2999-12-31') DESC, filename
            LIMIT %s OFFSET %s
        """, (*params, limit, offset))
        items = cur.fetchall()
    return {"items": items, "next_offset": offset + len(items)}

class TcPatch(BaseModel):
    effective_from: Optional[str] = None
    expires_at: Optional[str] = None
    version_label: Optional[str] = None
    insurer_code: Optional[str] = None

@router.patch("/{id}")
def patch_tc(id: int, payload: TcPatch, conn = Depends(get_db)):
    sets, vals = [], []
    if payload.effective_from is not None: sets.append("effective_from=%s"); vals.append(payload.effective_from)
    if payload.expires_at is not None:     sets.append("expires_at=%s");     vals.append(payload.expires_at)
    if payload.version_label is not None:  sets.append("version_label=%s");  vals.append(payload.version_label)
    if payload.insurer_code is not None:   sets.append("insurer_code=%s");   vals.append(payload.insurer_code.upper())
    if not sets: raise HTTPException(400, "No fields to update")
    with conn.cursor() as cur:
        cur.execute(f"UPDATE public.offer_files SET {', '.join(sets)} WHERE id=%s RETURNING id", (*vals, id))
        if not cur.fetchone(): raise HTTPException(404, "Not found")
        conn.commit()
    return {"ok": True}
