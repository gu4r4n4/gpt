from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel

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
        "You are an expert CASCO (car insurance) offers extraction engine.\n"
        "Your job is to extract structured data about insurance coverages from Latvian PDF offers.\n\n"
        "CRITICAL OUTPUT REQUIREMENTS:\n"
        "1. You MUST return ONLY valid JSON - no markdown, no explanations, no extra text\n"
        "2. The JSON MUST have this EXACT structure:\n"
        "   {\n"
        '     "offers": [\n'
        "       {\n"
        '         "structured": { ...all CascoCoverage fields... },\n'
        '         "raw_text": "exact quotes from PDF where you found the data"\n'
        "       }\n"
        "     ]\n"
        "   }\n"
        "3. The 'structured' object MUST include ALL fields from CascoCoverage schema\n"
        "4. If a field value is unknown or not found, set it to null (not omit it)\n"
        "5. The 'offers' array MUST contain at least one offer\n\n"
        "EXTRACTION RULES:\n"
        "- Be fully objective and neutral\n"
        "- Extract ONLY what is clearly stated in the document\n"
        "- If a coverage, limit, or feature is not explicitly present, set that field to null\n"
        "- Do NOT guess or infer missing data\n"
        "- Booleans must be true ONLY if the coverage is explicitly included, otherwise null\n"
        "- Numeric fields must be parsed as numbers in EUR\n"
        "- Map each benefit or feature to the most appropriate field\n"
        "- Preserve insurer-specific nuances in the raw_text field\n"
        "- If the document contains only one offer, return a single-element offers array"
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
        f"Extract CASCO insurance offer data for insurer '{insurer_name}'{filename_part} "
        f"from the following PDF text.\n\n"
        f"PDF TEXT START:\n{pdf_text}\nPDF TEXT END.\n\n"
        "REQUIRED OUTPUT FORMAT (STRICT):\n"
        "{\n"
        '  "offers": [\n'
        "    {\n"
        '      "structured": {\n'
        '        "insurer_name": "' + insurer_name + '",\n'
        '        "product_name": null or "...",\n'
        '        "offer_id": null or "...",\n'
        f'        "pdf_filename": "{pdf_filename or ""}",\n'
        '        "damage": null or true/false,\n'
        '        "total_loss": null or true/false,\n'
        '        "theft": null or true/false,\n'
        "        ... (ALL 60+ CascoCoverage fields - use null if not found)\n"
        "      },\n"
        '      "raw_text": "exact quotes from PDF sections where coverage data was found"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "EXTRACTION INSTRUCTIONS:\n"
        "- The document typically contains ONE CASCO offer for this insurer\n"
        "- You MUST include ALL fields from CascoCoverage in 'structured'\n"
        "- If a specific coverage, limit, or feature is not clearly specified, set its value to null\n"
        "- NEVER omit a field - always include it with null if unknown\n"
        "- Booleans: true ONLY if coverage is explicitly included, otherwise null\n"
        "- Numeric fields (limits, sums, deductibles): parse as numbers in EUR, or null if not found\n"
        "- territory: short string like 'Latvija', 'Baltija', 'Eiropa', or null\n"
        "- insured_value_type: MUST be 'market', 'new', or 'other' (use 'other' if unclear), or null\n"
        "- raw_text: include precise sentences, bullet points or table snippets where you found the data\n"
        "- Return ONLY valid JSON - no markdown formatting, no explanations before or after"
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

            # Parse JSON response
            raw = (resp.choices[0].message.content or "").strip()
            
            if not raw or raw == "{}":
                raise ValueError("Empty response from model")
            
            # Remove markdown formatting if present
            if raw.startswith("```"):
                # Extract JSON from markdown code block
                lines = raw.split("\n")
                raw = "\n".join(line for line in lines if not line.startswith("```"))
                raw = raw.strip()
            
            payload = json.loads(raw)
            
            # Defensive validation: ensure "offers" key exists
            if "offers" not in payload:
                raise ValueError("Response missing 'offers' key")
            
            if not isinstance(payload["offers"], list):
                raise ValueError("'offers' must be a list")
            
            if len(payload["offers"]) == 0:
                raise ValueError("'offers' array is empty")
            
            # Defensive logic: ensure each offer has 'structured' and 'raw_text'
            for i, offer in enumerate(payload["offers"]):
                if not isinstance(offer, dict):
                    raise ValueError(f"Offer {i} is not a dict")
                payload["offers"][i] = _ensure_structured_field(offer, insurer_name, pdf_filename)
            
            # Validate against Pydantic model
            root = ResponseRoot(**payload)
            
            # If we got here, extraction succeeded
            break

        except json.JSONDecodeError as e:
            last_error = ValueError(f"Invalid JSON from model (attempt {attempt + 1}/{max_retries + 1}): {e}")
            if attempt < max_retries:
                continue
            raise last_error

        except Exception as e:
            last_error = ValueError(f"CASCO extraction failed (attempt {attempt + 1}/{max_retries + 1}): {e}")
            if attempt < max_retries:
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


