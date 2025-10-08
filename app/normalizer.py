# app/normalizer.py
from __future__ import annotations
from typing import Any, Dict, List

MISSING = "-"

# Keep this list in sync with extraction prompt and FE needs
FEATURE_KEYS: List[str] = [
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

    # Papildprogrammas (canonical for BE; FE aliases can map to their preferred labels)
    "Zobārstniecība ar 50% atlaidi (pamatpolise)",
    "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)",  # canonical in normalizer
    "Vakcinācija pret ērcēm un gripu",
    "Ambulatorā rehabilitācija (pp)",
    "Medikamenti ar 50% atlaidi",
    "Sports",
    "Kritiskās saslimšanas",
    "Maksas stacionārie pakalpojumi, limits EUR (pp)",

    # NEW fields
    "Maksas Operācijas, limits EUR",
    "Optika 50%, limits EUR",
]

# Map legacy or alternate labels to the canonical ones above
LEGACY_KEYS_MAP = {
    "Vakcinācija pret ērčiem un gripu": "Vakcinācija pret ērcēm un gripu",
    # FE/extractor might use the shorter "(pp)" label; map to canonical with "apdrošinājuma summa"
    "Zobārstniecība ar 50% atlaidi (pp)": "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)",
    # keep other existing mappings (e.g., diacritics drift)
    "Vakcinācija pret ercem un gripu": "Vakcinācija pret ērcēm un gripu",
}

def _unwrap(v: Any) -> str:
    if v is None:
        return MISSING
    if isinstance(v, dict) and "value" in v:
        v = v.get("value")
    if isinstance(v, (int, float)):
        s = str(v)
        return s[:-2] if s.endswith(".0") else s
    s = str(v).strip()
    return s if s else MISSING

def _is_missing(v: Any) -> bool:
    return v in (None, "", MISSING, "-")

def _coerce_feature_value(v: Any) -> str:
    s = _unwrap(v)
    # collapse merged values with semicolons/commas if the model produced them
    if ";" in s:
        parts = [p.strip() for p in s.split(";") if p.strip()]
        s = parts[0] if parts else MISSING
    if len(s) > 160:
        s = s[:160]
    return s

def _coerce_base_sum(v: Any) -> Any:
    s = _unwrap(v)
    if s == MISSING:
        return MISSING
    try:
        return int(float(str(s).replace("€", "").replace(" EUR", "").strip()))
    except Exception:
        return MISSING

def _coerce_premium(v: Any) -> str:
    s = _unwrap(v)
    if s == MISSING:
        return MISSING
    s = s.replace("EUR", "").replace("€", "").strip()
    return s if s else MISSING

# ---------------- Formatting helpers -----------------
def _fmt_eur(n: Any) -> str:
    try:
        x = float(str(n).replace("€", "").replace(" EUR", "").strip())
        return f"{int(x)} EUR" if float(int(x)) == x else f"{x} EUR"
    except Exception:
        return MISSING

def _presence_to_v(value: str) -> str:
    """For presence-only fields, convert any non-missing content to 'v'."""
    return "v" if value not in (None, "", MISSING, "-") else MISSING

# --------------- Papildprogramma folding ---------------
def _is_pp_program(name: str) -> bool:
    s = (name or "").lower()
    return ("papildprogram" in s) or any(k in s for k in [
        "zobārst", "kritisk", "rehabilit", "sports", "stacionār", "medikament", "operāc", "optik"
    ])

