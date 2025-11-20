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
    SIMPLIFIED 21-FIELD SYSTEM PROMPT for CASCO extraction.
    
    Returns the exact prompt provided by the user, implementing:
    - 21 Latvian-named fields
    - "v" (covered), "-" (not covered), or descriptive values
    - Special rules for Vandālisms, Stiklojums, etc.
    """
    return """You are a strict CASCO insurance PDF parser.

You receive the FULL TEXT of one CASCO insurance offer (for one insurer). Your task is to read the whole document and return a SINGLE JSON object with exactly 21 fields describing coverage.

IMPORTANT GENERAL RULES
- Work ONLY from the provided document text.
- Think like an underwriter, not a marketing writer.
- If coverage exists (risk is insured) in any positive/covered context → mark "v".
- If risk is clearly NOT covered, or only appears in exclusions, disclaimers, or is not mentioned at all → mark "-".
- For some fields you must extract a VALUE (limit, number of days, EUR amount). If a value is clearly present, return that value as a human-readable string (e.g. "15 dienas / 30 EUR dienā", "Eiropa", "160 EUR").
- If a field expects a value but the document clearly includes coverage with no obvious numeric / limit → return "v".
- NEVER invent values. If unsure about the exact number/limit, but the coverage is present, just use "v".
- If coverage is not present at all, or only in exclusions, return "-".
- Output MUST be pure JSON, no comments, no explanations, no extra keys.

RETURN EXACTLY THIS JSON SHAPE (24 KEYS)

Return a single JSON object:

{
  "Bojājumi": "...",
  "Bojāeja": "...",
  "Zādzība": "...",
  "Apzagšana": "...",
  "Teritorija": "...",
  "Pašrisks – bojājumi": "...",
  "Stiklojums bez pašriska": "...",
  "Maiņas / nomas auto (dienas)": "...",
  "Palīdzība uz ceļa": "...",
  "Hidrotrieciens": "...",
  "Personīgās mantas / bagāža": "...",
  "Atslēgu zādzība/atjaunošana": "...",
  "Degvielas sajaukšana/tīrīšana": "...",
  "Riepas / diski": "...",
  "Numurzīmes": "...",
  "Nelaimes gad. vadīt./pasažieriem": "...",
  "Sadursme ar dzīvnieku": "...",
  "Uguns / dabas stihijas": "...",
  "Vandālisms": "...",
  "Remonts klienta servisā": "...",
  "Remonts pie dīlera": "...",
  "premium_total": "...",
  "insured_amount": "...",
  "period": "..."
}

Allowed values per field:
- For boolean-type coverage: "v" or "-"
- For value-type coverage: either a non-empty string with the value / limit / description, OR "v" if coverage exists but no clear numeric/territorial value is extractable, OR "-" if no coverage.
- For premium_total and insured_amount: numeric string or "-"
- For period: always "12 mēneši"

DETAILED FIELD RULES

1. "Bojājumi"
Goal: is damage coverage included?

Search for words like:
- "Bojājumi", "Avārija"

If the document lists "Bojājumi" or similar in covered risks (e.g., "Bojājumi, Bojāeja …"), mark:
- "Bojājumi": "v"

If not mentioned in covered risks → "Bojājumi": "-".

2. "Bojāeja"
Goal: is total loss / destruction covered?

Search for:
- "Bojāeja", "bojāejas", "Pilnīga bojāeja"

If present in coverage lists → "Bojāeja": "v", else "-".

3. "Zādzība"
Goal: theft coverage.

Search for:
- "Zādzība", "Zādzības risks", "Zādzība un laupīšana", "Zādzībai"

If coverage present → "Zādzība": "v", else "-".

4. "Apzagšana"
Goal: burglary / robbery coverage (separate from theft of whole car).

Search for:
- "Apzagšana", "Laupīšanas risks", "laupīšana"

If coverage present → "Apzagšana": "v", else "-".

5. "Teritorija"
Goal: extract insurance territory text.

Search for:
- "Teritorija", "Apdrošināšanas teritorija",
  "Apdrošināšanas līguma darbības teritorija",
  "Teritoriālais segums", " Latvija,"
Look especially near premium/variants tables.

If found, return the CLEANED HUMAN STRING, e.g.:
- "Eiropa"
- "Eiropa (izņemot Baltkrieviju, Krieviju, Moldovu un Ukrainu)"
- "Latvija"
- "Eiropa bez NVS"
If not clearly mentioned → "Teritorija": "-".

6. "Pašrisks – bojājumi"
Goal: self-risk (deductible) for damage.

