from __future__ import annotations
import re
from typing import Optional

from .schema import CascoCoverage


# ============================================================
# Helper utilities
# ============================================================

def _to_float(val: Optional[str | float | int]) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (float, int)):
        return float(val)

    text = str(val).lower().strip()

    # Remove EUR symbols, spaces, commas-as-decimals
    cleaned = (
        text.replace("eur", "")
            .replace("€", "")
            .replace(",", ".")
            .replace(" ", "")
    )

    # Special cases
    if cleaned in ["", "-", "n/a", "na"]:
        return None

    # "bez pašriska" or similar
    if "bez" in cleaned and "pašrisk" in cleaned:
        return 0.0

    # "0" or "0.0"
    match = re.match(r"^0+(\.0+)?$", cleaned)
    if match:
        return 0.0

    try:
        return float(cleaned)
    except:
        return None


def _to_bool(val: Optional[str | bool]) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val

    text = str(val).lower().strip()
    if text in ["yes", "true", "jā", "✓", "include", "included"]:
        return True
    if text in ["no", "false", "nē", "-", "not included"]:
        return False

    return None


def _normalize_territory(val: Optional[str]) -> Optional[str]:
    if not val:
        return None

    t = val.lower()

    if "eiropa" in t:
        return "Eiropa"
    if "balt" in t:
        return "Baltija"
    if "latv" in t:
        return "Latvija"

    return val.strip()


def _normalize_value_type(val: Optional[str]) -> Optional[str]:
    if not val:
        return None

    t = val.lower()

    if "jaun" in t:
        return "new"
    if "tirgus" in t:
        return "market"

    return "other"


# ============================================================
# MAIN NORMALIZER
# ============================================================

def normalize_casco_coverage(c: CascoCoverage) -> CascoCoverage:
    """
    Normalizes fields extracted by the GPT hybrid extractor.
    Ensures:
    - comparable numeric values
    - objective boolean mappings
    - standardized territory and value types
    - safe defaults for missing or ambiguous fields
    """

    # Clone to avoid modifying the original
    data = c.model_dump()

    # --------------------------
    # SIMPLE NORMALIZATIONS
    # --------------------------

    data["territory"] = _normalize_territory(c.territory)
    data["insured_value_type"] = _normalize_value_type(c.insured_value_type)

    # Deductibles
    data["deductible_damage_eur"] = _to_float(c.deductible_damage_eur)
    data["deductible_theft_eur"] = _to_float(c.deductible_theft_eur)
    data["deductible_glass_eur"] = _to_float(c.deductible_glass_eur)

    # Numbers
    data["insured_value_eur"] = _to_float(c.insured_value_eur)
    data["replacement_car_daily_limit"] = _to_float(c.replacement_car_daily_limit)
    data["towing_limit_eur"] = _to_float(c.towing_limit_eur)
    data["glass_limit_eur"] = _to_float(c.glass_limit_eur)
    data["personal_items_limit"] = _to_float(c.personal_items_limit)
    data["pa_death"] = _to_float(c.pa_death)
    data["pa_disability"] = _to_float(c.pa_disability)
    data["pa_trauma"] = _to_float(c.pa_trauma)

    # Booleans
    bool_fields = [
        "damage", "total_loss", "theft", "partial_theft", "vandalism", "fire",
        "natural_perils", "water_damage", "no_deductible_animal",
        "no_deductible_pothole", "replacement_car", "roadside_assistance",
        "glass_covered", "glass_no_deductible", "hydroshock",
        "electric_unit_damage", "careless_usage", "ferry_coverage",
        "offroad_coverage", "personal_items", "luggage_insurance",
        "accessories_insurance", "tires_insurance", "license_plate_insurance",
        "documents_insurance", "key_theft", "wrong_fuel", "washing_damage",
        "animal_damage", "pothole_coverage", "wrap_paint_damage",
        "personal_accident",
    ]

    for bf in bool_fields:
        data[bf] = _to_bool(data.get(bf))

    # Replacement car days normalization
    if c.replacement_car_days:
        try:
            data["replacement_car_days"] = int(float(str(c.replacement_car_days)))
            data["replacement_car"] = True
        except:
            pass

    # Ensure extras is always a list
    if not c.extras:
        data["extras"] = []
    else:
        cleaned_extras = []
        for item in c.extras:
            if not item:
                continue
            cleaned_extras.append(str(item).strip())
        data["extras"] = cleaned_extras

    # Return normalized CascoCoverage
    return CascoCoverage(**data)
