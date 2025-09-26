# app/routes/ingest.py
from fastapi import APIRouter, UploadFile, File
from sqlalchemy import create_engine
import os

from app.gpt_extractor import extract_offer_from_pdf_bytes
from app.services.persist_offers import persist_offers

router = APIRouter(prefix="/ingest", tags=["ingest"])
engine = create_engine(os.environ["DATABASE_URL"], future=True)

@router.post("/pdf")
async def ingest_pdf(file: UploadFile = File(...)):
    pdf_bytes = await file.read()
    normalized = extract_offer_from_pdf_bytes(pdf_bytes, document_id=file.filename)
    count = len(normalized.get("programs") or [])
    print(f"[ingest] programs detected: {count} -> {[p.get('program_code') for p in normalized.get('programs',[])]}")
    ids = persist_offers(engine, file.filename, normalized)
    return {"inserted": len(ids), "ids": ids, "filename": file.filename}
