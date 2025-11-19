"""
CASCO API Endpoints
Handles PDF upload, extraction, normalization, persistence, and comparison.
Integrates seamlessly with existing insurance_inquiries workflow.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional, List

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import APIRouter, UploadFile, Form, HTTPException, Depends, Request

from app.casco.service import process_casco_pdf
from app.casco.comparator import build_casco_comparison_matrix
from app.casco.schema import CascoCoverage
from app.casco.persistence import CascoOfferRecord


router = APIRouter(prefix="/casco", tags=["CASCO"])


def get_db():
    """Database connection dependency."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------
# Helper: Save to DB (sync adapter for persistence layer)
# ---------------------------
def _save_casco_offer_sync(
    conn,
    offer: CascoOfferRecord,
) -> int:
    """
    Synchronous adapter for saving CASCO offers.
    Adapts the async persistence layer to work with psycopg2.
    """
    import json
    
    sql = """
    INSERT INTO public.offers_casco (
        insurer_name,
        reg_number,
        insured_entity,
        inquiry_id,
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
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s, %s
    )
    RETURNING id;
    """
    
    # Normalize coverage to plain dict for JSONB storage
    if isinstance(offer.coverage, CascoCoverage):
        coverage_payload = offer.coverage.model_dump(exclude_none=True)
    else:
        coverage_payload = offer.coverage or {}
    
    premium_breakdown = offer.premium_breakdown or {}
    
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                offer.insurer_name,
                offer.reg_number,
                offer.insured_entity,
                offer.inquiry_id,
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
        )
        row = cur.fetchone()
        conn.commit()
        return row["id"]


def _fetch_casco_offers_by_inquiry_sync(conn, inquiry_id: int) -> List[dict]:
    """
    Fetch all CASCO offers for an inquiry.
    Filters by product_line='casco' to ensure only CASCO offers are returned.
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
        period,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text,
        product_line,
        created_at
    FROM public.offers_casco
    WHERE inquiry_id = %s
      AND product_line = 'casco'
    ORDER BY created_at DESC;
    """
    
    with conn.cursor() as cur:
        cur.execute(sql, (inquiry_id,))
        return cur.fetchall()


def _fetch_casco_offers_by_reg_number_sync(conn, reg_number: str) -> List[dict]:
    """
    Fetch all CASCO offers for a vehicle.
    Filters by product_line='casco' to ensure only CASCO offers are returned.
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
        period,
        premium_total,
        premium_breakdown,
        coverage,
        raw_text,
        product_line,
        created_at
    FROM public.offers_casco
    WHERE reg_number = %s
      AND product_line = 'casco'
    ORDER BY created_at DESC;
    """
    
    with conn.cursor() as cur:
        cur.execute(sql, (reg_number,))
        return cur.fetchall()


