# app/services/persist_offers.py
from sqlalchemy import text
import json

INSERT_SQL = text("""
INSERT INTO public.offers (
  insurer, program_code, base_sum_eur, premium_eur, payment_method,
  features, raw_json, filename, inquiry_id, company_name, employee_count,
  org_id, created_by_user_id
)
VALUES (
  :insurer, :program_code, :base_sum_eur, :premium_eur, :payment_method,
  (:features)::jsonb, (:raw_json)::jsonb, :filename, :inquiry_id, :company_name, :employee_count,
  :org_id, :created_by_user_id
)
RETURNING id
""")

def persist_offers(engine, filename, normalized, org_id=None, created_by_user_id=None):
    programs = normalized.get("programs") or []
    params = []
    for p in programs:
        params.append({
            "insurer": normalized.get("insurer_code"),
            "program_code": p.get("program_code"),
            "base_sum_eur": p.get("base_sum_eur") if isinstance(p.get("base_sum_eur"), (int, float)) else None,
            "premium_eur": p.get("premium_eur") if isinstance(p.get("premium_eur"), (int, float)) else None,
            "payment_method": normalized.get("payment_method"),
            "features": json.dumps(p.get("features") or {}),
            "raw_json": json.dumps(p),  # helpful to debug
            "filename": filename,
            "inquiry_id": normalized.get("inquiry_id"),
            "company_name": normalized.get("company_name"),
            "employee_count": normalized.get("employee_count"),
            "org_id": org_id,
            "created_by_user_id": created_by_user_id,
        })

    ids = []
    with engine.begin() as conn:
        res = conn.execute(INSERT_SQL, params)
        ids = [r[0] for r in res.fetchall()]
    print(f"[persist_offers] inserted {len(ids)} rows for {filename}")
    return ids
