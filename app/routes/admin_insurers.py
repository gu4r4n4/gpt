from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
import os, psycopg2
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/api/admin/insurers", tags=["admin-insurers"])

def get_db():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    try: yield conn
    finally: conn.close()

class InsurerUpsert(BaseModel):
    org_id: int
    code: str = Field(..., min_length=2, max_length=32)
    name: str
    logo_url: Optional[str] = None

class Insurer(BaseModel):
    id: int
    org_id: int
    code: str
    name: str
    logo_url: Optional[str]

@router.get("", response_model=List[Insurer])
def list_insurers(org_id: int, conn = Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT id, org_id, code, name, logo_url FROM public.insurers WHERE org_id=%s ORDER BY name", (org_id,))
        return cur.fetchall()

@router.post("", response_model=Insurer)
def upsert_insurer(payload: InsurerUpsert, conn = Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO public.insurers (org_id, code, name, logo_url)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (org_id, code) DO UPDATE SET name=EXCLUDED.name, logo_url=EXCLUDED.logo_url
            RETURNING id, org_id, code, name, logo_url
        """, (payload.org_id, payload.code.upper(), payload.name, payload.logo_url))
        row = cur.fetchone()
        conn.commit()
        return row

@router.delete("")
def delete_insurer(org_id: int, code: str, conn = Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.insurers WHERE org_id=%s AND code=%s", (org_id, code.upper()))
        conn.commit()
    return {"ok": True}
