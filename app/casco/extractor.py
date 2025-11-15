"""
CASCO Extraction Module - ISOLATED from HEALTH logic

This module handles CASCO (car insurance) PDF extraction using OpenAI Chat Completions API.

OPENAI CALL SITES:
- extract_casco_offers_from_text() → client.chat.completions.create()
  Uses model "gpt-4o" with response_format={"type": "json_object"}

KEY FUNCTIONS:
- _safe_parse_casco_json(): Robust JSON parser with repair heuristics
- extract_casco_offers_from_text(): Main extraction function (called by service.py)
- _ensure_structured_field(): Defensive validation for offer structure

HEALTH EXTRACTION: Completely separate - see app/gpt_extractor.py
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from pydantic import BaseModel, ValidationError

from .schema import CascoCoverage


class CascoExtractionResult(BaseModel):
    """
    Hybrid extraction result:
    - coverage: structured data persisted/compared in the system
    - raw_text: source paragraph(s) GPT used to derive the data (for debugging, audits, QA)
    """
    coverage: CascoCoverage
    raw_text: str


def _get_openai_client():
    """
    Returns the shared OpenAI client.

    NOTE:
    -----
    Adjust this import to match your existing implementation.
    In many setups, `app/services/openai_client.py` exposes a `client` instance.

    Example if needed:
        from app.services.openai_client import client
        return client
    """
    from app.services import openai_client  # type: ignore[attr-defined]
    return getattr(openai_client, "client")
    # If your code uses get_openai_client(), change to:
    # from app.services.openai_client import get_openai_client
    # return get_openai_client()


# Removed: _build_casco_json_schema() - No longer needed with responses.parse()


def _safe_parse_casco_json(raw: str) -> dict:
    """
    Robust JSON parser for CASCO extraction - tries hard to recover from common issues.
    
    Steps:
    1. Strip code fences (```json, ```)
    2. Extract content between first '{' and last '}'
    3. Try json.loads() directly
    4. If it fails, apply cosmetic fixes:
       - Remove trailing commas before } or ]
       - Remove common control characters
    5. If still fails, raise ValueError with preview
    
    This is CASCO-specific and does NOT affect HEALTH extraction.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty JSON string from model")
    
    # Step 1: Strip code fences if present
    cleaned = raw.strip()
    
    # Remove markdown code blocks
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove lines that start with ``` (opening and closing)
        cleaned = "\n".join(
            line for line in lines 
            if not line.strip().startswith("```")
        ).strip()
    
    # Step 2: Extract JSON object (from first { to last })
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    
    if first_brace == -1 or last_brace == -1 or last_brace < first_brace:
        preview = cleaned[:500] if len(cleaned) > 500 else cleaned
        raise ValueError(
            f"No valid JSON object found in model output. "
            f"Preview (first 500 chars): {preview}"
        )
    
    cleaned = cleaned[first_brace:last_brace + 1]
    
    # Step 3: Try direct parsing
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Step 4: Apply cosmetic repairs
        
        # Fix 1: Remove trailing commas before } or ]
        # Pattern: , followed by optional whitespace, then } or ]
        repaired = re.sub(r',\s*([}\]])', r'\1', cleaned)
        
        # Fix 2: Remove common control characters that might slip through
        repaired = repaired.replace('\r', '').replace('\x00', '')
        
        # Fix 3: Try to fix unescaped quotes in strings (conservative)
        # This is risky, so only do it if we detect obvious issues
        
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as e2:
            # Step 5: Give up and provide helpful error
            preview_start = cleaned[:300] if len(cleaned) > 300 else cleaned
            preview_end = cleaned[-200:] if len(cleaned) > 500 else ""
            
            char_pos = getattr(e2, 'pos', 0)
            context_start = max(0, char_pos - 100)
            context_end = min(len(repaired), char_pos + 100)
            error_context = repaired[context_start:context_end]
            
            raise ValueError(
                f"Invalid JSON from model after repair attempts. "
                f"Error: {e2}. "
                f"Preview (first 300 chars): {preview_start}... "
                f"Preview (last 200 chars): ...{preview_end}. "
                f"Error context (±100 chars around position {char_pos}): {error_context}"
            )