# ---------------------------
# 1. Upload a single CASCO offer
# ---------------------------
@router.post("/upload")
async def upload_casco_offer(
    file: UploadFile,
    insurer_name: str = Form(...),
    reg_number: str = Form(...),
    inquiry_id: Optional[int] = Form(None),
    conn = Depends(get_db),
):
    """
    Upload and process a single CASCO PDF offer.
    
    Steps:
    1. Extract text from PDF
    2. Run GPT hybrid extraction (structured + raw_text)
    3. Normalize coverage
    4. Persist to public.offers_casco
    
    Returns inserted offer ID(s).
    """
    try:
        # Validate file type
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are supported")
        
        # Read PDF
        pdf_bytes = await file.read()
        
        # Extract and normalize (sync function)
        extraction_results = process_casco_pdf(
            file_bytes=pdf_bytes,
            insurer_name=insurer_name,
            pdf_filename=file.filename,
        )
        
        # Map to DB records and save
        inserted_ids = []
        
        for result in extraction_results:
            coverage = result.coverage
            
            # Extract financial fields from GPT result
            premium_total_str = coverage.premium_total if hasattr(coverage, 'premium_total') else None
            insured_amount_str = coverage.insured_amount if hasattr(coverage, 'insured_amount') else None
            period_str = coverage.period if hasattr(coverage, 'period') else "12 mēneši"
            
            # Convert to Decimal (handle "-" and non-numeric values)
            def to_decimal(val):
                if not val or val == "-":
                    return None
                try:
                    # Remove currency symbols and spaces
                    cleaned = val.replace("EUR", "").replace("€", "").replace(" ", "").strip()
                    return Decimal(cleaned)
                except:
                    return None
            
            premium_total_decimal = to_decimal(premium_total_str)
            insured_amount_decimal = to_decimal(insured_amount_str)
            
            # Build record
            offer_record = CascoOfferRecord(
                insurer_name=insurer_name,
                reg_number=reg_number,
                inquiry_id=inquiry_id,
                insured_entity=None,
                insured_amount=insured_amount_decimal,
                currency="EUR",
                territory=coverage.Teritorija if coverage.Teritorija and coverage.Teritorija != "-" else None,
                period=period_str,  # "12 mēneši"
                premium_total=premium_total_decimal,
                premium_breakdown=None,
                coverage=coverage,
                raw_text=result.raw_text,
            )
            
            # Save to DB
            offer_id = _save_casco_offer_sync(conn, offer_record)
            inserted_ids.append(offer_id)
        
        return {
            "success": True,
            "inquiry_id": inquiry_id,
            "offer_ids": inserted_ids,
            "message": f"Successfully processed {len(inserted_ids)} CASCO offer(s)"
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process CASCO offer: {str(e)}")


# ---------------------------
# 2. Batch upload
# ---------------------------
@router.post("/upload/batch")
async def upload_casco_offers_batch(
    request: Request,
    reg_number: str = Form(...),
    inquiry_id: Optional[int] = Form(None),
    conn = Depends(get_db),
):
    """
    Upload multiple CASCO PDFs at once (one per insurer).
    
    Frontend sends multiple form fields:
        files=file1.pdf
        files=file2.pdf
        insurers=BALTA
        insurers=BALCIA
        insurers=IF
    
    This endpoint properly extracts repeated form fields using .getlist()
    """
    
    try:
        # FIX: Properly extract repeated form fields
        form = await request.form()
        insurers_list = form.getlist("insurers")   # Frontend sends multiple "insurers" fields
        files_list = form.getlist("files")          # List[UploadFile]
        
        if not insurers_list:
            raise HTTPException(
                status_code=400,
                detail="No insurers provided. Send multiple 'insurers' form fields."
            )
        
        if not files_list:
            raise HTTPException(
                status_code=400,
                detail="No files provided. Send multiple 'files' form fields."
            )
        
        if len(files_list) != len(insurers_list):
            raise HTTPException(
                status_code=400,
                detail=f"Files count ({len(files_list)}) and insurers count ({len(insurers_list)}) mismatch"
            )
        
        inserted_ids = []
        
        for file, insurer in zip(files_list, insurers_list):
            # Validate file type
            if not file.filename.lower().endswith('.pdf'):
                continue  # Skip non-PDF files
            
            # Read and process
            pdf_bytes = await file.read()
            
            extraction_results = process_casco_pdf(
                file_bytes=pdf_bytes,
                insurer_name=insurer,
                pdf_filename=file.filename,
            )
            
            # Save each extracted offer
            for result in extraction_results:
                coverage = result.coverage
                
                # Extract financial fields from GPT result
                premium_total_str = coverage.premium_total if hasattr(coverage, 'premium_total') else None
                insured_amount_str = coverage.insured_amount if hasattr(coverage, 'insured_amount') else None
                period_str = coverage.period if hasattr(coverage, 'period') else "12 mēneši"
                
                # Convert to Decimal (handle "-" and non-numeric values)
                def to_decimal(val):
                    if not val or val == "-":
                        return None
                    try:
                        # Remove currency symbols and spaces
                        cleaned = val.replace("EUR", "").replace("€", "").replace(" ", "").strip()
                        return Decimal(cleaned)
                    except:
                        return None
                
                premium_total_decimal = to_decimal(premium_total_str)
                insured_amount_decimal = to_decimal(insured_amount_str)
                
                offer_record = CascoOfferRecord(
                    insurer_name=insurer,
                    reg_number=reg_number,
                    inquiry_id=inquiry_id,
                    insured_entity=None,
                    insured_amount=insured_amount_decimal,
                    currency="EUR",
                    territory=coverage.Teritorija if coverage.Teritorija and coverage.Teritorija != "-" else None,
                    period=period_str,  # "12 mēneši"
                    premium_total=premium_total_decimal,
                    premium_breakdown=None,
                    coverage=coverage,
                    raw_text=result.raw_text,
                )
                
                offer_id = _save_casco_offer_sync(conn, offer_record)
                inserted_ids.append(offer_id)
        
        return {
            "success": True,
            "inquiry_id": inquiry_id,
            "offer_ids": inserted_ids,
            "total_offers": len(inserted_ids)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch upload failed: {str(e)}")


# ---------------------------
# 3. Compare by inquiry
# ---------------------------
@router.get("/inquiry/{inquiry_id}/compare")
async def casco_compare_by_inquiry(
    inquiry_id: int,
    conn = Depends(get_db),
):
    """
    Get CASCO comparison matrix for all offers in an inquiry.
    
    Returns:
        - offers: Raw offer data with metadata
        - comparison: Structured comparison matrix for frontend rendering
    """
    try:
        raw_offers = _fetch_casco_offers_by_inquiry_sync(conn, inquiry_id)
        
        if not raw_offers:
            return {
                "offers": [],
                "comparison": None,
                "message": "No CASCO offers found for this inquiry"
            }
        
        # ✅ FIX: Pass raw_offers directly (includes premium_total, etc.)
        # Build comparison matrix
        comparison = build_casco_comparison_matrix(raw_offers)
        
        return {
            "offers": raw_offers,
            "comparison": comparison,
            "offer_count": len(raw_offers)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build comparison: {str(e)}")


# ---------------------------
# 4. Compare by vehicle reg number (DEPRECATED)
# ---------------------------
@router.get("/vehicle/{reg_number}/compare", deprecated=True)
async def casco_compare_by_vehicle(
    reg_number: str,
    conn = Depends(get_db),
):
    """
    [DEPRECATED] Get CASCO comparison matrix for all offers for a specific vehicle.
    
    ⚠️ DEPRECATED: This endpoint is deprecated and should not be used by frontend.
    Use GET /casco/inquiry/{inquiry_id}/compare instead.
    
    This endpoint fetches offers across multiple inquiries for a vehicle,
    which is not the intended behavior. Frontend should use inquiry-based comparison.
    """
    try:
        raw_offers = _fetch_casco_offers_by_reg_number_sync(conn, reg_number)
        
        if not raw_offers:
            return {
                "offers": [],
                "comparison": None,
                "message": f"No CASCO offers found for vehicle {reg_number}"
            }
        
        # ✅ FIX: Pass raw_offers directly (includes premium_total, etc.)
        # Build comparison matrix
        comparison = build_casco_comparison_matrix(raw_offers)
        
        return {
            "offers": raw_offers,
            "comparison": comparison,
            "offer_count": len(raw_offers)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build comparison: {str(e)}")


# ---------------------------
# 5. Raw offers by inquiry
# ---------------------------
@router.get("/inquiry/{inquiry_id}/offers")
async def casco_offers_by_inquiry(
    inquiry_id: int,
    conn = Depends(get_db),
):
    """
    Get raw CASCO offers for an inquiry without comparison matrix.
    
    Returns all offer data including metadata, coverage, and raw_text.
    """
    try:
        offers = _fetch_casco_offers_by_inquiry_sync(conn, inquiry_id)
        return {
            "offers": offers,
            "count": len(offers)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch offers: {str(e)}")


# ---------------------------
# 6. Raw offers by vehicle (DEPRECATED)
# ---------------------------
@router.get("/vehicle/{reg_number}/offers", deprecated=True)
async def casco_offers_by_vehicle(
    reg_number: str,
    conn = Depends(get_db),
):
    """
    [DEPRECATED] Get raw CASCO offers for a vehicle without comparison matrix.
    
    ⚠️ DEPRECATED: This endpoint is deprecated and should not be used by frontend.
    Use GET /casco/inquiry/{inquiry_id}/offers instead.
    
    Returns all offer data including metadata, coverage, and raw_text.
    """
    try:
        offers = _fetch_casco_offers_by_reg_number_sync(conn, reg_number)
        return {
            "offers": offers,
            "count": len(offers)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch offers: {str(e)}")

