# app/gpt_extractor.py
"""
Robust extractor that:

* Uses Responses API with PDF as input_file (base64) when available.
* If the SDK doesn't support response_format, retries without it.
* If Responses path fails, falls back to Chat Completions with PDF text.
* Always prunes unknown keys, validates against a strict schema, then runs the normalizer.

Safe post-process:

* If the PDF clearly contains multiple *base* variants in PAMATPROGRAMMA, synthesize one programs[] per variant.
* We explicitly stop before any PAPILD... section so premiums are never taken from add-ons.
* Normalizer safety-belt: if it collapses multiple programs to a single program, restore synthesized programs
  (can be disabled via env KEEP_SYNTH_MULTI=0).

Plus:
* Parse Papildprogrammas section from raw PDF text and merge specific feature values into each base program,
  including: "Maksas Operācijas, limits EUR" and "Optika 50%, limits EUR", dentistry (pp), meds, rehab, stacionārie, kritiskās.
* Six-item fetcher with canonical-first + alias lookup and env override SIX_FIELDS.
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

    # Base + Papildprogrammas—keep labels exactly:
    "Zobārstniecība ar 50% atlaidi (pamatpolise)",
    "Zobārstniecība ar 50% atlaidi (pp)",  # extractor label (normalized later)
    "Vakcinācija pret ērcēm un gripu",
    "Ambulatorā rehabilitācija (pp)",
    "Medikamenti ar 50% atlaidi",
    "Sports",
    "Kritiskās saslimšanas",
    "Maksas stacionārie pakalpojumi, limits EUR (pp)",

    # NEW papildprogrammas:
    "Maksas Operācijas, limits EUR",
    "Optika 50%, limits EUR",
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
# PDF utils & normalization helpers
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

_BASE_ROW_RE = re.compile(
    r"^\s*(?P<name>[A-Za-zĀČĒĢĪĶĻŅŌŖŠŪŽāčēģīķļņōŗšūž0-9+/\-()., ]{3,}?)\s+"
    r"(?:(?P<count>\d{1,4})\s+)?"
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
        moneyish = [n for n in nums if n >= 150]  # ignore tiny numbers
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
# Papildprogrammas extraction & merge  (with premiums-in-parentheses)
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

# amounts: single or slash list
_AMOUNT_SINGLE = r"(?:\d{2,4}(?:[.,]\d{3})*(?:[.,]\d+)?)"
_AMOUNT_LIST = rf"{_AMOUNT_SINGLE}(?:\s*/\s*{_AMOUNT_SINGLE})+"
_EUR_AMOUNT = rf"(?:{_AMOUNT_LIST}|{_AMOUNT_SINGLE})\s*(?:EUR|€)"
# per-person premium list in parentheses, possibly slash-separated
_PREM_LIST = r"\(\s*\d{1,3}(?:[.,]\d{2})(?:\s*/\s*\d{1,3}(?:[.,]\d{2}))*\s*\)"

# proximity anchors for premium columns/wording
_PREMIUM_HINTS = re.compile(
    r"Prēmija\s*1\s*darb\.|Apdrošināšanas\s+prēmija\s+vien(am|ai)\s+(darbiniekam|personai)\s+gadā|vienai\s+personai\s+gadā|Prēmija\s*1\(vienai\)\s*pers\.,\s*EUR|\+\s*\d{1,3}[.,]\d{2}\s*€\s*vienai\s*personai\s*gadā",
    re.IGNORECASE,
)

def _txt_clean(t: str) -> str:
    return t.replace("\u00A0", " ").replace("\u00AD", "")

def _pp_section_slice(text: str) -> str:
    """Extract the Papildprogrammas area to avoid pulling base rows; if marker not found, scan full doc."""
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

def _format_amount_str(raw: str) -> str:
    # keep slash groups, add EUR
    nums = [n.strip() for n in re.split(r"/", raw.split()[0])]
    pretty = " / ".join([str(int(float(_normalize_num_str(n)))) for n in nums])
    return f"{pretty} EUR"

def _short_excerpt(text: str, span: Tuple[int, int], radius: int = 120) -> str:
    start = max(0, span[0] - radius)
    end = min(len(text), span[1] + radius)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:160]

def _find_amount_near(text: str, anchor_span: Tuple[int, int]) -> Optional[str]:
    start = max(0, anchor_span[0] - 300)
    end = min(len(text), anchor_span[1] + 300)
    window = text[start:end]
    m = re.search(_EUR_AMOUNT, window, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(0)
    # normalize to "X / Y / Z EUR"
    nums = re.findall(r"\d{2,4}(?:[.,]\d{2})?", raw)
    if not nums:
        return None
    clean = " / ".join([str(int(float(_normalize_num_str(n)))) for n in nums])
    return f"{clean} EUR"

def _find_premium_near(text: str, anchor_span: Tuple[int, int]) -> Optional[str]:
    # 1) look for explicit (xx.xx[/yy.yy]) near anchor
    s = max(0, anchor_span[0] - 200)
    e = min(len(text), anchor_span[1] + 260)
    win = text[s:e]
    m = re.search(_PREM_LIST, win)
    if m:
        # sanity filter: each value <= 200
        vals = [float(_normalize_num_str(x)) for x in re.findall(r"\d{1,3}[.,]\d{2}", m.group(0))]
        if all(0 < v <= 200 for v in vals):
            return "(" + "/".join([f"{v:.2f}" for v in vals]) + ")"

    # 2) find a premium-hint and a number sequence close by
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

def _present_or_default(match: Optional[re.Match], default: str = "-") -> str:
    return "v" if match else default

def _format_value(amount: Optional[str], premium: Optional[str], included: bool, label_limits: bool = True) -> str:
    if amount and premium:
        return f"{amount.replace(' EUR', ' EUR' if label_limits else '')} {premium}" if label_limits else f"{amount} {premium}"
    if amount and included:
        return f"{amount} (iekļauts)"
    if amount:
        return amount
    if included:
        return "v"
    return "-"

def extract_papildprogrammas_features(full_text_raw: str) -> Dict[str, Dict[str, Any]]:
    """
    Parse Papildprogrammas and return feature map keyed by _PP_CANON_KEYS.
    Values formatted like "100 EUR limits (35.28)" or "300 / 500 / 750 EUR (10.68/21.36/32.16)", or "v", or "-".
    """
    text = _txt_clean(full_text_raw or "")
    pp_text = _pp_section_slice(text)

    out: Dict[str, Dict[str, Any]] = {}
    def set_default(key: str):
        if key not in out:
            out[key] = _value_obj("-", 0.2, "not found in Papildprogrammas")

    # Helpers to find by keyword(s)
    def find_by_keywords(keywords: List[str]) -> Optional[Tuple[Tuple[int,int], str]]:
        for kw in keywords:
            m = re.search(kw, pp_text, re.IGNORECASE)
            if m:
                return m.span(), _short_excerpt(pp_text, m.span())
        return None

    # 1) Maksas Operācijas, limits EUR
    key = "Maksas Operācijas, limits EUR"
    hit = find_by_keywords([r"Maksas\s+Operācij(?:a|as)"])
    if hit:
        span, prov = hit
        special = re.search(r"saska[nņ]ā\s+ar\s+programmas\s+nosac[iī]jumiem", pp_text[max(0, span[0]-40):span[1]+200], re.IGNORECASE)
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        if special:
            out[key] = _value_obj("saskaņā ar programmas nosacījumiem", 0.9, prov)
        else:
            val = _format_value(amt, prem, included=False)
            out[key] = _value_obj(val, 0.9 if amt or prem else 0.6, prov)
    else:
        set_default(key)

    # 2) Optika 50%, limits EUR
    key = "Optika 50%, limits EUR"
    hit = find_by_keywords([r"\bOptika\s*50\s*%"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = _format_value(amt if amt else None, prem, included=bool(re.search(r"\b(iekļauts|ir\s+iekļauts|v)\b", prov, re.IGNORECASE)))
        out[key] = _value_obj(val, 0.85 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # 3) Zobārstniecība (pamatpolise) presence
    key = "Zobārstniecība ar 50% atlaidi (pamatpolise)"
    hit = find_by_keywords([r"Zobārstniecība.*50\s*%.*(pamatpolise|pamatprogramma)"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        out[key] = _value_obj((amt if amt else "v"), 0.8 if amt else 0.6, prov)
    else:
        set_default(key)

    # 4) Zobārstniecība (pp) — accept “C3CH”, “(Z2) 50%”, etc.
    key = "Zobārstniecība ar 50% atlaidi (pp)"
    hit = find_by_keywords([r"Zobārstniecība\s*[–-]\s*C3CH", r"Zobārstniecība\s*\(Z2\)\s*50\s*%", r"Zobārstniecība\s+ar\s+50\s*%"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = _format_value(amt, prem, included=False)
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # 5) Vakcinācija pret ērcēm un gripu
    key = "Vakcinācija pret ērcēm un gripu"
    hit = find_by_keywords([r"Vakcin[āa]cija.*(ēr[cč]u|ērcēm).*grip"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        out[key] = _value_obj((f"limits {amt}" if amt else "v"), 0.85 if amt else 0.6, prov)
    else:
        set_default(key)

    # 6) Ambulatorā rehabilitācija (pp) — include IF wording “Masāžas un ārstnieciskā vingrošana”
    key = "Ambulatorā rehabilitācija (pp)"
    hit = find_by_keywords([r"Ambulator[āa]\s+rehabilit[āa]cija", r"Masāžas\s+un\s+ārstniecisk[āa]\s+vingrošana"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = _format_value(amt, prem, included=False)
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # 7) Medikamenti ar 50% atlaidi — accept “Medikamenti B4”
    key = "Medikamenti ar 50% atlaidi"
    hit = find_by_keywords([r"Medikamenti\s*B4", r"Medikament\w+\s+50\s*%"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = _format_value(amt, prem, included=False)
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # 8) Sports (presence)
    key = "Sports"
    hit = find_by_keywords([r"\bSports\b", r"Sporta\s+pakalpojumi", r"Sporta\s+aktivit"])
    if hit:
        span, prov = hit
        out[key] = _value_obj("v", 0.6, prov)
    else:
        set_default(key)

    # 9) Kritiskās saslimšanas
    key = "Kritiskās saslimšanas"
    hit = find_by_keywords([r"Kritisk[āa]s\s+saslimšan\w+", r"Kritisko\s+saslimšan\w+"])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = _format_value(amt, prem, included=not bool(amt or prem), label_limits=True)
        out[key] = _value_obj(val, 0.85 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # 10) Maksas stacionārie pakalpojumi, limits EUR (pp)
    key = "Maksas stacionārie pakalpojumi, limits EUR (pp)"
    hit = find_by_keywords([
        r"Maksas\s+stacion[āa]rie\s+pakalpojumi",
        r"MAKSAS\s+STACIONĀRS",
        r"Maksas\s+stacionār[āa]\s+palīdzība",
        r"Maksas\s+pakalpojumi\s+stacionārā",
    ])
    if hit:
        span, prov = hit
        amt = _find_amount_near(pp_text, span)
        prem = _find_premium_near(pp_text, span)
        val = _format_value(amt, prem, included=False)
        out[key] = _value_obj(val, 0.9 if (amt or prem) else 0.6, prov)
    else:
        set_default(key)

    # Cap values to <= 160 chars per schema
    for k, v in out.items():
        if isinstance(v.get("value"), str) and len(v["value"]) > 160:
            v["value"] = v["value"][:160]

    return out

def _safe_merge_features(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Merge src feature objects into dst (do NOT overwrite good values)."""
    out = dict(dst or {})
    for k, v in (src or {}).items():
        dv = out.get(k, {})
        dv_val = (dv or {}).get("value", "-") if isinstance(dv, dict) else "-"
        should_set = (dv is None) or (dv_val in ("", "-")) or (isinstance(dv_val, str) and not dv_val.strip())
        if should_set:
            out[k] = v
    return out

