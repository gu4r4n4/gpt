# app/casco/persistence.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence
import uuid

import json

from .schema import CascoCoverage


@dataclass
class CascoOfferRecord:
    """
    Canonical CASCO offer shape we store in public.offers_casco.
    This is the bridge between extractor/normalizer and the DB.
    
    Uses casco_job_id (UUID string) for grouping offers (NOT inquiry_id).
    Each upload creates a new job, and all offers in that upload share the same casco_job_id.
    """

    insurer_name: str
    reg_number: str
    casco_job_id: str  # Required - UUID string linking to casco_jobs.casco_job_id
    insured_entity: Optional[str] = None

    insured_amount: Optional[str] = None  # Always "Tirgus vērtība" for CASCO
    currency: str = "EUR"
    territory: Optional[str] = None
    period: Optional[str] = None  # Insurance period (e.g., "12 mēneši")

    premium_total: Optional[Decimal] = None
    premium_breakdown: Optional[Dict[str, Any]] = None  # e.g. {"kasko": 1480.0, "nelaimes": 4.68}

    coverage: CascoCoverage | Dict[str, Any] = None
    raw_text: Optional[str] = None
    product_line: str = "casco"  # Product line identifier (always 'casco' for CASCO offers)


async def create_casco_job(
    conn,
    reg_number: str,
) -> str:
    """
    Create a new CASCO job entry with UUID identifier.
    Returns the new job ID (UUID string).
    
    This should be called at the start of each upload (single or batch).
    """
    job_id = str(uuid.uuid4())
    
    sql = """
    INSERT INTO public.casco_jobs (casco_job_id, reg_number, product_line)
    VALUES ($1, $2, 'casco')
    RETURNING casco_job_id;
    """
    
    row = await conn.fetchrow(sql, job_id, reg_number)
    return row["casco_job_id"]


async def save_casco_offers(
    conn,  # asyncpg.Connection or compatible
    offers: Sequence[CascoOfferRecord],
) -> List[int]:
    """
    Persist multiple CASCO offers into public.offers_casco.

    Returns list of inserted IDs in the same order as input.
    
    All offers MUST have the same casco_job_id (from the same upload batch).
    """

    sql = """
    INSERT INTO public.offers_casco (
        insurer_name,
        reg_number,
        insured_entity,
        casco_job_id,
        insured_amount,
        currency,
        territory,
        period,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text,
        product_line
    ) VALUES (
        $1, $2, $3, $4,
        $5, $6, $7, $8,
        $9, $10, $11::jsonb, $12, $13
    )
    RETURNING id;
    """

    ids: List[int] = []

    for offer in offers:
        # Normalize coverage to plain dict for JSONB storage
        if isinstance(offer.coverage, CascoCoverage):
            coverage_payload = offer.coverage.model_dump(exclude_none=True)
        else:
            coverage_payload = offer.coverage or {}

        premium_breakdown = offer.premium_breakdown or {}

        row = await conn.fetchrow(
            sql,
            offer.insurer_name,
            offer.reg_number,
            offer.insured_entity,
            offer.casco_job_id,  # UUID string
            offer.insured_amount,
            offer.currency,
            offer.territory,
            offer.period,  # "12 mēneši"
            offer.premium_total,
            json.dumps(premium_breakdown),
            json.dumps(coverage_payload),
            offer.raw_text,
            offer.product_line,  # Always 'casco' via default
        )
        ids.append(row["id"])

    return ids


async def save_single_casco_offer(conn, offer: CascoOfferRecord) -> int:
    """
    Convenience wrapper for inserting a single CASCO offer.
    """
    ids = await save_casco_offers(conn, [offer])
    return ids[0]


async def fetch_casco_offers_by_job(
    conn,
    casco_job_id: str,
) -> List[Dict[str, Any]]:
    """
    Fetch all CASCO offers for a given job ID (UUID string).
    Returns list of dicts with all fields.
    Filters by product_line='casco' to ensure only CASCO offers are returned.
    """
    sql = """
    SELECT 
        id,
        insurer_name,
        reg_number,
        insured_entity,
        casco_job_id,
        insured_amount,
        currency,
        territory,
        period,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text,
        product_line,
        created_at
    FROM public.offers_casco
    WHERE casco_job_id = $1
      AND product_line = 'casco'
    ORDER BY created_at DESC;
    """
    
    rows = await conn.fetch(sql, casco_job_id)
    return [dict(row) for row in rows]


async def fetch_casco_offers_by_reg_number(
    conn,
    reg_number: str,
) -> List[Dict[str, Any]]:
    """
    DEPRECATED: Fetch all CASCO offers for a given vehicle registration number.
    
    This function is kept for backwards compatibility but should NOT be used
    for new code. Use fetch_casco_offers_by_job() instead.
    
    Note: This fetches across multiple jobs, which is not the intended behavior.
    """
    sql = """
    SELECT 
        id,
        insurer_name,
        reg_number,
        insured_entity,
        casco_job_id,
        insured_amount,
        currency,
        territory,
        period,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text,
        product_line,
        created_at
    FROM public.offers_casco
    WHERE reg_number = $1
      AND product_line = 'casco'
    ORDER BY created_at DESC;
    """
    
    rows = await conn.fetch(sql, reg_number)
    return [dict(row) for row in rows]
