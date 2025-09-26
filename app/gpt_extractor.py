# app/gpt_extractor.py
"""
Robust extractor that:
- Uses Responses API with PDF as input_file (base64) when available.
- If the SDK doesn't support response_format, retries without it.
- If Responses path fails, falls back to Chat Completions with PDF text.
- Always prunes unknown keys, validates against a strict schema, then runs the normalizer.

Safe post-process:
- If the PDF clearly contains multiple *base* variants in PAMATPROGRAMMA, synthesize one programs[] per variant.
- We explicitly stop before any PAPILD... section so premiums are never taken from add-ons.
- Normalizer safety-belt: if it collapses multiple programs to a single program, restore synthesized programs
  (can be disabled via env KEEP_SYNTH_MULTI=0).
"""

from __future__ import annotations
import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator
from openai import OpenAI
from pypdf import PdfReader

from app.normalizer import normalize_offer_json

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
                                        {"type": "string", "maxLength": 160},
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
# Prompt
# =========================
def _build_user_instructions(document_id: str) -> str:
    return f"""
DOCUMENT_ID: {document_id}

TASK:
Read the attached PDF (Latvian insurer offer) and return ONE JSON strictly matching the provided schema.
Top-level keys allowed ONLY: document_id, insurer_code (optional), programs, warnings.
DO NOT add any other keys like base_program, additional_programs, persons_count, etc.

IMPORTANT:
If the document contains MORE THAN ONE base program / variant (e.g. multiple rows in one summary table,
or sections titled "1. VARIANTS", "2. VARIANTS", etc.), you MUST return one item in programs[] for EACH variant.

PROGRAM SHAPE (minimum per program item):
- program_code  ← program name / code in the document (e.g., "Pamatprogramma V2+", "DZINTARS Pluss 2", "V1 PLUSS (C20/1)")
- base_sum_eur  ← from/near "Apdrošinājuma summa vienai personai"; if missing, put "-"
- premium_eur   ← from "Prēmija vienai personai, EUR" (or "Prēmija"), numeric if possible; if missing, put "-"
- features      ← object of feature-name → {{ "value": <string|number> }}

FEATURES TO EXTRACT (LV labels exactly as below):
{chr(10).join(f"- {name}" for name in FEATURE_NAMES)}

STRICT RULES (override inference):
1) "Pakalpojuma apmaksas veids" MUST be exactly: "Saskaņā ar cenrādi".
2) "Maksas grūtnieču aprūpe": if mentioned at all → "v", else "-".
3) "Vakcinācija pret ērcēm un gripu": look for "ērču". If a limit is stated, return like "limits 70 EUR"; if only included, "v"; otherwise "-".
4) Do NOT create separate Papildprogramma items. Merge additions into the base program via fields:
   - "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)"
   - "Ambulatorā rehabilitācija (pp)"
   - "Medikamenti ar 50% atlaidi"
   - "Sports"
   - "Kritiskās saslimšanas"
   - "Maksas stacionārie pakalpojumi, limits EUR (pp)"
5) If a value is not clearly present, set "-".
6) Do not invent "Programmas kods"; if none, use "-".
7) "Pacientu iemaksa": use "100%" unless explicitly stated otherwise.

OUTPUT:
Return STRICT JSON conforming to the schema. No markdown or prose.
""".strip()

# =========================
# PDF utils & normalization helpers
# =========================
def _pdf_to_text_pages(pdf_bytes: bytes, max_pages: int = 100) -> List[str]:
    pages: List[str] = []
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for page in reader.pages[:max_pages]:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = txt.replace("\u00A0", " ").replace("\u00AD", "").replace("\r", "\n")
        pages.append(txt[:20000])
    return pages

_MONEY_RE = re.compile(
    r"^\s*([0-9]{1,3}(?:[ .][0-9]{3})*|[0-9]+)(?:[.,]([0-9]{1,2}))?\s*(?:eur|€)?\s*$",
    re.IGNORECASE,
)

def _parse_money_like(s: str) -> Optional[float]:
    s = (s or "").strip().replace("\u00A0", " ")
    m = _MONEY_RE.match(s)
    if not m:
        return None
    whole, dec = m.groups()
    whole = (whole or "").replace(" ", "").replace(".", "")
    try:
        return float(f"{whole}.{dec}" if dec else whole)
    except Exception:
        return None

def _to_number_or_dash(v: Any) -> Any:
    if v is None:
        return "-"
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s in {"-", ""}:
            return "-"
        val = _parse_money_like(s)
        return val if val is not None else "-"
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