def _apply_global_overrides(features: Dict[str, Any], full_text: str) -> Dict[str, Any]:
    """Business overrides per spec."""
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

    # Raw extraction
    raw = call_gpt_extractor(document_id=document_id, pdf_bytes=pdf_bytes)

    # Synthesize base variants from PAMATPROGRAMMA if applicable
    augmented = _augment_with_detected_variants(raw, pdf_bytes)

    # Merge Papildprogrammas feature signals (incl. premiums-in-parentheses)
    enriched = _merge_papild_into_programs(augmented, pdf_bytes)

    # Normalize with safety-belt
    normalized = _normalize_safely(enriched, document_id=document_id)
    return normalized


# =========================
# Six-field fetch helpers (NON-BREAKING ADD-ON)
# =========================

# Default 6 items (can be overridden via env SIX_FIELDS="a,b,c,d,e,f")
_DEFAULT_SIX_FIELDS = [
    "Programmas nosaukums",
    "Apdrošinājuma summa pamatpolisei, EUR",
    "Pamatpolises prēmija 1 darbiniekam, EUR",
    "Maksas Operācijas, limits EUR",
    "Optika 50%, limits EUR",
    "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)",  # canonical (normalizer)
]

# Aliases to make FE robust if extractor/normalizer labels drift
_SIX_ALT_KEYS: Dict[str, List[str]] = {
    "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)": [
        "Zobārstniecība ar 50% atlaidi (pp)",
    ],
}

