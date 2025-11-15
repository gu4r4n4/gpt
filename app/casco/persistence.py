# app/casco/persistence.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

import json

from .schema import CascoCoverage


@dataclass
class CascoOfferRecord:
    """
    Canonical CASCO offer shape we store in public.offers_casco.
    This is the bridge between extractor/normalizer and the DB.
    """

    insurer_name: str
    reg_number: str
    inquiry_id: Optional[int] = None
    insured_entity: Optional[str] = None

    insured_amount: Optional[Decimal] = None
    currency: str = "EUR"
    territory: Optional[str] = None
    period_from: Optional[date] = None
    period_to: Optional[date] = None

    premium_total: Optional[Decimal] = None
    premium_breakdown: Optional[Dict[str, Any]] = None  # e.g. {"kasko": 1480.0, "nelaimes": 4.68}

    coverage: CascoCoverage | Dict[str, Any] = None
    raw_text: Optional[str] = None


async def save_casco_offers(
    conn,  # asyncpg.Connection or compatible
    offers: Sequence[CascoOfferRecord],
) -> List[int]:
    """
    Persist multiple CASCO offers into public.offers_casco.

    Returns list of inserted IDs in the same order as input.
    """

    sql = """
    INSERT INTO public.offers_casco (
        insurer_name,
        reg_number,
        insured_entity,
        inquiry_id,
        insured_amount,
        currency,
        territory,
        period_from,
        period_to,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text
    ) VALUES (
        $1, $2, $3, $4,
        $5, $6, $7, $8, $9,
        $10, $11, $12::jsonb, $13
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
            offer.inquiry_id,
            offer.insured_amount,
            offer.currency,
            offer.territory,
            offer.period_from,
            offer.period_to,
            offer.premium_total,
            json.dumps(premium_breakdown),
            json.dumps(coverage_payload),
            offer.raw_text,
        )
        ids.append(row["id"])

    return ids


async def save_single_casco_offer(conn, offer: CascoOfferRecord) -> int:
    """
    Convenience wrapper for inserting a single CASCO offer.
    """
    ids = await save_casco_offers(conn, [offer])
    return ids[0]


async def fetch_casco_offers_by_inquiry(
    conn,
    inquiry_id: int,
) -> List[Dict[str, Any]]:
    """
    Fetch all CASCO offers for a given inquiry_id.
    Returns list of dicts with all fields.
    """
    sql = """
    SELECT 
        id,
        insurer_name,
        reg_number,
        insured_entity,
        inquiry_id,
        insured_amount,
        currency,
        territory,
        period_from,
        period_to,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text,
        created_at,
        updated_at
    FROM public.offers_casco
    WHERE inquiry_id = $1
    ORDER BY created_at DESC;
    """
    
    rows = await conn.fetch(sql, inquiry_id)
    return [dict(row) for row in rows]


async def fetch_casco_offers_by_reg_number(
    conn,
    reg_number: str,
) -> List[Dict[str, Any]]:
    """
    Fetch all CASCO offers for a given vehicle registration number.
    """
    sql = """
    SELECT 
        id,
        insurer_name,
        reg_number,
        insured_entity,
        inquiry_id,
        insured_amount,
        currency,
        territory,
        period_from,
        period_to,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text,
        created_at,
        updated_at
    FROM public.offers_casco
    WHERE reg_number = $1
    ORDER BY created_at DESC;
    """
    
    rows = await conn.fetch(sql, reg_number)
    return [dict(row) for row in rows]

