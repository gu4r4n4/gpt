# app/gpt_extractor.py
"""
Extractor with multi-variant support.

Flow:
1) Try Responses API with the main strict schema (programs[]).
2) If programs < 2, do a SECOND PASS (Responses API) with a tiny schema that
   ONLY enumerates the list of visible base plan variants in the PDF (name + premium + base sum).
3) If the second pass returns ≥2 variants, synthesize programs[] using those variants,
   inheriting features from the first program (so FE gets identical rows with different names/premiums).
4) Validate + normalize as before.
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

from jsonschema import Draft202012Validator
from openai import OpenAI

from app.normalizer import normalize_offer_json

# =========================
# STRICT JSON SCHEMA (main)
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
        "warnings": {"type": "array", "items": {"type": "string"}},
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
    },
}
_SCHEMA_VALIDATOR = Draft202012Validator(INSURER_OFFER_SCHEMA)

# =========================
# SECOND PASS schema (variants-only)
# =========================
VARIANTS_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "VariantList_v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["document_id", "variants"],
    "properties": {
        "document_id": {"type": "string", "minLength": 1},
        "variants": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["program_code"],
                "properties": {
                    "program_code": {"type": "string", "minLength": 1},
                    "base_sum_eur": {"oneOf": [{"type": "number"}, {"type": "string", "enum": ["-"]}]},
                    "premium_eur": {"oneOf": [{"type": "number"}, {"type": "string", "enum": ["-"]}]},
                },
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}
_VARIANTS_VALIDATOR = Draft202012Validator(VARIANTS_SCHEMA)

# =========================
# Prompt helpers
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

def _build_user_instructions(document_id: str) -> str:
    return f"""
DOCUMENT_ID: {document_id}

TASK:
Read the attached PDF (Latvian insurer offer) and return ONE JSON strictly matching the provided schema.
Top-level keys allowed ONLY: document_id, insurer_code (optional), programs, warnings.
Do NOT add any other keys.

IMPORTANT:
If the document contains MORE THAN ONE base program / variant (e.g. multiple rows in a summary table
or sections titled like "1. VARIANTS", "2. VARIANTS"), you MUST return one item in programs[] for EACH variant.

PROGRAM SHAPE (minimum per item):
- program_code  ← program name / code in the document (e.g., "DZINTARS PLUSS 2", "DZINTARS PLUSS 1", "V1 PLUSS (C20/1)")
- base_sum_eur  ← from/near "Apdrošinājuma summa", return number if possible, else "-"
- premium_eur   ← from "Prēmija", return number if possible, else "-"
- features      ← feature-name → {{ "value": <string|number> }}

FEATURES TO EXTRACT (LV labels exactly as below):
{chr(10).join(f"- {name}" for name in FEATURE_NAMES)}

STRICT RULES:
1) "Pakalpojuma apmaksas veids" MUST be exactly: "Saskaņā ar cenrādi".
2) Do NOT create separate papildprogrammas as programs; merge them into the same program via addon fields.
3) If a value is not clearly present, set "-".
4) "Pacientu iemaksa": if not stated, use "100%".
5) Titles/labels must keep Latvian diacritics; keep “MR” (not “MRG”).

OUTPUT:
Return STRICT JSON conforming to the schema. No markdown or prose.
""".strip()

def _build_variant_list_prompt(document_id: str) -> str:
    return f"""
DOCUMENT_ID: {document_id}

TASK:
Look ONLY for the list/table of base plans (variants). Extract EVERY variant you can see.
Return JSON that matches the 'VariantList_v1' schema: document_id + variants[].
For each variant return:
- program_code (plan name/title as printed, e.g. "DZINTARS PLUSS 2")
- premium_eur (number if possible, else "-")
- base_sum_eur (number if possible, else "-")

IMPORTANT:
- Include ALL variants if multiple are visible in the same document.
- Do not include papildprogrammas as separate variants.
- Do not invent names.