def _build_system_prompt() -> str:
    """
    System prompt for CASCO extraction - STRICTLY enforces JSON schema compliance.

    Key rules:
    - MUST return valid JSON matching EXACT schema structure
    - Be objective, no marketing language
    - Never hallucinate coverages – if not clearly included, set field to null
    - All insurers MUST be comparable across the same field set
    - ALWAYS include "offers" array with "structured" and "raw_text" fields
    """
    return (
        "You are an expert CASCO (car insurance) extraction engine for Latvian PDFs.\n\n"
        "OUTPUT FORMAT - YOU MUST RETURN ONLY VALID JSON:\n"
        "{\n"
        '  "offers": [\n'
        "    {\n"
        '      "structured": { ...CascoCoverage fields... },\n'
        '      "raw_text": "1-3 sentence summary"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "CRITICAL RULES:\n"
        "1. Return a SINGLE JSON object - no markdown, no commentary, no text before or after\n"
        "2. The top-level object MUST have an 'offers' array\n"
        "3. Each offer MUST have 'structured' and 'raw_text' keys\n"
        "4. Include ALL CascoCoverage fields in 'structured' - use null if not found\n"
        "5. NEVER omit a field - always include it with null if unknown\n"
        "6. Keep raw_text SHORT (1-3 sentences max) - NOT the entire document\n\n"
        "EXTRACTION RULES:\n"
        "- Be objective and neutral\n"
        "- Booleans: true ONLY if coverage explicitly included, otherwise null\n"
        "- Numbers: parse as numeric values in EUR, or null\n"
        "- Strings: short and precise\n"
        "- If document has one offer, return single-element array"
    )


def _build_user_prompt(pdf_text: str, insurer_name: str, pdf_filename: Optional[str]) -> str:
    """
    User message that gives the raw PDF text and asks for STRICTLY structured JSON.

    Emphasizes:
    - EXACT JSON structure required
    - Each PDF usually represents a single offer from a single insurer
    - All fields from CascoCoverage must be present (null if not found)
    - No additional text outside JSON
    """
    filename_part = f" (file: {pdf_filename})" if pdf_filename else ""
    return (
        f"Extract CASCO offer from insurer '{insurer_name}'{filename_part}.\n\n"
        f"PDF TEXT:\n{pdf_text}\n\n"
        "Return ONLY a JSON object with this structure:\n"
        "{\n"
        '  "offers": [\n'
        "    {\n"
        '      "structured": {\n'
        f'        "insurer_name": "{insurer_name}",\n'
        f'        "pdf_filename": "{pdf_filename or ""}",\n'
        '        "damage": true/false/null,\n'
        '        "theft": true/false/null,\n'
        '        "territory": "Latvija"/null,\n'
        '        "insured_value_eur": 15000/null,\n'
        '        ... (ALL CascoCoverage fields)\n'
        "      },\n"
        '      "raw_text": "Short 1-3 sentence summary of key coverage"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "RULES:\n"
        "- Include ALL fields (use null if not found)\n"
        "- Keep raw_text SHORT (1-3 sentences max, NOT full document text)\n"
        "- Booleans: true only if explicitly covered\n"
        "- Numbers: parse as numbers in EUR\n"
        "- Return ONLY JSON - no markdown, no extra text"
    )


def _ensure_structured_field(offer_dict: dict, insurer_name: str, pdf_filename: Optional[str]) -> dict:
    """
    Defensive logic: ensures 'structured' field exists and has required metadata.
    If missing or incomplete, creates minimal valid structure with all fields as null.
    """
    if "structured" not in offer_dict or not isinstance(offer_dict["structured"], dict):
        # Create minimal valid structured object with all fields as null
        offer_dict["structured"] = {
            "insurer_name": insurer_name,
            "product_name": None,
            "offer_id": None,
            "pdf_filename": pdf_filename,
            # All other fields will be None by default due to Pydantic Optional
        }
    else:
        # Ensure metadata is present
        offer_dict["structured"].setdefault("insurer_name", insurer_name)
        if pdf_filename:
            offer_dict["structured"].setdefault("pdf_filename", pdf_filename)
    
    # Ensure raw_text exists
    if "raw_text" not in offer_dict:
        offer_dict["raw_text"] = ""
    
    return offer_dict


