# app/services/ingest_offers.py
from typing import Any, Dict, Optional
import os
import json
from decimal import Decimal

from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

engine = create_engine(DATABASE_URL, future=True)

def _num(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip()
    if s == "-" or s == "":
        return None
    # normalize "1 200,50" "1.200,50" → 1200.50
    s = s.replace("€", "").replace("EUR", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return None


def persist_extraction_result(
    *,
    filename: str,
    extracted: Dict[str, Any],
    inquiry_id: Optional[int] = None,
    org_id: Optional[int] = None,
    user_id: Optional[int] = None,
    company_name: Optional[str] = None,
    employee_count: Optional[int] = None,
) -> None:
    """
    Wipes any previous rows for this filename, then inserts ONE row per program.
    """
    insurer = (extracted.get("insurer_code") or "").strip() or None
    programs = extracted.get("programs") or []
    if not programs:
        # Nothing to insert, but clear any old rows for consistency
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM public.offers WHERE filename = :f"), {"f": filename})
        return

    with engine.begin() as conn:
        # idempotent – clear existing rows for this file before re-inserting
        conn.execute(text("DELETE FROM public.offers WHERE filename = :f"), {"f": filename})

        for p in programs:
            program_code = (p.get("program_code") or "").strip() or None
            base_sum = _num(p.get("base_sum_eur"))
            premium = _num(p.get("premium_eur"))
            payment_method = (p.get("payment_method") or "").strip() or None

            features = p.get("features") or {}
            features_json = json.dumps(features)

            conn.execute(text("""
                INSERT INTO public.offers (
                  insurer,
                  company_hint,
                  program_code,
                  source,
                  filename,
                  inquiry_id,
                  base_sum_eur,
                  premium_eur,
                  payment_method,
                  features,
                  raw_json,
                  status,
                  error,
                  company_name,
                  employee_count,
                  org_id,
                  created_by_user_id
                ) VALUES (
                  :insurer,
                  NULL,
                  :program_code,
                  :source,
                  :filename,
                  :inquiry_id,
                  :base_sum_eur,
                  :premium_eur,
                  :payment_method,
                  :features::jsonb,
                  :raw_json::jsonb,
                  'parsed',
                  NULL,
                  :company_name,
                  :employee_count,
                  :org_id,
                  :user_id
                )
            """), {
                "insurer": insurer,
                "program_code": program_code,
                "source": "gpt",  # or whatever you use
                "filename": filename,
                "inquiry_id": inquiry_id,
                "base_sum_eur": base_sum,
                "premium_eur": premium,
                "payment_method": payment_method,
                "features": features_json,
                "raw_json": json.dumps(extracted),
                "company_name": company_name,
                "employee_count": employee_count,
                "org_id": org_id,
                "user_id": user_id,
            })
