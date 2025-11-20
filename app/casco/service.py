from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from app.gpt_extractor import _pdf_pages_text  # HEALTH-safe shared PDF extractor

from .extractor import (
    extract_casco_offers_from_text,
    CascoExtractionResult,
)
from .normalizer import normalize_casco_coverage
from .schema import CascoCoverage
from .persistence import CascoOfferRecord, save_casco_offers


def process_casco_pdf(
    file_bytes: bytes,
    insurer_name: str,
    pdf_filename: Optional[str] = None,
) -> List[CascoExtractionResult]:
    """
    High-level CASCO processing pipeline — SAFE and ISOLATED.

    Steps:
    1. Extract text from PDF (using same logic HEALTH uses)
    2. Hybrid GPT CASCO extraction (structured + raw_text)
    3. Normalize structured coverage
    4. Return hybrid results ready for DB or comparison

    HEALTH logic is never touched.
    """

    # 1. Extract text from PDF using existing HEALTH logic
    full_text, _pages = _pdf_pages_text(file_bytes)

    # 2. Run GPT hybrid CASCO extraction
    extracted_results = extract_casco_offers_from_text(
        pdf_text=full_text,
        insurer_name=insurer_name,
        pdf_filename=pdf_filename,
    )

    # 3. Normalize each structured coverage
    normalized_results: List[CascoExtractionResult] = []

    for result in extracted_results:
        normalized_structured: CascoCoverage = normalize_casco_coverage(
            result.coverage
        )

        normalized_results.append(
            CascoExtractionResult(
                coverage=normalized_structured,
                raw_text=result.raw_text,
            )
        )

    return normalized_results


async def process_and_persist_casco_pdf(
    conn,  # asyncpg connection
    file_bytes: bytes,
    insurer_name: str,
    reg_number: str,
    inquiry_id: Optional[int] = None,
    pdf_filename: Optional[str] = None,
    insured_amount: Optional[str] = None,  # Always "Tirgus vērtība" for CASCO
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
    premium_total: Optional[Decimal] = None,
) -> List[int]:
    """
    Complete CASCO pipeline with DB persistence.
    
    Steps:
    1. Extract text from PDF
    2. Hybrid GPT extraction (structured + raw_text)
    3. Normalize coverage
    4. Persist to public.offers_casco
    
    Returns: List of inserted offer IDs
    """
    
    # Step 1-3: Use existing pipeline
    extraction_results: List[CascoExtractionResult] = process_casco_pdf(
        file_bytes=file_bytes,
        insurer_name=insurer_name,
        pdf_filename=pdf_filename,
    )
    
    # Step 4: Map to DB records
    to_persist: List[CascoOfferRecord] = []
    
    for result in extraction_results:
        coverage = result.coverage
        
        # Extract territory from new 21-field model (Teritorija field)
        territory_val = coverage.Teritorija if coverage.Teritorija and coverage.Teritorija != "-" else None
        
        # insured_amount is always "Tirgus vērtība" (from extractor)
        insured_amt = coverage.insured_amount if hasattr(coverage, 'insured_amount') else "Tirgus vērtība"
        
        # Parse dates if provided as strings
        period_from_date = None
        period_to_date = None
        
        if period_from:
            try:
                period_from_date = datetime.fromisoformat(period_from).date()
            except Exception:
                pass
                
        if period_to:
            try:
                period_to_date = datetime.fromisoformat(period_to).date()
            except Exception:
                pass
        
        to_persist.append(
            CascoOfferRecord(
                insurer_name=insurer_name,
                reg_number=reg_number,
                inquiry_id=inquiry_id,
                insured_entity=None,  # Can be extracted if needed
                insured_amount=insured_amt,
                currency="EUR",
                territory=territory_val,
                period_from=period_from_date,
                period_to=period_to_date,
                premium_total=premium_total,
                premium_breakdown=None,  # Can be extracted later if needed
                coverage=coverage,
                raw_text=result.raw_text,
            )
        )
    
    # Step 5: Persist to DB
    return await save_casco_offers(conn, to_persist)
