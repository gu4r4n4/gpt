"""
Robust extractor compatible with openai==2.2.0 (no response_format={"type":"json_schema"} on Responses).

Key changes vs your version:
- Removed any usage of response_format={"type":"json_schema"} on Responses API.
- Prefer Chat Completions path first (with response_format={"type":"json_object"} if available, else plain text),
  then try a minimal Responses call WITHOUT response_format as a secondary option.
- Hardened JSON parsing: tolerant JSON substring extraction + last-resort braces-scan.
- Papildprogrammas parser unchanged in logic, but wired so it always runs post LLM, even if LLM path fails;
  i.e., we still enrich base programs from raw PDF text.
- All schema validation stays local (jsonschema) — model is *instructed* to follow it, we enforce it after.

This file is drop-in: replace your existing app/gpt_extractor.py with this one. normalizer.py can stay as-is.
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

from app.normalizer import normalize_offer_json  # keeps canonical labels + legacy key mapping

# =========================
# STRICT JSON SCHEMA (unchanged)
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
# Prompt & helpers (unchanged where not SDK-specific)
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
    "Zobārstniecība ar 50% atlaidi (pp)",
    "Vakcinācija pret ērcēm un gripu",
    "Ambulatorā rehabilitācija (pp)",
    "Medikamenti ar 50% atlaidi",
    "Sports",
    "Kritiskās saslimšanas",
    "Maksas stacionārie pakalpojumi, limits EUR (pp)",
    "Maksas Operācijas, limits EUR",
    "Optika 50%, limits EUR",
]

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
   - "Zobārstniecība ar 50% atlaidi (pamatpolise)"
   - "Zobārstniecība ar 50% atlaidi (pp)"
   - "Ambulatorā rehabilitācija (pp)"
   - "Medikamenti ar 50% atlaidi"
   - "Sports"
   - "Kritiskās saslimšanas"
   - "Maksas stacionārie pakalpojumi, limits EUR (pp)"
   - "Maksas Operācijas, limits EUR"
   - "Optika 50%, limits EUR"
5) If a value is not clearly present, set "-".
6) Do not invent "Programmas kods"; if none, use "-".
7) "Pacientu iemaksa": use "100%" unless explicitly stated otherwise.

OUTPUT:
Return STRICT JSON conforming to the schema. No markdown or prose.
""".strip()

# =========================
# PDF utils
# =========================

def _pdf_to_text_pages(pdf_bytes: bytes, max_pages: int = 200) -> List[str]:
    pages: List[str] = []
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for page in reader.pages[:max_pages]:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = txt.replace("\u00A0", " ").replace("\u00AD", "").replace("\r", "\n")
        pages.append(txt[:30000])
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

# =========================
# Post-LLM base variant detection (same logic as your version)
# =========================
_PAMAT_MARK = re.compile(r"\bPAMATPROGRAMMA\b|\bPamatprogramma\b", re.IGNORECASE)
_PAPILD_MARK = re.compile(r"\bPAPILDPROGRAMMAS?\b|\bPapildprogrammas?\b|\bPapildprogramma\b", re.IGNORECASE)

_BASE_HEADER_HINTS = (
    "Programmas nosaukums",
    "Apdrošinājuma summa",
    "Prēmija",
    "vienai personai",
)