# =========================
# Heuristics: multi-variant detection (BASE ONLY)
# =========================
_PAMAT_MARK = re.compile(r"\bPAMATPROGRAMMA\b|\bPamatprogramma\b", re.IGNORECASE)
_PAPILD_MARK = re.compile(r"\bPAPILDPROGRAMMAS?\b|\bPapildprogrammas?\b|\bPapildprogramma\b", re.IGNORECASE)

_BASE_HEADER_HINTS = (
    "Programmas nosaukums",
    "Apdrošinājuma summa",
    "Prēmija",
    "vienai personai",
)

# Strict single-line row pattern with OPTIONAL small "count" column
_BASE_ROW_RE = re.compile(
    r"^\s*(?P<name>[A-Za-zĀČĒĢĪĶĻŅŌŖŠŪŽāčēģīķļņōŗšūž0-9+/\-()., ]{3,}?)\s+"
    r"(?:(?P<count>\d{1,4})\s+)?"  # optional 'insured count' column
    r"(?P<sum>(?:[0-9]{1,3}(?:[ .][0-9]{3})*|[0-9]+)(?:[.,][0-9]{1,2})?)\s*(?:€|EUR)?\s+"
    r"(?P<premium>(?:[0-9]{1,3}(?:[ .][0-9]{3})*|[0-9]+)(?:[.,][0-9]{1,2})?)\s*(?:€|EUR)?\s*$",
    re.MULTILINE,
)

def _pdf_pages_text(pdf_bytes: bytes) -> Tuple[str, List[str]]:
    pages = _pdf_to_text_pages(pdf_bytes)
    full = "\n".join(pages)
    return full, pages

def _looks_like_base_header(block: str) -> bool:
    hit = 0
    for h in _BASE_HEADER_HINTS:
        if h.lower() in block.lower():
            hit += 1
    return hit >= 2

def _extract_pamat_block(full_text: str) -> Optional[str]:
    m_start = _PAMAT_MARK.search(full_text)
    if not m_start:
        return None
    start = m_start.start()
    m_end = _PAPILD_MARK.search(full_text, m_start.end())
    end = m_end.start() if m_end else len(full_text)
    return full_text[start:end]

def _parse_base_rows_strict(block: str) -> List[Dict[str, Any]]:
    if not block or not _looks_like_base_header(block):
        return []
    rows: List[Dict[str, Any]] = []
    for m in _BASE_ROW_RE.finditer(block):
        name = (m.group("name") or "").strip(" -\u200b")
        base_sum = _parse_money_like(m.group("sum") or "")
        premium = _parse_money_like(m.group("premium") or "")
        if not name or base_sum is None or premium is None:
            continue
        if any(k in name for k in ("Programmas", "Apdrošinājuma", "Prēmija")):
            continue
        rows.append({"name": name, "base_sum": base_sum, "premium": premium})
    return rows

_MONEY_ANYWHERE_RE = re.compile(
    r"([0-9]{1,3}(?:[ .][0-9]{3})*|[0-9]+)(?:[.,]([0-9]{1,2}))?(?:\s*(?:€|EUR))?",
    re.IGNORECASE,
)

def _parse_base_rows_loose(block: str) -> List[Dict[str, Any]]:
    if not block:
        return []
    lines = [l.rstrip() for l in block.splitlines() if l.strip()]
    rows: List[Dict[str, Any]] = []
    acc_name: List[str] = []
    acc_nums: List[float] = []

    def flush():
        nonlocal acc_name, acc_nums, rows
        if not acc_name:
            return
        nums = [n for n in acc_nums if isinstance(n, (int, float))]
        moneyish = [n for n in nums if n >= 150]  # ignore tiny "count" numbers
        if len(moneyish) >= 2:
            s, p = moneyish[-2], moneyish[-1]
            base_sum, premium = (max(s, p), min(s, p))
            if base_sum >= 500 and premium <= 700:
                name = re.sub(r"\s{2,}", " ", " ".join(acc_name)).strip(" -\u200b")
                if name and not any(h in name for h in ("Programmas", "Apdrošinājuma", "Prēmija")):
                    rows.append({"name": name, "base_sum": float(base_sum), "premium": float(premium)})
        acc_name, acc_nums = [], []

    for ln in lines:
        if _looks_like_base_header(ln):
            flush()
            continue
        acc_name.append(ln)
        for m in _MONEY_ANYWHERE_RE.finditer(ln):
            val = _parse_money_like(m.group(0))
            if val is not None:
                acc_nums.append(val)
        if re.search(r"\d(?:[.,]\d{2})?\s*(?:€|EUR)?\s*$", ln, re.IGNORECASE):
            flush()

    flush()
    return rows if len(rows) >= 2 else []