Search for:
- "Paša risks", "Pašrisks bojājumiem", "Paša risks bojājumiem",
  "Pašrisks", "Bojājumiem, apzagšanai", "Bojājumiem",
  "Klienta pašrisks", "Pamata Pašrisks"

Extract the main damage deductible for this offer (or the default/most relevant variant), e.g.:
- "100 EUR"
- "160 EUR"
- "0 (140) EUR / 150 EUR / 200 EUR"
If cannot identify any damage deductible but coverage exists → "v".
If not present at all → "-".

7. "Stiklojums bez pašriska"
Goal: is glass covered with 0% / 0 EUR or special favorable condition.

Search for any of the following or very similar:
- "Stiklojums bez paša riska bojājumiem"
- "Pirmajam stiklu plīsuma riska gadījumam tiek noteikts 0% pašrisks"
- "Visu salona stiklu apdrošināšana bez paša riska neierobežotam gadījumu skaitam"
- "Stiklojumam", "Stiklojums"
- "Pašrisks vējstiklam 0.00 EUR"
- "Pašrisks Stiklojuma bojājumiem 0 €"
- Balcia special case:
  "Pirmajam stiklu plīsuma riska gadījumam tiek noteikts 0% pašrisks, ja stikla nomaiņa tiek veikta Balcia norādītā remontuzņēmumā"
- BTA special case:
  "Stiklu plīsuma riska gadījumam tiek piemērots šajā apdrošināšanas polisē norādītais bojājumu pašrisks bez gadījumu skaita ierobežojuma, ja stikla nomaiņa tiek veikta klienta izvēlētā remontuzņēmumā un ar šo apdrošināšanas līgumu (polisi) ir iegādāta papildus apdrošināšanas aizsardzība 'Remonts klienta izvēlētā servisā'"

If ANY of these or equivalent meaning appears in coverage/special conditions:
- "Stiklojums bez pašriska": "v"
If no glass special coverage → "-".

8. "Maiņas / nomas auto (dienas)"
Goal: replacement / rental car coverage and value.

Search for:
- "Aizvietošanas auto", "Bezmaksas maiņas auto (A variants)",
- "Maiņas auto līdz 15 dienām ar limitu 30.00 EUR diennaktī",
- "Maiņas auto nodrošināšanas apdrošināšana",
- "Transportlīdzekļa aizvietošana (20 dienas / 30 EUR dienā)",
- "Transportlīdzekļa aizvietošanas apdrošināšana",
- "Auto aizvietošana",
- "Nomas transportlīdzeklis līdz 30 dienām (kompaktā klase)"

If there is a clear number of days and/or daily limit, return it as string:
- e.g. "15 dienas / 30 EUR dienā", "20 dienas / 30 EUR dienā", "līdz 30 dienām (kompaktā klase)"
If coverage exists but no clear numeric/value → "v".
If no coverage → "-".

9. "Palīdzība uz ceļa"
Goal: roadside assistance.

Search for:
- "Palīdzība uz ceļa",
- "Diennakts autopalīdzības pakalpojumi, ieskaitot evakuāciju Latvijas teritorijā, bez limita un Eiropas teritorijā ar limitu 1 000.00 EUR",
- "Transportlīdzekļa transportēšana pēc apdrošināšanas gadījuma, limits līdz 750.00 EUR līguma darbības laikā.",
- "Autohelp24",
- "Diennakts autopalīdzība",
- "Izdevumi nokļūšanai remonta iestādē"

If limit/value given, return that string (e.g. "LV bez limita, Eiropā 1000 EUR", "transportēšana līdz 750 EUR").
If only generic assistance mentioned but no clear limit → "v".
If no assistance → "-".

10. "Hidrotrieciens"
Goal: hydro strike risk.

Search for:
- "Hidrotrieciens", "Hidrotrieciena risks",
- "Elektriskie vai mehāniskie bojājumi hidrotrieciena dēļ bez paša riska ar limitu 7 000.00 EUR",
- "Ekstra apdrošināšana" when clearly tied to hidrotrieciens.

If coverage present with limit, return value string (e.g. "bez paša riska ar limitu 7000 EUR").
If coverage present but no limit → "v".
If absent → "-".

11. "Personīgās mantas / bagāža"
Goal: personal items / baggage risk.

Search for:
- "Personisko mantu apdrošināšana",
- "Personīgo mantu bojājumi vai zādzība",
- "Personisko mantu un bagāžas adprošināšana bez paša riska ar limitu",
- "Bezrūpības risks",
- "Mantas un inventāra apdrošināšana",
- "Mantas apdrošināšana",
- "Bagāžas apdrošināšana",
- "Personīgo mantu bojājums vai zādzība"

