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
    System prompt for CASCO extraction.

    Key rules:
    - Be objective, no marketing language.
    - Never hallucinate coverages – if not clearly included, leave field null.
    - All insurers MUST be comparable across the same field set.
    """
    return (
        "You are an expert CASCO (car insurance) offers extraction engine. "
        "Your job is to extract structured data about insurance coverages from Latvian PDF offers. "
        "You must:\n"
        "- Be fully objective and neutral.\n"
        "- Extract only what is clearly stated in the document.\n"
        "- If a coverage, limit, or feature is not explicitly present, set that field to null.\n"
        "- Do NOT guess or infer missing data.\n"
        "- Map each benefit or feature to the most appropriate field in the provided schema.\n"
        "- Preserve insurer-specific nuances only in the raw_text explanation.\n"
        "- If the document contains only one offer, return a single-element offers array."
    )


def _build_user_prompt(pdf_text: str, insurer_name: str, pdf_filename: Optional[str]) -> str:
    """
    User message that gives the raw PDF text and asks for structured JSON.

    We clearly explain that:
    - Each PDF usually represents a single offer from a single insurer.
    - All fields from CascoCoverage are allowed to be null if absent.
    """
    filename_part = f" (file: {pdf_filename})" if pdf_filename else ""
    return (
        f"Extract CASCO insurance offer data for insurer '{insurer_name}'{filename_part} "
        f"from the following PDF text.\n\n"
        f"PDF TEXT START:\n{pdf_text}\nPDF TEXT END.\n\n"
        "Instructions:\n"
        "- The document typically contains ONE CASCO offer for this insurer.\n"
        "- Populate all relevant fields of the provided CASCO schema. "
        "If a specific coverage, limit, or feature is not clearly specified, set its value to null.\n"
        "- Booleans must be true only if the coverage is explicitly included.\n"
        "- Numeric fields (limits, sums, deductibles) must be parsed in EUR.\n"
        "- 'territory' should be a short string summarizing the territory (e.g. 'Latvija', 'Baltija', 'Eiropa', 'Eiropa + NVS').\n"
        "- 'insured_value_type' must be one of: 'market', 'new', or 'other'. Use 'other' only if wording is unclear.\n"
        "- In 'raw_text', include the precise sentences, bullet points or table snippet where you found the data. "
        "This is used for audit and debugging.\n"
        "- Return JSON that matches the response JSON schema exactly."
    )


def extract_casco_offers_from_text(
    pdf_text: str,
    insurer_name: str,
    pdf_filename: Optional[str] = None,
    model: str = "gpt-5.1",
) -> List[CascoExtractionResult]:
    """
    Core hybrid extractor using OpenAI Chat Completions API (SDK 1.52.0).
    
    Uses client.chat.completions.create() with JSON response format.
    Validates output against Pydantic schema for type safety.
    
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

    # ---- Use chat.completions.create() - the actual API in SDK 1.52.0 ----
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
        raw = (resp.choices[0].message.content or "").strip() or "{}"
        payload = json.loads(raw)
        
        # Validate against Pydantic model
        root = ResponseRoot(**payload)

    except Exception as e:
        raise ValueError(f"CASCO extraction failed: {e}") from e

    results: List[CascoExtractionResult] = []

    for offer in root.offers:
        # Inject known metadata into the structured coverage
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