def extract_casco_offers_from_text(
    pdf_text: str,
    insurer_name: str,
    pdf_filename: Optional[str] = None,
    model: str = "gpt-4o",
    max_retries: int = 2,
) -> List[CascoExtractionResult]:
    """
    Core hybrid extractor using OpenAI Chat Completions API (SDK 1.52.0).
    
    ROBUST EXTRACTION with:
    - Strict schema enforcement via prompts
    - Retry mechanism for failures
    - Defensive key validation
    - Pydantic validation
    - Automatic field population for missing data
    
    This function is PURE w.r.t. HEALTH logic: it only knows about CASCO and CascoCoverage.
    """
    client = _get_openai_client()

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(pdf_text=pdf_text, insurer_name=insurer_name, pdf_filename=pdf_filename)

    # Define response structure using Pydantic models for validation
    class Offer(BaseModel):
        structured: CascoCoverage
        raw_text: str

    class ResponseRoot(BaseModel):
        offers: List[Offer]

    last_error: Optional[Exception] = None

    # ---- Retry loop for robustness ----
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )

            # Get raw response
            raw_content = (resp.choices[0].message.content or "").strip()
            
            if not raw_content:
                raise ValueError("Empty response from model")
            
            # Use robust parser (handles markdown, trailing commas, etc.)
            payload = _safe_parse_casco_json(raw_content)
            
            # Defensive validation: ensure "offers" key exists
            if "offers" not in payload:
                raise ValueError("Response missing 'offers' key")
            
            if not isinstance(payload["offers"], list):
                raise ValueError("'offers' must be a list")
            
            if len(payload["offers"]) == 0:
                raise ValueError("'offers' array is empty")
            
            # Defensive logic: ensure each offer has 'structured' and 'raw_text'
            valid_offers = []
            for i, offer in enumerate(payload["offers"]):
                if not isinstance(offer, dict):
                    print(f"[WARN] CASCO offer {i} is not a dict, skipping")
                    continue
                
                # Ensure required keys exist
                offer = _ensure_structured_field(offer, insurer_name, pdf_filename)
                
                # Try to validate this single offer against Pydantic
                try:
                    validated_offer = Offer(**offer)
                    valid_offers.append(validated_offer)
                except ValidationError as ve:
                    print(f"[WARN] CASCO offer {i} failed Pydantic validation: {ve}")
                    # Continue with other offers rather than failing completely
                    continue
            
            if len(valid_offers) == 0:
                raise ValueError("All offers failed validation")
            
            # Create ResponseRoot with valid offers
            root = ResponseRoot(offers=valid_offers)
            
            # If we got here, extraction succeeded
            break

        except ValueError as e:
            # Enhance error message with context
            error_msg = f"CASCO extraction failed (attempt {attempt + 1}/{max_retries + 1}): {str(e)}"
            last_error = ValueError(error_msg)
            
            if attempt < max_retries:
                print(f"[RETRY] {error_msg}")
                continue
            raise last_error

        except Exception as e:
            error_msg = f"CASCO extraction unexpected error (attempt {attempt + 1}/{max_retries + 1}): {type(e).__name__}: {str(e)}"
            last_error = ValueError(error_msg)
            
            if attempt < max_retries:
                print(f"[RETRY] {error_msg}")
                continue
            raise last_error

    # Build results
    results: List[CascoExtractionResult] = []

    for offer in root.offers:
        # Ensure metadata is properly set (redundant but safe)
        offer.structured.insurer_name = insurer_name
        if pdf_filename:
            offer.structured.pdf_filename = pdf_filename

        results.append(
            CascoExtractionResult(
                coverage=offer.structured,
                raw_text=offer.raw_text or "",
            )
        )

    return results


