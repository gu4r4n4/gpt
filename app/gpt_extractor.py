# app/gpt_extractor.py
"""
Robust extractor that:
- Uses Responses API with PDF as input_file (base64) when available.
- If the SDK doesn't support response_format, retries without it.
- If Responses path fails, falls back to Chat Completions with PDF text.
- Always prunes unknown keys, validates against a strict schema,
  and THEN runs the normalizer (hard rules + PP folding).
"""

from __future__ import annotations
import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from jsonschema import Draft202012Validator, ValidationError
from pypdf import PdfReader
from openai import OpenAI

from app.normalizer import normalize_offer_json  # <-- ensure normalizer is applied

# =========================
# STRICT JSON SCHEMA
# =========================
INSURER_OFFER_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "InsurerOfferExtraction_v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["document_id", "programs"],
    "properties": {
        "document_id": {"type": "string", "minLength": 1},
        "insurer_code": {"type": "string"},
        "programs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["program_code", "base_sum_eur", "premium_eur", "features"],
                "properties": {
                    "program_code": {"type": "string", "minLength": 1},
                    "program_type": {"type": "string", "enum": ["base", "additional"]},
                    "base_sum_eur": {"oneOf": [{"type": "number"}, {"type": "string", "enum": ["-"]}]},
                    "premium_eur": {"oneOf": [{"type": "number"}, {"type": "string", "enum": ["-"]}]},
                    "features": {
                        "type": "object",
                        "minProperties": 1,
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["value"],
                            "properties": {
                                "value": {
                                    "oneOf": [
                                        {"type": "number"},
                                        {"type": "string", "maxLength": 160}
                                    ]
                                },
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "provenance": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "page": {"type": "integer", "minimum": 1},
                                        "source_text": {"type": "string"},
                                        "table_hint": {
                                            "type": "string",
                                            "enum": ["PAMATPROGRAMMA", "PAPILDPROGRAMMAS", "auto"],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}
_SCHEMA_VALIDATOR = Draft202012Validator(INSURER_OFFER_SCHEMA)

# =========================
# Features list (prompt helper)
# =========================
FEATURE_NAMES: List[str] = [
    "Programmas nosaukums",
    "Pakalpojuma apmaksas veids",
    "Programmas kods",
    "Apdrošinājuma summa pamatpolisei, EUR",
    "Pacientu iemaksa",
    "Maksas ģimenes ārsta mājas vizītes, limits EUR",
    "Maksas ārsta-specialista konsultācija, limits EUR",
    "Profesora, docenta, internista konsultācija, limits EUR",
    "Homeopāts",
    "Psihoterapeits",
    "Sporta ārsts",
    "ONLINE ārstu konsultācijas",
    "Laboratoriskie izmeklējumi",
    "Maksas diagnostika, piem., rentgens, elektrokradiogramma, USG, utml.",
    "Augsto tehnoloģiju izmeklējumi, piem., MR, CT, limits (reižu skaits vai EUR)",
    "Obligātās veselības pārbaudes, limits EUR",
    "Ārstnieciskās manipulācijas",
    "Medicīniskās izziņas",
    "Fizikālā terapija",
    "Procedūras",
    "Vakcinācija, limits EUR",
    "Maksas grūtnieču aprūpe",
    "Maksas onkoloģiskā, hematoloģiskā ārstēšana",
    "Neatliekamā palīdzība valsts un privātā (limits privātai, EUR)",
    "Maksas stacionārie pakalpojumi, limits EUR",
    "Maksas stacionārā rehabilitācija, limits EUR",
    "Ambulatorā rehabilitācija",
    "Pamatpolises prēmija 1 darbiniekam, EUR",
    "Piemaksa par plastikāta kartēm, EUR",
    "Zobārstniecība ar 50% atlaidi (pamatpolise)",
    "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)",
    "Vakcinācija pret ērcēm un gripu",
    "Ambulatorā rehabilitācija (pp)",
    "Medikamenti ar 50% atlaidi",
    "Sports",
    "Kritiskās saslimšanas",
    "Maksas stacionārie pakalpojumi, limits EUR (pp)",
]

# =========================
# Prompts
# =========================
def _build_user_instructions(document_id: str) -> str:
    return f"""
DOCUMENT_ID: {document_id}

TASK:
Read the attached PDF (Latvian insurer offer) and return ONE JSON strictly matching the provided schema.
Top-level keys allowed ONLY: document_id, insurer_code (optional), programs, warnings.
DO NOT add any other keys like base_program, additional_programs, persons_count, etc.

PROGRAM SHAPE (minimum):
- program_code  ← program name / code in the document (e.g., "Pamatprogramma V2+")
- base_sum_eur  ← from/near "Apdrošinājuma summa vienai personai"; if missing, put "-"
- premium_eur   ← from "Prēmija vienai personai, EUR" in table "PAMATPROGRAMMA"; if missing, put "-"
- features      ← object of feature-name → {{ "value": <string|number> }}

FEATURES TO EXTRACT (LV labels exactly as below):
{chr(10).join(f"- {name}" for name in FEATURE_NAMES)}

STRICT RULES (override inference):
1) "Pakalpojuma apmaksas veids" MUST be exactly: "Saskaņā ar cenrādi". Do not infer other text.
2) "Maksas grūtnieču aprūpe":
   - Search for "Grūtnieču aprūpe" or "grūtniecības aprūpe".
   - If mentioned at all → return "v", else return "-".
3) "Vakcinācija pret ērcēm un gripu":
   - Search for the keyword "ērču" (e.g., "ērču encefalīta vakcīna").
   - If a limit is stated, return a textual limit like "limits 70 EUR"; if only inclusion is stated, return "v"; otherwise return "-".
   - Use the exact label spelling above ("ērcēm", not "ērčiem").
4) Do NOT create separate Papildprogramma objects. Merge any additional coverage into the base program using these fields:
   - "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)"
   - "Ambulatorā rehabilitācija (pp)"
   - "Medikamenti ar 50% atlaidi"
   - "Sports"
   - "Kritiskās saslimšanas"
   - "Maksas stacionārie pakalpojumi, limits EUR (pp)"
5) If a value is not clearly present in the PDF, set "-".
6) Do not invent "Programmas kods"; if none, use "-".
7) "Pacientu iemaksa": use "100%" if the document doesn't explicitly state a different value.

FEATURE-SPECIFIC FIND LOGIC (apply these when filling feature values):
1) "Maksas diagnostika, piem., rentgens, elektrokradiogramma, USG, utml.": search for the keyword "diagnostika". If included anywhere in covered services → return "v"; otherwise "-".
2) "Obligātās veselības pārbaudes, limits EUR": search for "Obligātās veselības pārbaudes" or "OVP". If included → return "100%"; otherwise "-".
3) "Procedūras": search for "Procedūras". If a quantitative/percent limit is present, return it verbatim (e.g., "10 reizes", "80%"). If included without a limit → "v". If absent → "-".
4) "Vakcinācija, limits EUR": search for "Vakcinācija". If a numeric limit is present, return as "limits <NUMBER> EUR" (e.g., "limits 75 EUR"). If only inclusion is stated → "v". If absent → "-".
5) "Vakcinācija pret ērcēm un gripu": per rule (3), return "limits <NUMBER> EUR" when stated; "v" if only inclusion; "-" if absent.
6) "Medikamenti ar 50% atlaidi": search for "Medikamenti"/"Medikamentu". If a numeric limit is present, return "<NUMBER> EUR limits"; else "v" if included; else "-".
7) "Sports": search for "Sports"/"Sporta". If a numeric limit is present, return "<NUMBER> EUR limits"; else "v" if included; else "-".
8) "Kritiskās saslimšanas": if an amount is stated return "<NUMBER> EUR limits"; else "v" if included; else "-".
9) "Maksas stacionārie pakalpojumi, limits EUR (pp)": if ANY of these keywords appears anywhere in the PDF — "Maksas stacionārie pakalpojumi", "MAKSAS STACIONĀRS", "Maksas pakalpojumi stacionārā", "Maksas stacionāra pakalpojumi" — return "ir iekļauts"; otherwise "-".

OUTPUT:
Return STRICT JSON conforming to the schema. No markdown or prose.
""".strip()

# =========================
# Utils: PDF → text (for fallback), normalization & pruning
# =========================
def _pdf_to_text_pages(pdf_bytes: bytes, max_pages: int = 30) -> List[str]:
    pages: List[str] = []
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for page in reader.pages[:max_pages]:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        pages.append(txt.replace("\u00A0", " ").replace("\r", "\n")[:20000])
    return pages

_MONEY_RE = re.compile(r"^\s*([0-9]{1,3}(?:[ .][0-9]{3})*|[0-9]+)(?:[.,]([0-9]{1,2}))?\s*(?:eur|€)?\s*$", re.IGNORECASE)

def _to_number_or_dash(v: Any) -> Any:
    if v is None:
        return "-"
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "-" or s == "":
            return "-"
        m = _MONEY_RE.match(s.replace("\u00A0", " "))
        if m:
            whole, dec = m.groups()
            whole = whole.replace(" ", "").replace(".", "")
            num = float(f"{whole}.{dec}" if dec else whole)
            return num
        return "-"
    return "-"

def _wrap_feature_value(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict) and "value" in v:
        return {"value": v["value"]} | ({k: v[k] for k in ("confidence","provenance") if k in v})
    return {"value": v}

def _prune_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    allowed_root = {"document_id", "insurer_code", "programs", "warnings"}
    out: Dict[str, Any] = {k: payload[k] for k in list(payload.keys()) if k in allowed_root}

    if not isinstance(out.get("document_id"), str) or not out["document_id"].strip():
        out["document_id"] = str(payload.get("document_id") or "uploaded.pdf")

    if "warnings" in out and not isinstance(out["warnings"], list):
        out["warnings"] = [str(out["warnings"])]

    programs = payload.get("programs") or []
    norm_programs: List[Dict[str, Any]] = []
    for p in programs:
        if not isinstance(p, dict):
            continue
        q: Dict[str, Any] = {}
        q["program_code"] = str(p.get("program_code") or "").strip() or str((p.get("name") or "")) or ""
        if "program_type" in p and p["program_type"] in ("base", "additional"):
            q["program_type"] = p["program_type"]

        q["base_sum_eur"] = _to_number_or_dash(p.get("base_sum_eur"))
        q["premium_eur"]  = _to_number_or_dash(p.get("premium_eur"))

        features_in = p.get("features") or {}
        if isinstance(features_in, dict):
            feat_out: Dict[str, Any] = {}
            for k, v in features_in.items():
                feat_out[str(k)] = _wrap_feature_value(v)
            q["features"] = feat_out
        else:
            q["features"] = {}

        if not q["program_code"]:
            pn = q["features"].get("Programmas nosaukums", {}).get("value")
            if isinstance(pn, str) and pn.strip():
                q["program_code"] = pn.strip()

        if q.get("program_code") and "features" in q:
            norm_programs.append(q)

    if not norm_programs:
        bp = payload.get("base_program")
        if isinstance(bp, dict):
            q = {
                "program_code": str(bp.get("name") or "Pamatprogramma").strip(),
                "base_sum_eur": _to_number_or_dash(bp.get("base_sum_eur")),
                "premium_eur":  _to_number_or_dash(bp.get("premium_eur")),
                "features": {},
            }
            feats = bp.get("features") or {}
            if isinstance(feats, dict):
                q["features"] = {str(k): _wrap_feature_value(v) for k, v in feats.items()}
            norm_programs.append(q)

    out["programs"] = norm_programs or []
    return out

# =========================
# OpenAI client setup
# =========================
@dataclass
class GPTConfig:
    model: str = os.getenv("GPT_MODEL", "gpt-5")
    max_retries: int = int(os.getenv("GPT_MAX_RETRIES", "2"))
    log_prompts: bool = os.getenv("LOG_PROMPTS", "false").lower() == "true"
    fallback_chat_model: str = os.getenv("FALLBACK_CHAT_MODEL", "gpt-4o-mini")

_client: Optional[OpenAI] = None
def _client_singleton() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client

class ExtractionError(Exception):
    pass

# =========================
# Core: Responses API path
# =========================
def _responses_with_pdf(model: str, document_id: str, pdf_bytes: bytes, allow_schema: bool) -> Dict[str, Any]:
    client = _client_singleton()
    content = [
        {"type": "input_text", "text": _build_user_instructions(document_id)},
        {"type": "input_file", "filename": document_id or "document.pdf",
         "file_data": base64.b64encode(pdf_bytes).decode("ascii")},
    ]

    kwargs: Dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
    }
    if allow_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "InsurerOfferExtraction_v1", "schema": INSURER_OFFER_SCHEMA, "strict": True},
        }

    resp = client.responses.create(**kwargs)

    payload = getattr(resp, "output_parsed", None)
    if payload is not None:
        return payload

    texts: List[str] = []
    for item in getattr(resp, "output", []) or []:
        t = getattr(item, "content", None)
        if isinstance(t, str):
            texts.append(t)
    raw = "".join(texts).strip() or "{}"
    return json.loads(raw)

# =========================
# Fallback: Chat Completions with extracted text
# =========================
def _chat_with_text(model: str, document_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
    client = _client_singleton()
    pages = _pdf_to_text_pages(pdf_bytes)
    user = (
        _build_user_instructions(document_id)
        + "\n\nPDF TEXT (per page):\n"
        + "\n\n".join(f"===== Page {i+1} =====\n{p}" for i, p in enumerate(pages))
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return STRICT JSON only. No markdown, no prose."},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        txt = resp.choices[0].message.content or "{}"
        return json.loads(txt)
    except TypeError:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return ONLY raw JSON that matches the required schema. No extra keys."},
                {"role": "user", "content": user},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        try:
            return json.loads(raw)
        except Exception:
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(raw[start:end + 1])
            raise

# =========================
# Public orchestration
# =========================
def call_gpt_extractor(document_id: str, pdf_bytes: bytes, cfg: Optional[GPTConfig] = None) -> Dict[str, Any]:
    cfg = cfg or GPTConfig()
    last_err: Optional[Exception] = None

    # 1) Try Responses API with schema
    for attempt in range(cfg.max_retries + 1):
        try:
            payload = _responses_with_pdf(cfg.model, document_id, pdf_bytes, allow_schema=True)
            pruned = _prune_payload(payload)
            _SCHEMA_VALIDATOR.validate(pruned)
            return pruned
        except TypeError as te:
            last_err = te
            break
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.7 * (attempt + 1))
                continue

    # 2) Responses API without schema
    for attempt in range(cfg.max_retries + 1):
        try:
            payload = _responses_with_pdf(cfg.model, document_id, pdf_bytes, allow_schema=False)
            pruned = _prune_payload(payload)
            _SCHEMA_VALIDATOR.validate(pruned)
            return pruned
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.7 * (attempt + 1))
                continue
            break

    # 3) Chat fallback
    for attempt in range(cfg.max_retries + 1):
        try:
            try_models = [cfg.model, cfg.fallback_chat_model] if cfg.fallback_chat_model != cfg.model else [cfg.model]
            for m in try_models:
                try:
                    payload = _chat_with_text(m, document_id, pdf_bytes)
                    pruned = _prune_payload(payload)
                    _SCHEMA_VALIDATOR.validate(pruned)
                    return pruned
                except Exception as inner:
                    last_err = inner
                    continue
            raise last_err or RuntimeError("Chat path failed")
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.7 * (attempt + 1))
                continue
            break

    raise ExtractionError(f"GPT extraction failed: {last_err}")

def extract_offer_from_pdf_bytes(pdf_bytes: bytes, document_id: str) -> Dict[str, Any]:
    if not pdf_bytes or len(pdf_bytes) > 10 * 1024 * 1024:
        raise ExtractionError("PDF too large or empty (limit: 10MB)")
    # 1) Get raw (schema-pruned) payload from GPT
    raw = call_gpt_extractor(document_id=document_id, pdf_bytes=pdf_bytes)
    # 2) Normalize (hard rules + PP folding + corrected labels)
    normalized = normalize_offer_json({
        **raw,
        "document_id": document_id,  # ensure filename propagates
    })
    return normalized
