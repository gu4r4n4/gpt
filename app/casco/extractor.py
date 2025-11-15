from __future__ import annotations

import json
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


def _build_casco_json_schema() -> dict:
    """
    Build JSON schema for the Responses API using the existing CascoCoverage model.
    The top-level shape is:

    {
      "offers": [
        {
          "structured": CascoCoverage JSON Schema,
          "raw_text": "source snippet where info comes from"
        }
      ]
    }
    """
    coverage_schema = CascoCoverage.model_json_schema(ref_template="#/components/schemas/{model}")
    return {
        "name": "casco_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "offers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "structured": coverage_schema,
                            "raw_text": {
                                "type": "string",
                                "description": (
                                    "Exact paragraph(s) or section(s) from the PDF where "
                                    "you found information for this offer."
                                ),
                            },
                        },
                        "required": ["structured", "raw_text"],
                    },
                },
            },
            "required": ["offers"],
        },
    }


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
    Core hybrid extractor: calls OpenAI Responses API and returns structured coverage + raw_text.

    This function is PURE w.r.t. HEALTH logic: it only knows about CASCO and CascoCoverage.
    """
    client = _get_openai_client()

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(pdf_text=pdf_text, insurer_name=insurer_name, pdf_filename=pdf_filename)
    json_schema = _build_casco_json_schema()

    # ---- OpenAI Responses API call ----
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": json_schema,
        },
    )

    # Parse JSON payload from the first output block
    try:
        content_block = response.output[0].content[0]
        if getattr(content_block, "type", None) == "output_text":
            raw_json = content_block.text
        else:
            # Fallback if the SDK object differs slightly
            raw_json = getattr(content_block, "text", None) or str(content_block)
    except (AttributeError, IndexError, KeyError) as e:
        raise ValueError(f"Unexpected OpenAI response format for CASCO extraction: {e}") from e

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to decode CASCO extraction JSON: {e}") from e

    offers_data = payload.get("offers", [])
    if not isinstance(offers_data, list):
        raise ValueError("CASCO extraction JSON must contain 'offers' as a list.")

    results: List[CascoExtractionResult] = []

    for offer in offers_data:
        structured = offer.get("structured", {})
        raw_text_section = offer.get("raw_text", "")

        # Inject known metadata into the structured part
        structured.setdefault("insurer_name", insurer_name)
        if pdf_filename:
            structured.setdefault("pdf_filename", pdf_filename)

        try:
            coverage = CascoCoverage(**structured)
        except ValidationError as e:
            raise ValueError(f"CASCO coverage validation error: {e}") from e

        results.append(
            CascoExtractionResult(
                coverage=coverage,
                raw_text=raw_text_section or "",
            )
        )

    return results


