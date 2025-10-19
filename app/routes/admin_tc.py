from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Query, status
from pydantic import BaseModel
from typing import List, Optional
from hashlib import sha256
from datetime import datetime
import os, json, psycopg2, traceback
from psycopg2.extras import RealDictCursor
from openai import OpenAI

router = APIRouter(prefix="/api/admin/tc", tags=["admin-tc"])
client = OpenAI()
UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "/var/app/uploads")

def get_db():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    try: yield conn
    finally: conn.close()

# --- Vector store management: one per org × product_line ---
def ensure_vs(conn, org_id: int, product_line: str) -> str:
    # dedicated table recommended; fall back to organizations.vector_store_id if needed
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.org_vector_stores (
              org_id INTEGER NOT NULL,
              product_line TEXT NOT NULL,
              vector_store_id TEXT NOT NULL,
              PRIMARY KEY (org_id, product_line)
            )
        """)
        conn.commit()
        cur.execute("SELECT vector_store_id FROM public.org_vector_stores WHERE org_id=%s AND product_line=%s",
                    (org_id, product_line))
        row = cur.fetchone()
        if row: return row["vector_store_id"]
        vs = client.beta.vector_stores.create(name=f"org_{org_id}_{product_line}".lower())
        cur.execute("INSERT INTO public.org_vector_stores(org_id, product_line, vector_store_id) VALUES (%s,%s,%s)",
                    (org_id, product_line, vs.id))
        conn.commit()
        return vs.id

def push_file(vector_store_id: str, local_path: str, attributes: dict) -> str:
    try:
        with open(local_path, "rb") as f:
            up = client.files.create(file=f, purpose="assistants")
        # NOTE: if your SDK version doesn't support attributes yet, comment 'attributes=attributes'
        client.beta.vector_stores.files.create(
            vector_store_id=vector_store_id,
            file_id=up.id,
            # attributes=attributes  # comment this if unsupported in your SDK
        )
        return up.id
    except Exception as e:
        # log server-side and raise a 502 so you see the cause in the response
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
    product_line: str = Form(...),                    # e.g., HEALTH | MTPL | CASCO
    expires_at: Optional[str] = Form(None),           # ISO date (YYYY-MM-DD)
    effective_from: Optional[str] = Form(None),
    version_label: Optional[str] = Form(None),
    created_by_user_id: Optional[int] = Form(None),
    files: List[UploadFile] = File(...),
    conn = Depends(get_db),
):
    # Validate insurer exists
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM public.insurers WHERE org_id=%s AND code=%s", (org_id, insurer_code.upper()))
        if not cur.fetchone():
            raise HTTPException(422, detail="Unknown insurer_code for this org")

    # Parse dates
    def parse_dt(s: str | None) -> str | None:
        if not s:
            return None
        s = s.strip()
        # normalize trailing Z to +00:00 for fromisoformat
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # allow date-only
        if len(s) == 10 and s.count("-") == 2:
            s = s + "T00:00:00+00:00"
        return datetime.fromisoformat(s).isoformat()
    
    eff = parse_dt(effective_from)
    exp = parse_dt(expires_at)

    vs_id = ensure_vs(conn, org_id, product_line.upper())

    out = []
    for uf in files:
        raw = await uf.read()
        file_sha = sha256(raw).hexdigest()

        # de-dup by (org_id, sha256, product_line, insurer_code)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, retrieval_file_id, filepath FROM public.offer_files
                WHERE org_id=%s AND sha256=%s AND product_line=%s AND insurer_code=%s
                LIMIT 1
            """, (org_id, file_sha, product_line.upper(), insurer_code.upper()))
            existing = cur.fetchone()

        datedir = datetime.utcnow().strftime("%Y/%m/%d")
        orgdir = os.path.join(UPLOAD_ROOT, f"org_{org_id}", "tc", product_line.upper(), datedir)
        os.makedirs(orgdir, exist_ok=True)
        local_path = os.path.join(orgdir, uf.filename)

        if existing:
            # ensure file exists on disk; if not, restore
            if not os.path.exists(existing["filepath"]):
                with open(local_path, "wb") as outfp: outfp.write(raw)
            retrieval_file_id = existing["retrieval_file_id"]
        else:
            with open(local_path, "wb") as outfp: outfp.write(raw)
            retrieval_file_id = push_file(
                vs_id,
                local_path,
                attributes={
                    "org_id": org_id,
                    "product_line": product_line.upper(),
                    "insurer_code": insurer_code.upper(),
                    "version_label": version_label or "",
                    "effective_from": eff or "",
                    "expires_at": exp or "",
                    "type": "TANDC"
                }
            )

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.offer_files
                      (org_id, filename, sha256, size_bytes, filepath,
                       vector_store_id, retrieval_file_id, is_permanent,
                       insurer_code, product_line, effective_from, expires_at, version_label, created_by_user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (org_id, uf.filename, file_sha, len(raw), local_path,
                      vs_id, retrieval_file_id, insurer_code.upper(), product_line.upper(),
                      eff, exp, version_label, created_by_user_id))
                new_id = cur.fetchone()["id"]
                conn.commit()

        out.append({"filename": uf.filename, "retrieval_file_id": retrieval_file_id})

    return {"ok": True, "files": out, "vector_store_id": vs_id}

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