OUTPUT: STRICT JSON, no prose.
""".strip()

# =========================
# Helpers: numbers & pruning
# =========================
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
            try:
                return float(f"{whole}.{dec}" if dec else whole)
            except Exception:
                return "-"
        return "-"
    return "-"

def _wrap_feature_value(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict) and "value" in v:
        out = {"value": v["value"]}
        for k in ("confidence", "provenance"):
            if k in v:
                out[k] = v[k]
        return out
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
        q["program_code"] = str(p.get("program_code") or "").strip() or str(p.get("name") or "")
        if "program_type" in p and p["program_type"] in ("base", "additional"):
            q["program_type"] = p["program_type"]
        q["base_sum_eur"] = _to_number_or_dash(p.get("base_sum_eur"))
        q["premium_eur"]  = _to_number_or_dash(p.get("premium_eur"))

        feats_in = p.get("features") or {}
        q["features"] = {}
        if isinstance(feats_in, dict):
            for k, v in feats_in.items():
                q["features"][str(k)] = _wrap_feature_value(v)

        # fallback: derive name from "Programmas nosaukums" feature
        if not q["program_code"]:
            pn = q["features"].get("Programmas nosaukums", {}).get("value")
            if isinstance(pn, str) and pn.strip():
                q["program_code"] = pn.strip()

        if q.get("program_code") and "features" in q:
            norm_programs.append(q)

    # legacy fallback
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

def _ensure_min_features(prog: Dict[str, Any]) -> Dict[str, Any]:
    feats = prog.get("features") or {}
    if not isinstance(feats, dict):
        feats = {}
    name = prog.get("program_code") or "-"
    feats.setdefault("Programmas nosaukums", {"value": name})
    if prog.get("base_sum_eur", "-") != "-":
        feats.setdefault("Apdrošinājuma summa pamatpolisei, EUR", {"value": prog["base_sum_eur"]})
    prog["features"] = feats
    return prog

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
# Responses API helpers
# =========================
def _responses_json_with_pdf(model: str, prompt: str, schema: Dict[str, Any], document_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Call Responses API with an explicit JSON schema. Falls back to parsing raw JSON text.
    """
    client = _client_singleton()
    content = [
        {"type": "input_text", "text": prompt},
        {"type": "input_file", "filename": document_id or "document.pdf",
         "file_data": base64.b64encode(pdf_bytes).decode("ascii")},
    ]
    kwargs: Dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema.get("title", "schema"), "schema": schema, "strict": True},
        },
    }
    resp = client.responses.create(**kwargs)
    payload = getattr(resp, "output_parsed", None)
    if payload is not None:
        return payload

    # fallback: parse concatenated text
    pieces: List[str] = []
    for item in getattr(resp, "output", []) or []:
        t = getattr(item, "content", None)
        if isinstance(t, str):
            pieces.append(t)
    raw = "".join(pieces).strip() or "{}"
    return json.loads(raw)