def _env_six_fields() -> List[str]:
    env = os.getenv("SIX_FIELDS", "")
    if not env:
        return list(_DEFAULT_SIX_FIELDS)
    parts = [p.strip() for p in env.split(",")]
    return [p for p in parts if p] or list(_DEFAULT_SIX_FIELDS)

def _get_feature_value(prog: Dict[str, Any], key: str) -> Any:
    """Safely pull a value for a display field from a program with aliasing + smart fallbacks."""
    feats: Dict[str, Any] = prog.get("features") or {}

    # canonical-first
    if key in feats and isinstance(feats[key], dict):
        val = feats[key].get("value")
    else:
        # alias lookup
        val = None
        for alias in _SIX_ALT_KEYS.get(key, []):
            if alias in feats and isinstance(feats[alias], dict):
                val = feats[alias].get("value")
                break

    # Smart fallbacks for base/premium top-levels
    if (val in (None, "-", "")) and key == "Apdrošinājuma summa pamatpolisei, EUR":
        top = prog.get("base_sum_eur")
        return top if top not in (None, "") else "-"
    if (val in (None, "-", "")) and key == "Pamatpolises prēmija 1 darbiniekam, EUR":
        top = prog.get("premium_eur")
        return top if top not in (None, "") else "-"

    return val if (val not in (None, "")) else "-"

def fetch_six_items_from_payload(payload: Dict[str, Any], fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Create a compact list per program with exactly 6 items (order preserved)."""
    fields = fields or _env_six_fields()
    progs = payload.get("programs") or []
    out: List[Dict[str, Any]] = []
    for p in progs:
        row = {"program_code": p.get("program_code", "-")}
        for f in fields:
            row[f] = _get_feature_value(p, f)
        out.append(row)
    return out

def extract_offer_and_fetch_six(pdf_bytes: bytes, document_id: str, fields: Optional[List[str]] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Convenience wrapper: returns (full_payload, six_items_list)"""
    payload = extract_offer_from_pdf_bytes(pdf_bytes, document_id)
    six = fetch_six_items_from_payload(payload, fields=fields)
    return payload, six