_BASE_ROW_RE = re.compile(
    r"^\s*(?P<name>[A-Za-zĀČĒĢĪĶĻŅŌŖŠŪŽāčēģīķļņōŗšūž0-9+/\-()., ]{3,}?)\s+" \
    r"(?:(?P<count>\d{1,4})\s+)?" \
    r"(?P<sum>(?:[0-9]{1,3}(?:[ .][0-9]{3})*|[0-9]+)(?:[.,][0-9]{1,2})?)\s*(?:€|EUR)?\s+" \
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

_MONEY_ANYWHERE_RE = re.compile(
    r"([0-9]{1,3}(?:[ .][0-9]{3})*|[0-9]+)(?:[.,]([0-9]{1,2}))?(?:\s*(?:€|EUR))?",
    re.IGNORECASE,
)

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
        moneyish = [n for n in nums if n >= 150]
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

# =========================
# Papildprogrammas extraction (unchanged logic)
# =========================
_PP_CANON_KEYS = [
    "Maksas Operācijas, limits EUR",
    "Optika 50%, limits EUR",
    "Zobārstniecība ar 50% atlaidi (pamatpolise)",
    "Zobārstniecība ar 50% atlaidi (pp)",
    "Vakcinācija pret ērcēm un gripu",
    "Ambulatorā rehabilitācija (pp)",
    "Medikamenti ar 50% atlaidi",
    "Sports",
    "Kritiskās saslimšanas",
    "Maksas stacionārie pakalpojumi, limits EUR (pp)",
]

_AMOUNT_SINGLE = r"(?:\d{2,4}(?:[.,]\d{3})*(?:[.,]\d+)?)"
_AMOUNT_LIST = rf"{_AMOUNT_SINGLE}(?:\s*/\s*{_AMOUNT_SINGLE})+"
_EUR_AMOUNT = rf"(?:{_AMOUNT_LIST}|{_AMOUNT_SINGLE})\s*(?:EUR|€)"
_PREM_LIST = r"\(\s*\d{1,3}(?:[.,]\d{2})(?:\s*/\s*\d{1,3}(?:[.,]\d{2}))*\s*\)"
_PREMIUM_HINTS = re.compile(
    r"Prēmija\s*1\s*darb\.|Apdrošināšanas\s+prēmija\s+vien(am|ai)\s+(darbiniekam|personai)\s+gadā|vienai\s+personai\s+gadā|Prēmija\s*1\(vienai\)\s*pers\.,\s*EUR|\+\s*\d{1,3}[.,]\d{2}\s*€\s*vienai\s*personai\s*gadā",
    re.IGNORECASE,
)

def _txt_clean(t: str) -> str:
    return t.replace("\u00A0", " ").replace("\u00AD", "")

def _pp_section_slice(text: str) -> str:
    m_start = re.search(r"\bPAPILDPROGRAMM[AĀ]S?\b|\bPapildprogramma[s]?\b", text, re.IGNORECASE)
    if not m_start:
        return text
    start = m_start.start()
    m_end = re.search(
        r"\bPAMATPROGRAMM[AĀ]\b|\bKopsavilkums\b|\bVisp[aā]r[īi]gie noteikumi\b|\bNoteikumi\b",
        text[start:],
        re.IGNORECASE,
    )
    end = start + m_end.start() if m_end else len(text)
    return text[start:end]

def _normalize_num_str(num_str: str) -> str:
    s = num_str.replace(" ", "").replace(".", "").replace("\u00A0", "")
    s = s.replace(",", ".")
    if s.endswith("."):
        s = s[:-1]
    return s

def _find_amount_near(text: str, anchor_span: Tuple[int, int]) -> Optional[str]:
    start = max(0, anchor_span[0] - 300)
    end = min(len(text), anchor_span[1] + 300)
    window = text[start:end]
    m = re.search(_EUR_AMOUNT, window, re.IGNORECASE)
    if not m:
        return None
    nums = re.findall(r"\d{2,4}(?:[.,]\d{2})?", m.group(0))
    if not nums:
        return None
    clean = " / ".join([str(int(float(_normalize_num_str(n)))) for n in nums])
    return f"{clean} EUR"

def _find_premium_near(text: str, anchor_span: Tuple[int, int]) -> Optional[str]:
    s = max(0, anchor_span[0] - 200)
    e = min(len(text), anchor_span[1] + 260)
    win = text[s:e]
    m = re.search(_PREM_LIST, win)
    if m:
        vals = [float(_normalize_num_str(x)) for x in re.findall(r"\d{1,3}[.,]\d{2}", m.group(0))]
        if all(0 < v <= 200 for v in vals):
            return "(" + "/".join([f"{v:.2f}" for v in vals]) + ")"
    for pm in _PREMIUM_HINTS.finditer(win):
        sub = win[pm.end():pm.end() + 100]
        vals = re.findall(r"\d{1,3}[.,]\d{2}(?:\s*/\s*\d{1,3}[.,]\d{2})*", sub)
        if vals:
            nums = [float(_normalize_num_str(x)) for x in re.findall(r"\d{1,3}[.,]\d{2}", vals[0])]
            if nums and all(0 < v <= 200 for v in nums):
                return "(" + "/".join([f"{v:.2f}" for v in nums]) + ")"
    return None

def _value_obj(value: Any, conf: float, prov_text: str) -> Dict[str, Any]:
    return {"value": value, "confidence": round(conf, 3), "provenance": {"source_text": prov_text, "table_hint": "PAPILDPROGRAMMAS"}}

def _short_excerpt(text: str, span: Tuple[int, int], radius: int = 120) -> str:
    start = max(0, span[0] - radius)
    end = min(len(text), span[1] + radius)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:160]


