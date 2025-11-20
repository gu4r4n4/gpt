"""
CASCO API Endpoints
Handles PDF upload, extraction, normalization, persistence, and comparison.
Uses internal job ID system (UUID strings) - NO inquiry_id dependency.
"""

from __future__ import annotations

import os
import uuid
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
# Helper: Job and Offer Management (sync adapters)
# ---------------------------
def _create_casco_job_sync(conn, reg_number: str) -> str:
    """
    Create a new CASCO job entry with UUID identifier.
    Returns the new job ID (UUID string).
    """
    job_id = str(uuid.uuid4())
    
    sql = """
    INSERT INTO public.casco_jobs (casco_job_id, reg_number, product_line)
    VALUES (%s, %s, 'casco')
    RETURNING casco_job_id;
    """
    
    with conn.cursor() as cur:
        cur.execute(sql, (job_id, reg_number))
        row = cur.fetchone()
        conn.commit()
        return row["casco_job_id"]


def _save_casco_offer_sync(
    conn,
    offer: CascoOfferRecord,
) -> int:
    """
    Synchronous adapter for saving CASCO offers.
    Adapts the async persistence layer to work with psycopg2.
    
    Requires casco_job_id (UUID string) to be set on the offer.
    """
    import json
    
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
        )
        row = cur.fetchone()
        conn.commit()
        return row["id"]


def _fetch_casco_offers_by_job_sync(conn, casco_job_id: str) -> List[dict]:
    """
    Fetch all CASCO offers for a job (UUID string).
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
    WHERE casco_job_id = %s
      AND product_line = 'casco'
    ORDER BY created_at DESC;
    """
    
    with conn.cursor() as cur:
        cur.execute(sql, (casco_job_id,))
        return cur.fetchall()


def _fetch_casco_offers_by_reg_number_sync(conn, reg_number: str) -> List[dict]:
    """
    DEPRECATED: Fetch all CASCO offers for a vehicle.
    
    This function is kept for backwards compatibility but should NOT be used.
    Use _fetch_casco_offers_by_job_sync() instead.
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
    conn = Depends(get_db),
):
    """
    Upload and process a single CASCO PDF offer.
    
    Steps:
    1. Create a new CASCO job (internal tracking with UUID)
    2. Extract text from PDF
    3. Run GPT hybrid extraction (structured + raw_text)
    4. Normalize coverage
    5. Persist to public.offers_casco with casco_job_id
    
    Returns:
    {
      "success": true,
      "casco_job_id": "<uuid-string>",
      "offer_ids": [<ids>],
      "total_offers": <int>
    }
    
    NOTE: inquiry_id is NO LONGER USED. Each upload creates a new internal job.
    """
    try:
        # Validate file type
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are supported")
        
        # Create CASCO job (internal tracking with UUID)
        casco_job_id = _create_casco_job_sync(conn, reg_number)
        
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
            insured_amount_str = coverage.insured_amount if hasattr(coverage, 'insured_amount') else "Tirgus vērtība"
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
            # insured_amount is always "Tirgus vērtība" (text, not converted to Decimal)
            
            # Build record with casco_job_id (UUID string)
            offer_record = CascoOfferRecord(
                insurer_name=insurer_name,
                reg_number=reg_number,
                casco_job_id=casco_job_id,  # UUID string
                insured_entity=None,
                insured_amount=insured_amount_str,  # Always "Tirgus vērtība"
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
            "casco_job_id": casco_job_id,  # UUID string for comparison
            "offer_ids": inserted_ids,
            "total_offers": len(inserted_ids)
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
    
    Returns:
    {
      "success": true,
      "casco_job_id": "<uuid-string>",
      "offer_ids": [<ids>],
      "total_offers": <int>
    }
    
    NOTE: inquiry_id is NO LONGER USED. Each batch upload creates a new internal job.
    All offers in the batch share the same casco_job_id.
    """
    
    try:
        # Create CASCO job for this batch (UUID - all offers will share this job ID)
        casco_job_id = _create_casco_job_sync(conn, reg_number)
        
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
                insured_amount_str = coverage.insured_amount if hasattr(coverage, 'insured_amount') else "Tirgus vērtība"
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
                # insured_amount is always "Tirgus vērtība" (text, not converted to Decimal)
                
                offer_record = CascoOfferRecord(
                    insurer_name=insurer,
                    reg_number=reg_number,
                    casco_job_id=casco_job_id,  # All offers in batch share same UUID job
                    insured_entity=None,
                    insured_amount=insured_amount_str,  # Always "Tirgus vērtība"
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
            "casco_job_id": casco_job_id,  # UUID string for comparison
            "offer_ids": inserted_ids,
            "total_offers": len(inserted_ids)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch upload failed: {str(e)}")


# ---------------------------
# 3. Compare by CASCO job
# ---------------------------
@router.get("/job/{casco_job_id}/compare")
async def casco_compare_by_job(
    casco_job_id: str,
    conn = Depends(get_db),
):
    """
    Get CASCO comparison matrix for all offers in a job (UUID string).
    
    Returns:
    {
      "offers": [...],
      "comparison": {
        "rows": [...],         // 22 rows (3 financial + 19 features)
        "columns": [...],      // insurer names
        "values": {...},       // "field::insurer": value
        "metadata": {...}      // insurer metadata
      },
      "offer_count": <int>
    }
    
    NOTE: This replaces the old inquiry-based comparison.
    Each upload creates a unique job ID that groups all offers from that batch.
    """
    try:
        raw_offers = _fetch_casco_offers_by_job_sync(conn, casco_job_id)
        
        if not raw_offers:
            return {
                "offers": [],
                "comparison": None,
                "offer_count": 0,
                "message": "No CASCO offers found for this job"
            }
        
        # Build comparison matrix (22 rows: 3 financial + 19 coverage fields)
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
    Use GET /casco/job/{casco_job_id}/compare instead.
    
    This endpoint fetches offers across multiple jobs for a vehicle,
    which is not the intended behavior. Frontend should use job-based comparison.
    """
    try:
        raw_offers = _fetch_casco_offers_by_reg_number_sync(conn, reg_number)
        
        if not raw_offers:
            return {
                "offers": [],
                "comparison": None,
                "offer_count": 0,
                "message": f"No CASCO offers found for vehicle {reg_number}"
            }
        
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
# 5. Raw offers by CASCO job
# ---------------------------
@router.get("/job/{casco_job_id}/offers")
async def casco_offers_by_job(
    casco_job_id: str,
    conn = Depends(get_db),
):
    """
    Get raw CASCO offers for a job (UUID string) without comparison matrix.
    
    Returns all offer data including metadata, coverage, and raw_text.
    
    NOTE: This replaces the old inquiry-based offers endpoint.
    """
    try:
        offers = _fetch_casco_offers_by_job_sync(conn, casco_job_id)
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
    Use GET /casco/job/{casco_job_id}/offers instead.
    
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