If limit/value is stated → return e.g. "bez paša riska ar limitu 1000 EUR".
If coverage exists but no clear limit → "v".
If absent → "-".

12. "Atslēgu zādzība/atjaunošana"
Goal: keys theft/replacement.

Search for:
- "Atslēgu aizvietošana",
- "Atslēgu zādzība",
- "Atslēgu un dokumentu atjaunošana bez paša riska vienu reizi polises darbības laikā",
- "Atslēgu atjaunošana",
- "Atslēgu risks",
- "Atslēgu zādzība vai degvielas sajaukšana"

Coverage present → return value if any (e.g. "bez paša riska 1 reizi polises laikā"), else "v".
If absent → "-".

13. "Degvielas sajaukšana/tīrīšana"
Goal: wrong fuel / fuel system cleaning.

Search for:
- "Degvielas padeves sistēmas tīrīšanas izdevumi",
- "Degvielas padeves sistēmas tīrīšanas izdevumi bez paša riska vienu reizi polises darbības",
- "Degvielas sistēmas tīrīšana",
- "Neatbilstošas degvielas iepildes risks",
- "Degvielas padeves sistēmas tīrīšana",
- "Atslēgu zādzība vai degvielas sajaukšana"

Coverage present → return value if any (e.g. "bez paša riska 1 reizi polises laikā"), else "v".
If absent → "-".

14. "Riepas / diski"
Goal: tyre and wheel damage coverage.

Search for:
- "Bojājumi riepām / diskiem no iebraukšanas bedrē bez paša riska",
- "Riepu un disku bojājumi",
- "Iebraukšana bedrē bez paša riska vienu reizi polises darbības laikā",
- "Par pirmo apdrošināšanas gadījumu, kurā bojāta ... riepa (-as) un/vai disks (-i) ... 0 EUR pašrisks",
- "Papildu nulles pašrisks transportlīdzekļa riepu un disku bojājumiem",
- "Vienas transportlīdzekļa ass visu riepu nomaiņa",
- "Riepu un numurzīmes apdrošināšana"

If coverage present → return main phrase/value (e.g. "0 EUR pašrisks pirmajam gadījumam", "Papildu nulles pašrisks riepu un disku bojājumiem"), otherwise "-".

15. "Numurzīmes"
Goal: registration plates / documents.

Search for:
- "Numura zīmes pazaudēšana / zādzība",
- "Numura zīmes atjaunošana bez paša riska vienu reizi polises darbības laikā",
- "Reģistrācijas dokumentu un numurzīmju atjaunošana",
- "Atslēgu, numuru un dokumentu apdrošināšana zādzības un nozaudēšanas gadījumam.",
- "Transportlīdzekļa numurzīme",
- "Riepu un numurzīmes apdrošināšana"

If coverage present → return phrase/value (e.g. "atjaunošana bez paša riska 1 reizi polises laikā"), else "-".

16. "Nelaimes gad. vadīt./pasažieriem"
Goal: accident insurance for driver and passengers.

Search for:
- "Transportlīdzekļa vadītājs un pasažieri",
- "Transportlīdzekļa vadītājs un četri pasažieri",
- "Nelaimes gadījumu apdrošināšana transportlīdzekļa vadītājam un pasažieriem 7500.00 EUR",
- "Vadītāja un pasažieru nelaimes gadījumu apdrošināšana",
- "Transportlīdzekļa vadītāja un pasažieru nelaimes gadījumu apdrošināšana"

If present, return the coverage sums string:
- e.g. "Nāve 2500 EUR, invaliditāte 5000 EUR, traumas 5000 EUR"
If clearly not included → "-".

17. "Sadursme ar dzīvnieku"
Goal: collision with animal (often with special self-risk).

Search for:
- "Sadursme ar dzīvnieku bez pašriska bojājumiem",
- "Pirmajam sadursmes gadījumam ar dzīvnieku netiek piemērots bojājumu pašrisks ...",
- "Sadursme ar dzīvnieku bez paša riska vienu reizi polises darbības laikā",
- "Apdrošināšanas līguma darbības laikā par pirmo ... sadursmes gadījumu ar dzīvnieku tiek noteikts 0 EUR pašrisks",
- phrases where "Bojājumi, Bojāeja" list includes "dzīvnieku nodarīto bojājumu",
- "Sadursme ar dzīvnieku bez pašriska",
- "Sadursme ar dzīvnieku"