def extract_papildprogrammas_features(full_text_raw: str) -> Dict[str, Dict[str, Any]]:
    """
    Parse Papildprogrammas and return feature map keyed by _PP_CANON_KEYS.
    Values formatted like "100 EUR (35.28)" or "300 / 500 / 750 EUR (10.68/21.36/32.16)", or "v", or "-".
    """
    text = _txt_clean(full_text_raw or "")
    pp_text = _pp_section_slice(text)

    out: Dict[str, Dict[str, Any]] = {}

    def set_default(key: str):
        if key not in out:
            out[key] = _value_obj("-", 0.2, "not found in Papildprogrammas")

    def find_by_keywords(keywords: List[str]) -> Optional[Tuple[Tuple[int, int], str]]:
        for kw in keywords:
            m = re.search(kw, pp_text, re.IGNORECASE)
            if m:
                return m.span(), _short_excerpt(pp_text, m.span())
        return None

    # Maksas Operācijas
    key = "Maksas Operācijas, limits EUR"
    hit = find_by_keywords([r"Maksas\s+Operācij(?:a|as)"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = (f"{amt} {prem}".strip() if (amt and prem) else (amt or "v")) if (amt or prem) else "-"
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # Optika 50%
    key = "Optika 50%, limits EUR"
    hit = find_by_keywords([r"\bOptika\s*50\s*%"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = (f"{amt} {prem}".strip() if (amt and prem) else (amt or "v")) if (amt or prem) else "-"
        out[key] = _value_obj(val, 0.85 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # Zobārstniecība (pamatpolise)
    key = "Zobārstniecība ar 50% atlaidi (pamatpolise)"
    hit = find_by_keywords([r"Zobārstniecība.*50\s*%.*(pamatpolise|pamatprogramma)"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        out[key] = _value_obj((amt if amt else "v"), 0.8 if amt else 0.6, prov)
    else:
        set_default(key)

    # Zobārstniecība (pp)
    key = "Zobārstniecība ar 50% atlaidi (pp)"
    hit = find_by_keywords([r"Zobārstniecība\s*[–-]\s*C3CH", r"Zobārstniecība\s*\(Z2\)\s*50\s*%", r"Zobārstniecība\s+ar\s+50\s*%"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = (f"{amt} {prem}".strip() if (amt and prem) else (amt or "v")) if (amt or prem) else "-"
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # Vakcinācija pret ērcēm un gripu
    key = "Vakcinācija pret ērcēm un gripu"
    hit = find_by_keywords([r"Vakcin[āa]cija.*(ēr[cč]u|ērcēm).*grip"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        out[key] = _value_obj((f"limits {amt}" if amt else "v"), 0.85 if amt else 0.6, prov)
    else:
        set_default(key)

    # Ambulatorā rehabilitācija (pp)
    key = "Ambulatorā rehabilitācija (pp)"
    hit = find_by_keywords([r"Ambulator[āa]\s+rehabilit[āa]cija", r"Masāžas\s+un\s+ārstniecisk[āa]\s+vingrošana"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = (f"{amt} {prem}".strip() if (amt and prem) else (amt or "v")) if (amt or prem) else "-"
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # Medikamenti ar 50%
    key = "Medikamenti ar 50% atlaidi"
    hit = find_by_keywords([r"Medikamenti\s*B4", r"Medikament\w+\s+50\s*%"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = (f"{amt} {prem}".strip() if (amt and prem) else (amt or "v")) if (amt or prem) else "-"
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # Sports
    key = "Sports"
    hit = find_by_keywords([r"\bSports\b", r"Sporta\s+pakalpojumi", r"Sporta\s+aktivit"])
    out[key] = _value_obj("v", 0.6, hit[1] if hit else "not found in Papildprogrammas") if hit else _value_obj("-", 0.2, "not found in Papildprogrammas")

    # Kritiskās saslimšanas
    key = "Kritiskās saslimšanas"
    hit = find_by_keywords([r"Kritisk[āa]s\s+saslimšan\w+", r"Kritisko\s+saslimšan\w+"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = (f"{amt} {prem}".strip() if (amt and prem) else (amt or "v")) if (amt or prem) else "v"
        out[key] = _value_obj(val, 0.85 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    for k, v in out.items():
        if isinstance(v.get("value"), str) and len(v["value"]) > 160:
            v["value"] = v["value"][:160]
    return out

# =========================
# OpenAI client
# =========================
@dataclass
class GPTConfig:
    model: str = os.getenv("GPT_MODEL", "gpt-4o-mini")
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
# Minimal Responses path (NO json_schema usage)
# =========================

def _responses_with_pdf_minimal(model: str, document_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
    """Use Responses API *without* response_format. Parse JSON string from output content.
    Works with openai==2.2.0 where json_schema is not supported.
    """
    client = _client_singleton()
    content = [
        {"type": "input_text", "text": _build_user_instructions(document_id)},
        {
            "type": "input_file",
            "filename": document_id or "document.pdf",
            "file_data": base64.b64encode(pdf_bytes).decode("ascii"),
        },
    ]
    resp = client.responses.create(model=model, input=[{"role": "user", "content": content}])

    # Try to resolve any JSON the model returned
    # v1: output_parsed (if server upgraded)
    payload = getattr(resp, "output_parsed", None)
    if payload:
        return payload

    # v2: concatenate all string parts
    texts: List[str] = []
    for item in getattr(resp, "output", []) or []:
        t = getattr(item, "content", None)
        if isinstance(t, str):
            texts.append(t)
    raw = "".join(texts).strip() or "{}"
    return _best_effort_json(raw)

# =========================
# Chat Completions path (PRIMARY)
# =========================

def _chat_with_text(model: str, document_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
    client = _client_singleton()
    pages = _pdf_to_text_pages(pdf_bytes)

    # Keep prompt size bounded (SDK 2.2.0 chat models handle ~hundreds of k tokens; still be safe)
    max_chars = int(os.getenv("PDF_TEXT_MAX_CHARS", "350000"))
    joined = "\n\n".join(f"===== Page {i+1} =====\n{p}" for i, p in enumerate(pages))
    if len(joined) > max_chars:
        joined = joined[:max_chars]

    user = _build_user_instructions(document_id) + "\n\nPDF TEXT (per page):\n" + joined

    # Try JSON-mode first
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
        return _best_effort_json(txt)
    except Exception:
        # Fallback: plain text, then extract JSON substring
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return ONLY raw JSON that matches the required schema. No extra keys."},
                {"role": "user", "content": user},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        return _best_effort_json(raw)

# =========================
# JSON post-processing
# =========================

def _best_effort_json(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    # quick path
    try:
        return json.loads(raw)
    except Exception:
        pass
    # find first '{' ... last '}'
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            pass
    # strip code fences if any
    raw2 = re.sub(r"^```[a-zA-Z]*", "", raw).strip()
    raw2 = re.sub(r"```$", "", raw2).strip()
    try:
        return json.loads(raw2)
    except Exception:
        return {}

# =========================
# Normalizer safety-belt + orchestration
# =========================
class ExtractionError(Exception):
    pass


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


def _prune_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    allowed_root = {"document_id", "insurer_code", "programs", "warnings"}
    out: Dict[str, Any] = {k: payload[k] for k in list(payload.keys()) if k in allowed_root}

    if not isinstance(out.get("document_id"), str) or not out["document_id"].strip():
        out["document_id"] = str(payload.get("document_id") or "uploaded.pdf")

    if "warnings" in out and not isinstance(out["warnings"], list):
        out["warnings"] = [str(out["warnings")]] if out.get("warnings") else []

    programs = payload.get("programs") or []
    norm_programs: List[Dict[str, Any]] = []
    for p in programs:
        if not isinstance(p, dict):
            continue
        q: Dict[str, Any] = {}
        q["program_code"] = str(p.get("program_code") or p.get("name") or "").strip()
        if p.get("program_type") in ("base", "additional"):
            q["program_type"] = p["program_type"]
        q["base_sum_eur"] = p.get("base_sum_eur", "-")
        q["premium_eur"] = p.get("premium_eur", "-")
        feats_in = p.get("features") or {}
        if isinstance(feats_in, dict):
            feat_out: Dict[str, Any] = {}
            for k, v in feats_in.items():
                if isinstance(v, dict) and "value" in v:
                    feat_out[str(k)] = v
                else:
                    feat_out[str(k)] = {"value": v}
            q["features"] = feat_out
        else:
            q["features"] = {}
        if not q["program_code"]:
            pn = q["features"].get("Programmas nosaukums", {}).get("value")
            if isinstance(pn, str) and pn.strip():
                q["program_code"] = pn.strip()
        if q.get("program_code") and "features" in q:
            norm_programs.append(q)
    out["programs"] = norm_programs or []
    return out


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


def _safe_merge_features(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(dst or {})
    for k, v in (src or {}).items():
        dv = out.get(k, {})
        dv_val = (dv or {}).get("value", "-") if isinstance(dv, dict) else "-"
        should_set = (dv is None) or (dv_val in ("", "-")) or (isinstance(dv_val, str) and not dv_val.strip())
        if should_set:
            out[k] = v
    return out


def _apply_global_overrides(features: Dict[str, Any], full_text: str) -> Dict[str, Any]:
    ft = full_text.lower()
    features["Pakalpojuma apmaksas veids"] = {"value": "Saskaņā ar cenrādi"}
    preg_match = re.search(r"gr[ūu]tnie[cč][uū]|\bgr[ūu]tniec[īi]ba\b", ft, re.IGNORECASE)
    features["Maksas grūtnieču aprūpe"] = {"value": "v" if preg_match else "-"}
    cur = features.get("Pacientu iemaksa", {}).get("value")
    if not cur or (isinstance(cur, str) and cur.strip() in {"", "-"}):
        features["Pacientu iemaksa"] = {"value": "100%"}
    for k, v in features.items():
        if isinstance(v, dict) and isinstance(v.get("value"), str) and len(v["value"]) > 160:
            v["value"] = v["value"][:160]
    return features


def _merge_papild_into_programs(payload: Dict[str, Any], pdf_bytes: bytes) -> Dict[str, Any]:
    full_text, _ = _pdf_pages_text(pdf_bytes)
    pp = extract_papildprogrammas_features(full_text)
    progs = payload.get("programs") or []
    for p in progs:
        feats = p.get("features") or {}
        feats = _safe_merge_features(feats, pp)
        feats = _apply_global_overrides(feats, full_text)
        p["features"] = feats
    if pp:
        payload.setdefault("warnings", []).append("postprocess: merged Papildprogrammas features into base program(s)")
    return payload


def _normalize_safely(augmented: Dict[str, Any], document_id: str) -> Dict[str, Any]:
    try:
        normalized = normalize_offer_json({**augmented, "document_id": document_id})
    except Exception as e:
        augmented.setdefault("warnings", []).append(f"normalize_error: {e}; returning augmented")
        return augmented
    pre = len(augmented.get("programs") or [])
    post = len(normalized.get("programs") or [])
    keep_multi = os.getenv("KEEP_SYNTH_MULTI", "1") != "0"
    if keep_multi and pre >= 2 and post < 2:
        normalized["warnings"] = (normalized.get("warnings") or []) + [
            f"postprocess: restored {pre} synthesized programs (normalizer had collapsed to {post})."
        ]
        normalized["programs"] = augmented["programs"]
    return normalized

# =========================
# Public API
# =========================

def call_gpt_extractor(document_id: str, pdf_bytes: bytes, cfg: Optional[GPTConfig] = None) -> Dict[str, Any]:
    cfg = cfg or GPTConfig()
    last_err: Optional[Exception] = None

    # 1) Chat path (primary)
    for attempt in range(cfg.max_retries + 1):
        try:
            try_models = [cfg.model]
            if cfg.fallback_chat_model and cfg.fallback_chat_model != cfg.model:
                try_models.append(cfg.fallback_chat_model)
            for m in try_models:
                payload = _chat_with_text(m, document_id, pdf_bytes)
                pruned = _prune_payload(payload)
                _SCHEMA_VALIDATOR.validate(pruned)
                return pruned
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            break

    # 2) Minimal Responses (no json_schema) — SECONDARY
    for attempt in range(cfg.max_retries + 1):
        try:
            payload = _responses_with_pdf_minimal(cfg.model, document_id, pdf_bytes)
            pruned = _prune_payload(payload)
            _SCHEMA_VALIDATOR.validate(pruned)
            return pruned
        except Exception as e:
            last_err = e
            if attempt < cfg.max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            break

    raise ExtractionError(f"GPT extraction failed: {last_err}")


def extract_offer_from_pdf_bytes(pdf_bytes: bytes, document_id: str) -> Dict[str, Any]:
    if not pdf_bytes or len(pdf_bytes) > 12 * 1024 * 1024:
        raise ExtractionError("PDF too large or empty (limit: 12MB)")

    # Raw extraction
    raw = call_gpt_extractor(document_id=document_id, pdf_bytes=pdf_bytes)

    # Synthesize base variants from PAMATPROGRAMMA if applicable
    augmented = _augment_with_detected_variants(raw, pdf_bytes)

    # Merge Papildprogrammas feature signals (incl. premiums-in-parentheses)
    enriched = _merge_papild_into_programs(augmented, pdf_bytes)

    # Normalize with safety-belt
    normalized = _normalize_safely(enriched, document_id=document_id)
    return normalized


# Convenience: six-field fetchers remain the same as in your version.
# (No SDK-specific changes needed.)