def _detect_base_programs_from_text(full_text: str) -> List[Dict[str, Any]]:
    block = _extract_pamat_block(full_text)
    if not block:
        return []
    strict = _parse_base_rows_strict(block)
    if len(strict) >= 2:
        return strict
    return _parse_base_rows_loose(block)

def _ensure_features_minimal(prog: Dict[str, Any]) -> Dict[str, Any]:
    feats = prog.get("features") or {}
    if not isinstance(feats, dict) or not feats:
        feats = {}
    name = prog.get("program_code") or "-"
    feats.setdefault("Programmas nosaukums", {"value": name})
    if prog.get("base_sum_eur", "-") != "-":
        feats.setdefault("Apdrošinājuma summa pamatpolisei, EUR", {"value": prog["base_sum_eur"]})
    prog["features"] = feats
    return prog

def _augment_with_detected_variants(pruned_payload: Dict[str, Any], pdf_bytes: bytes) -> Dict[str, Any]:
    programs = pruned_payload.get("programs") or []
    full_text, _ = _pdf_pages_text(pdf_bytes)
    detected = _detect_base_programs_from_text(full_text)

    ws = list(pruned_payload.get("warnings") or [])
    if detected:
        names_preview = ", ".join([d["name"] for d in detected[:4]])
        ws.append(f"postprocess: detected {len(detected)} base rows in PAMATPROGRAMMA: {names_preview}")

    if len(programs) >= 2 or len(detected) < 2:
        pruned_payload["warnings"] = ws
        return pruned_payload

    base_prog = programs[0] if programs else {
        "program_code": "Pamatprogramma",
        "base_sum_eur": "-",
        "premium_eur": "-",
        "features": {},
    }
    base_features = base_prog.get("features") or {}

    synthesized: List[Dict[str, Any]] = []
    for d in detected:
        prog = {
            "program_code": d["name"],
            "base_sum_eur": d["base_sum"] if d["base_sum"] is not None else "-",
            "premium_eur": d["premium"] if d["premium"] is not None else "-",
            "features": dict(base_features),
        }
        prog = _ensure_features_minimal(prog)
        synthesized.append(prog)

    out = dict(pruned_payload)
    out["programs"] = synthesized
    ws.append(f"postprocess: synthesized {len(synthesized)} programs from PAMATPROGRAMMA (base table only)")
    out["warnings"] = ws
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

# =========================
# Core: Responses API path
# =========================
def _responses_with_pdf(model: str, document_id: str, pdf_bytes: bytes, allow_schema: bool) -> Dict[str, Any]:
    client = _client_singleton()
    content = [
        {"type": "input_text", "text": _build_user_instructions(document_id)},
        {
            "type": "input_file",
            "filename": document_id or "document.pdf",
            "file_data": base64.b64encode(pdf_bytes).decode("ascii"),
        },
    ]

    kwargs: Dict[str, Any] = {"model": model, "input": [{"role": "user", "content": content}]}
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
                return json.loads(raw[start : end + 1])
            raise

# =========================
# Normalizer safety-belt + orchestration
# =========================
class ExtractionError(Exception):
    pass

def _normalize_safely(augmented: Dict[str, Any], document_id: str) -> Dict[str, Any]:
    """Run normalizer; if it collapses synthesized multi-variant programs, restore them (unless KEEP_SYNTH_MULTI=0)."""
    try:
        normalized = normalize_offer_json({**augmented, "document_id": document_id})
    except Exception as e:
        augmented.setdefault("warnings", []).append(f"normalize_error: {e}; returning augmented")
        return augmented

    keep_multi = os.getenv("KEEP_SYNTH_MULTI", "1") != "0"
    pre = len(augmented.get("programs") or [])
    post = len(normalized.get("programs") or [])
    if keep_multi and pre >= 2 and post < 2:
        normalized["warnings"] = (normalized.get("warnings") or []) + [
            f"postprocess: restored {pre} synthesized programs (normalizer had collapsed to {post})."
        ]
        normalized["programs"] = augmented["programs"]
    return normalized

def call_gpt_extractor(document_id: str, pdf_bytes: bytes, cfg: Optional[GPTConfig] = None) -> Dict[str, Any]:
    cfg = cfg or GPTConfig()
    last_err: Optional[Exception] = None

    # 1) Responses + schema
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

    # 2) Responses without schema
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
    if not pdf_bytes or len(pdf_bytes) > 12 * 1024 * 1024:
        raise ExtractionError("PDF too large or empty (limit: 12MB)")
    raw = call_gpt_extractor(document_id=document_id, pdf_bytes=pdf_bytes)
    augmented = _augment_with_detected_variants(raw, pdf_bytes)
    normalized = _normalize_safely(augmented, document_id=document_id)
    return normalized