If such coverage exists (even with conditions) → "v", else "-".

18. "Uguns / dabas stihijas"
Goal: fire & natural perils.

Search for:
- "Bojājumi, tajā skaitā (bet ne tikai) CSNg, ugunsgrēks, dabas stihijas, krītoši priekšmeti, trešo personu prettiesiska rīcība",
- "Dabas stihijas risks",
- similar phrasing indicating damage or total loss from fire or natural perils,
- "Ceļu satiksmes negadījums, uguns risks, dabas spēku iedarbība, dažādu priekšmetu un vielu iedarbība, iegruvums, trešo personu prettiesiska darbība, dzīvnieku, putnu nodarīti bojājumi.",
- "no dabas stihijas iedarbības;",
- "Dabas stihijas"

If present → "v", else "-".

19. "Vandālisms"
Goal: vandalism/third-party malicious damage.

Search for:
- "Vandālisms",
- "Bojājumi, tajā skaitā (bet ne tikai) CSNg, ugunsgrēks, dabas stihijas, krītoši priekšmeti, trešo personu prettiesiska rīcība",
- "Bojājumi" in a context of broad damage coverage including third-party malicious actions,
- "Aerogrāfija",
- "Segums ārpus ceļu satiksmes",
- "Ekstra apdrošināšana" when clearly extending to such damage.

SPECIAL RULE FOR THIS FIELD:
- If the policy clearly includes general "Bojājumi" coverage (standard CASCO damage coverage) and does NOT explicitly exclude vandalism/third-party malicious actions, then set:
  "Vandālisms": "v"
even if the exact word "vandālisms" does not appear.
- Only set "-" if damage is not covered at all or vandalism-type damage is clearly excluded.

20. "premium_total"
Goal: extract the total premium amount.

Search for:
- "Kopējā prēmija", "Apdrošināšanas prēmija", "1 maksājums",
  "Pavisam apmaksai", "Prēmija samaksai kopā", "KOPĀ", "Total premium"

Extract the numeric value with currency, e.g.:
- "450.00 EUR"
- "1480 €"
- "320.50"

If found, return the numeric string.
If not found → "premium_total": "-".

21. "insured_amount"
Goal: extract the insured sum/vehicle value.

Search for:
- "Apdrošinājuma summa", "Apdrošināšanas summa", "Transportlīdzekļa vērtība",
  "Insured sum", "Apdrošinātā summa"

Extract the numeric value, e.g.:
- "15000 EUR"
- "20000"

If found, return the numeric string.
If not found → "insured_amount": "Tirgus vērtība".

22. "period"
Goal: insurance period/duration.

ALWAYS return: "12 mēneši"

(This is the standard period for CASCO insurance in Latvia.)

23. "Remonts klienta servisā"
Goal: repair at customer's chosen service center.

Search for:
- "Remonts klienta izvēlētā servisā"
- "Remonts brīvas izvēles servisā"
- "Klienta serviss"
- "Brīvas izvēles serviss"

If this coverage option is included → return "v".
If not mentioned → return "-".

24. "Remonts pie dīlera"
Goal: repair at authorized dealer service center.

Search for:
- "Remonts dīlera servisā"
- "autorizēta dīlera serviss"
- "Remonts pie dīlera"
- "Remonts dīlerservisā"
- "Remonts dīlerī ar jaunām, oriģinālām rezerves daļām"
- "Remonts pie dīlera pēc garantijas laika beigām"

If this coverage option is included → return "v".
If not mentioned → return "-".

OUTPUT FORMAT
- Output MUST be a single valid JSON object.
- Use EXACTLY the 24 keys specified (21 coverage fields + premium_total + insured_amount + period).
- Values must be strings.
- Do NOT include any extra keys, comments, explanations, or trailing commas."""


def _build_user_prompt(pdf_text: str, insurer_name: str, pdf_filename: Optional[str]) -> str:
    """
    User message with PDF text for 24-field extraction (21 coverage + 3 financial).
    Simple and direct - just provides document text.
    """
    filename_part = f" (file: {pdf_filename})" if pdf_filename else ""
    return f"""Extract CASCO insurance offer data for insurer '{insurer_name}'{filename_part}.

DOCUMENT TEXT START:
{pdf_text}
DOCUMENT TEXT END.

