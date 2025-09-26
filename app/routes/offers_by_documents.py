# app/routes/offers_by_documents.py
from typing import Any, Dict, List
import os
import json

from fastapi import APIRouter, Body
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

# Add pool_pre_ping to avoid stale pooled connections
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
router = APIRouter(prefix="/offers", tags=["offers"])


@router.post("/by-documents")
def offers_by_documents(payload: Dict[str, Any] = Body(...)) -> List[Dict[str, Any]]:
    """
    Input:  { "document_ids": ["<document_id>", ...] }
    Output: [
      {
        "source_file": "<document_id>",
        "inquiry_id": 123,
        "company_name": "...",
        "employee_count": 42,
        "programs": [ { ... } ]
      }
    ]
    """
    document_ids = payload.get("document_ids") or []
    if not document_ids:
        return []

    sql = text("""
        SELECT
          id,
          insurer,
          program_code,
          base_sum_eur,
          premium_eur,
          payment_method,
          features,
          filename,
          inquiry_id,
          company_name,
          employee_count
        FROM public.offers
        WHERE filename = ANY(:docs)
        ORDER BY filename, insurer NULLS LAST, id
    """)

    with engine.begin() as conn:
        rows = conn.execute(sql, {"docs": document_ids}).mappings().all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        fname = r["filename"]
        g = grouped.get(fname)
        if not g:
            g = {
                "source_file": fname,
                "inquiry_id": r["inquiry_id"],
                "company_name": r["company_name"],
                "employee_count": r["employee_count"],
                "programs": [],
            }
            grouped[fname] = g

        features_obj = r["features"]
        if isinstance(features_obj, str):
            try:
                features_obj = json.loads(features_obj)
            except Exception:
                features_obj = {}

        g["programs"].append({
            "row_id": r["id"],
            "insurer": r["insurer"],
            "program_code": r["program_code"],
            "base_sum_eur": float(r["base_sum_eur"]) if r["base_sum_eur"] is not None else None,
            "premium_eur": float(r["premium_eur"]) if r["premium_eur"] is not None else None,
            "payment_method": r["payment_method"],
            "features": features_obj or {},
        })

    out = list(grouped.values())
    seen = set(grouped.keys())
    for fname in document_ids:
        if fname not in seen:
            out.append({"source_file": fname, "programs": []})
    return out