def _fold_papild_into_base(programs: List[Dict[str, Any]], insurer_code: str | None = None) -> List[Dict[str, Any]]:
    """
    If the extractor returned multiple 'programs' (some of which are add-ons),
    fold add-on values into the first non-PP 'base' program.
    """
    if not programs:
        return programs

    # choose base: first non-PP
    base_idx = 0
    for i, p in enumerate(programs):
        code = _coerce_feature_value(p.get("program_code"))
        if not _is_pp_program(code):
            base_idx = i
            break

    base = programs[base_idx]
    base_feats: Dict[str, str] = base.get("features", {}) or {}

    def set_if(val: str, key: str):
        if isinstance(val, str) and val.strip() and val not in (MISSING, "-"):
            base_feats[key] = val.strip()

    for i, p in enumerate(programs):
        if i == base_idx:
            continue
        name_raw = p.get("program_code") or p.get("features", {}).get("Programmas nosaukums") or ""
        name = str(name_raw).lower()
        feats = p.get("features", {}) or {}
        sum_pp = p.get("base_sum_eur", MISSING)
        sum_pp_str = _fmt_eur(sum_pp) if sum_pp != MISSING else MISSING

        if "zobārst" in name:
            # canonical for BE
            set_if(sum_pp_str, "Zobārstniecība ar 50% atlaidi, apdrošinājuma summa (pp)")
        elif "kritisk" in name:
            set_if(sum_pp_str, "Kritiskās saslimšanas")
        elif "rehabilit" in name and "ambulator" in name:
            set_if(sum_pp_str, "Ambulatorā rehabilitācija (pp)")
        elif "medikament" in name:
            v = _coerce_feature_value(feats.get("Medikamenti ar 50% atlaidi", MISSING))
            set_if(v if v != MISSING else sum_pp_str, "Medikamenti ar 50% atlaidi")
        elif "sports" in name:
            v = _coerce_feature_value(feats.get("Sports", MISSING))
            set_if(v if v != MISSING else sum_pp_str, "Sports")
        elif "stacionār" in name:
            v = _coerce_feature_value(feats.get("Maksas stacionārie pakalpojumi, limits EUR", MISSING))
            set_if("ir iekļauts" if (v != MISSING or sum_pp != MISSING) else MISSING, "Maksas stacionārie pakalpojumi, limits EUR (pp)")
        elif "operāc" in name:
            # NEW: operations (if presented as a separate PP)
            v = _coerce_feature_value(feats.get("Maksas Operācijas, limits EUR", MISSING))
            if v != MISSING:
                set_if(v, "Maksas Operācijas, limits EUR")
            else:
                set_if(sum_pp_str if sum_pp_str != MISSING else "v", "Maksas Operācijas, limits EUR")
        elif "optik" in name:
            # NEW: optics 50%
            v = _coerce_feature_value(feats.get("Optika 50%, limits EUR", MISSING))
            if v != MISSING:
                set_if(v, "Optika 50%, limits EUR")
            else:
                set_if("v" if sum_pp_str == MISSING else f"ir iekļauts ({sum_pp_str.replace(' EUR','') } EUR)", "Optika 50%, limits EUR")

    # Hard default: Pacientu iemaksa = 100% if missing on base
    if base_feats.get("Pacientu iemaksa", MISSING) in (MISSING, "-"):
        base_feats["Pacientu iemaksa"] = "100%"

    # Special case per earlier note: Compensa VA => Maksas diagnostika = 'v' if empty
    ic = (insurer_code or "").lower()
    pc = (base.get("program_code") or "").lower()
    if ("compensa" in ic) and ("va" in pc):
        key_diag = "Maksas diagnostika, piem., rentgens, elektrokradiogramma, USG, utml."
        if base_feats.get(key_diag, MISSING) in (MISSING, "-"):
            base_feats[key_diag] = "v"

    base["features"] = base_feats
    return [base]

# -------------------- Normalization entry -----------------
def normalize_offer_json(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "document_id": _unwrap(doc.get("document_id")) or MISSING,
        "insurer_code": _unwrap(doc.get("insurer_code")) or MISSING,
        # optional user-supplied metadata if present
        "insurer": _unwrap(doc.get("insurer")) or MISSING,
        "company": _unwrap(doc.get("company")) or MISSING,
        "insured_count": doc.get("insured_count") if isinstance(doc.get("insured_count"), int) else MISSING,
        "inquiry_id": _unwrap(doc.get("inquiry_id")) or MISSING,
        "programs": [],
        "warnings": [],
    }

    warnings_in = doc.get("warnings")
    if isinstance(warnings_in, list):
        out["warnings"] = [str(_unwrap(w)) for w in warnings_in if not _is_missing(w)]

    for p in doc.get("programs", []) or []:
        features_in = p.get("features") or {}

        # Map legacy keys to corrected ones if needed
        for old_k, new_k in LEGACY_KEYS_MAP.items():
            if old_k in features_in and new_k not in features_in:
                features_in[new_k] = features_in.get(old_k)

        features_out: Dict[str, str] = {}
        for key in FEATURE_KEYS:
            features_out[key] = _coerce_feature_value(features_in.get(key, MISSING))

        # --- Business rule overrides ---
        # Pakalpojuma apmaksas veids → always "Saskaņā ar cenrādi"
        features_out["Pakalpojuma apmaksas veids"] = "Saskaņā ar cenrādi"
        # Maksas grūtnieču aprūpe → presence-only (v / -)
        features_out["Maksas grūtnieču aprūpe"] = _presence_to_v(features_out.get("Maksas grūtnieču aprūpe", MISSING))

        program_code_any = p.get("program_code") or features_out.get("Programmas kods") or features_out.get("Programmas nosaukums")
        program_code = _coerce_feature_value(program_code_any) or "-"
        if program_code == MISSING:
            program_code = "-"

        base_sum_eur = _coerce_base_sum(p.get("base_sum_eur"))
        premium_eur = _coerce_premium(p.get("premium_eur"))

        # Backfill key figures into feature block when missing
        if features_out.get("Apdrošinājuma summa pamatpolisei, EUR", MISSING) == MISSING and base_sum_eur != MISSING:
            features_out["Apdrošinājuma summa pamatpolisei, EUR"] = str(base_sum_eur)
        if features_out.get("Pamatpolises prēmija 1 darbiniekam, EUR", MISSING) == MISSING and premium_eur != MISSING:
            features_out["Pamatpolises prēmija 1 darbiniekam, EUR"] = premium_eur

        out["programs"].append({
            "program_code": program_code,
            "base_sum_eur": base_sum_eur,
            "premium_eur": premium_eur,
            "features": features_out,
        })

    # Fold Papildprogrammas programs (if any) into the base one and apply defaults
    out["programs"] = _fold_papild_into_base(out["programs"], insurer_code=out.get("insurer_code"))

    return out