def _responses_without_schema(model: str, prompt: str, document_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Same call, but without response_format — some SDK builds require this.
    """
    client = _client_singleton()
    content = [
        {"type": "input_text", "text": prompt},
        {"type": "input_file", "filename": document_id or "document.pdf",
         "file_data": base64.b64encode(pdf_bytes).decode("ascii")},
    ]
    resp = client.responses.create(model=model, input=[{"role": "user", "content": content}])

    pieces: List[str] = []
    for item in getattr(resp, "output", []) or []:
        t = getattr(item, "content", None)
        if isinstance(t, str):
            pieces.append(t)
    raw = "".join(pieces).strip() or "{}"
    return json.loads(raw)

# =========================
# Core passes
# =========================
def _first_pass(document_id: str, pdf_bytes: bytes, cfg: GPTConfig) -> Dict[str, Any]:
    last_err: Optional[Exception] = None

    # Responses API with schema
    for attempt in range(cfg.max_retries + 1):
        try:
            payload = _responses_json_with_pdf(cfg.model, _build_user_instructions(document_id), INSURER_OFFER_SCHEMA, document_id, pdf_bytes)
            pruned = _prune_payload(payload)
            _SCHEMA_VALIDATOR.validate(pruned)
            return pruned
        except TypeError as te:
            last_err = te
            break
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue

    # Responses API without schema
    for attempt in range(cfg.max_retries + 1):
        try:
            payload = _responses_without_schema(cfg.model, _build_user_instructions(document_id), document_id, pdf_bytes)
            pruned = _prune_payload(payload)
            _SCHEMA_VALIDATOR.validate(pruned)
            return pruned
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            break

    # Final fallback — chat with text is intentionally omitted here to keep latency acceptable
    raise ExtractionError(f"first pass failed: {last_err}")

def _second_pass_variants(document_id: str, pdf_bytes: bytes, cfg: GPTConfig) -> Optional[List[Dict[str, Any]]]:
    """
    Ask the model to enumerate visible variants. Returns list of dicts or None.
    """
    last_err: Optional[Exception] = None

    # With schema
    for attempt in range(cfg.max_retries + 1):
        try:
            payload = _responses_json_with_pdf(cfg.model, _build_variant_list_prompt(document_id), VARIANTS_SCHEMA, document_id, pdf_bytes)
            _VARIANTS_VALIDATOR.validate(payload)
            variants = payload.get("variants") or []
            if isinstance(variants, list) and variants:
                # normalize numbers/dashes
                out = []
                for v in variants:
                    out.append({
                        "program_code": str(v.get("program_code") or "").strip(),
                        "base_sum_eur": _to_number_or_dash(v.get("base_sum_eur")),
                        "premium_eur":  _to_number_or_dash(v.get("premium_eur")),
                    })
                return [x for x in out if x["program_code"]]
            return None
        except TypeError as te:
            last_err = te
            break
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue

    # Without schema
    for attempt in range(cfg.max_retries + 1):
        try:
            payload = _responses_without_schema(cfg.model, _build_variant_list_prompt(document_id), document_id, pdf_bytes)
            # be forgiving here
            try:
                data = dict(payload)
            except Exception:
                # if payload is a stringified JSON, parse again
                data = json.loads(str(payload))
            variants = data.get("variants") or []
            out = []
            for v in variants:
                out.append({
                    "program_code": str(v.get("program_code") or "").strip(),
                    "base_sum_eur": _to_number_or_dash(v.get("base_sum_eur")),
                    "premium_eur":  _to_number_or_dash(v.get("premium_eur")),
                })
            return [x for x in out if x["program_code"]]
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            break

    # give up
    return None

def _synthesize_from_variants(first_pass: Dict[str, Any], variants: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build programs[] from variant rows but keep features from the first program
    (so all columns get the same coverage details but different names/premiums).
    """
    base_prog = (first_pass.get("programs") or [{}])[0] if first_pass.get("programs") else {}
    base_features = dict(base_prog.get("features") or {})

    programs: List[Dict[str, Any]] = []
    for v in variants:
        prog = {
            "program_code": v.get("program_code") or base_prog.get("program_code") or "Pamatprogramma",
            "base_sum_eur": v.get("base_sum_eur", base_prog.get("base_sum_eur", "-")),
            "premium_eur":  v.get("premium_eur",  base_prog.get("premium_eur", "-")),
            "features": dict(base_features),
        }
        prog = _ensure_min_features(prog)
        programs.append(prog)

    out = dict(first_pass)
    out["programs"] = programs
    ws = list(out.get("warnings") or [])
    ws.append("second-pass: multi-variant table detected; programs synthesized from variant list")
    out["warnings"] = ws
    return out

# =========================
# Public API
# =========================
def extract_offer_from_pdf_bytes(pdf_bytes: bytes, document_id: str) -> Dict[str, Any]:
    if not pdf_bytes or len(pdf_bytes) > 10 * 1024 * 1024:
        raise ExtractionError("PDF too large or empty (limit: 10MB)")

    cfg = GPTConfig()

    # 1) First pass (full programs)
    first = _first_pass(document_id, pdf_bytes, cfg)

    # 2) If single program, try second pass to enumerate variants
    if len(first.get("programs") or []) < 2:
        variants = _second_pass_variants(document_id, pdf_bytes, cfg)
        if variants and len(variants) >= 2:
            first = _synthesize_from_variants(first, variants)

    # 3) Validate again and normalize
    _SCHEMA_VALIDATOR.validate(first)
    normalized = normalize_offer_json({**first, "document_id": document_id})
    return normalized