Return the JSON object with exactly 24 fields (21 coverage + premium_total + insured_amount + period) as specified in the system prompt."""


def _map_json_keys_to_python(raw_json: dict) -> dict:
    """
    Maps JSON keys (with spaces, dashes, slashes) to Python-friendly attribute names.
    
    Mapping:
    - "Pašrisks – bojājumi" → "Pašrisks_bojājumi"
    - "Stiklojums bez pašriska" → "Stiklojums_bez_pašriska"
    - "Maiņas / nomas auto (dienas)" → "Maiņas_nomas_auto_dienas"
    - "Palīdzība uz ceļa" → "Palīdzība_uz_ceļa"
    - "Personīgās mantas / bagāža" → "Personīgās_mantas_bagāža"
    - "Atslēgu zādzība/atjaunošana" → "Atslēgu_zādzība_atjaunošana"
    - "Degvielas sajaukšana/tīrīšana" → "Degvielas_sajaukšana_tīrīšana"
    - "Riepas / diski" → "Riepas_diski"
    - "Nelaimes gad. vadīt./pasažieriem" → "Nelaimes_gad_vadīt_pasažieriem"
    - "Sadursme ar dzīvnieku" → "Sadursme_ar_dzīvnieku"
    - "Uguns / dabas stihijas" → "Uguns_dabas_stihijas"
    - "Remonts klienta servisā" → "Remonts_klienta_servisā"
    - "Remonts pie dīlera" → "Remonts_pie_dīlera"
    """
    key_mapping = {
        "Pašrisks – bojājumi": "Pašrisks_bojājumi",
        "Stiklojums bez pašriska": "Stiklojums_bez_pašriska",
        "Maiņas / nomas auto (dienas)": "Maiņas_nomas_auto_dienas",
        "Palīdzība uz ceļa": "Palīdzība_uz_ceļa",
        "Personīgās mantas / bagāža": "Personīgās_mantas_bagāža",
        "Atslēgu zādzība/atjaunošana": "Atslēgu_zādzība_atjaunošana",
        "Degvielas sajaukšana/tīrīšana": "Degvielas_sajaukšana_tīrīšana",
        "Riepas / diski": "Riepas_diski",
        "Nelaimes gad. vadīt./pasažieriem": "Nelaimes_gad_vadīt_pasažieriem",
        "Sadursme ar dzīvnieku": "Sadursme_ar_dzīvnieku",
        "Uguns / dabas stihijas": "Uguns_dabas_stihijas",
        "Remonts klienta servisā": "Remonts_klienta_servisā",
        "Remonts pie dīlera": "Remonts_pie_dīlera",
    }
    
    mapped = {}
    for json_key, value in raw_json.items():
        # Use mapping if exists, otherwise keep original key
        python_key = key_mapping.get(json_key, json_key)
        mapped[python_key] = value
    
    return mapped


def extract_casco_offers_from_text(
    pdf_text: str,
    insurer_name: str,
    pdf_filename: Optional[str] = None,
    model: str = "gpt-4o",
    max_retries: int = 2,
) -> List[CascoExtractionResult]:
    """
    CASCO extractor using OpenAI Chat Completions API with 22 fields.
    
    FIELDS:
    - 19 coverage fields (Bojājumi, Zādzība, Teritorija, etc.)
    - 3 financial fields (premium_total, insured_amount, period)
    
    All fields are strings ("v", "-", or descriptive values).
    Single offer per PDF (typical use case).
    
    Returns a single-element list for API compatibility.
    """
    client = _get_openai_client()

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(pdf_text=pdf_text, insurer_name=insurer_name, pdf_filename=pdf_filename)

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
            
            # Map JSON keys to Python-friendly names
            mapped_payload = _map_json_keys_to_python(payload)
            
            # Add metadata
            mapped_payload["insurer_name"] = insurer_name
            if pdf_filename:
                mapped_payload["pdf_filename"] = pdf_filename
            
            # Validate against Pydantic model
            try:
                coverage = CascoCoverage(**mapped_payload)
            except ValidationError as ve:
                raise ValueError(f"19-field validation failed: {ve}")
            
            # Generate raw_text summary (simple extraction-based summary)
            covered_fields = [
                key for key, val in mapped_payload.items() 
                if val and val not in ["-", "None", None] and key not in ["insurer_name", "pdf_filename"]
            ]
            raw_text = f"Extracted {len(covered_fields)} coverage fields for {insurer_name}"
            
            # Create result
            result = CascoExtractionResult(
                coverage=coverage,
                raw_text=raw_text,
            )
            
            # If we got here, extraction succeeded
            return [result]  # Single-element list for API compatibility

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
    
    # Should never reach here due to retry loop raising
    raise last_error or ValueError("Extraction failed for unknown reason")


