from pydantic import BaseModel
from typing import Optional, List, Literal


# ---------------------------------------
# CASCO Coverage Data Model
# ---------------------------------------

class CascoCoverage(BaseModel):
    # Metadata
    insurer_name: str
    product_name: Optional[str] = None
    offer_id: Optional[str] = None
    pdf_filename: Optional[str] = None

    # A. Core Coverage
    damage: Optional[bool] = None
    total_loss: Optional[bool] = None
    theft: Optional[bool] = None
    partial_theft: Optional[bool] = None
    vandalism: Optional[bool] = None
    fire: Optional[bool] = None
    natural_perils: Optional[bool] = None
    water_damage: Optional[bool] = None

    # B. Territory
    territory: Optional[str] = None

    # C. Insured Value
    insured_value_type: Optional[Literal["market", "new", "other"]] = None
    insured_value_eur: Optional[float] = None

    # D. Deductibles
    deductible_damage_eur: Optional[float] = None
    deductible_theft_eur: Optional[float] = None
    deductible_glass_eur: Optional[float] = None
    no_deductible_animal: Optional[bool] = None
    no_deductible_pothole: Optional[bool] = None

    # E. Mobility
    replacement_car: Optional[bool] = None
    replacement_car_days: Optional[int] = None
    replacement_car_daily_limit: Optional[float] = None
    roadside_assistance: Optional[bool] = None
    towing_limit_eur: Optional[float] = None

    # F. Glass
    glass_covered: Optional[bool] = None
    glass_no_deductible: Optional[bool] = None
    glass_limit_eur: Optional[float] = None

    # G. Mechanical / Special Risks
    hydroshock: Optional[bool] = None
    electric_unit_damage: Optional[bool] = None
    careless_usage: Optional[bool] = None
    ferry_coverage: Optional[bool] = None
    offroad_coverage: Optional[bool] = None

    # H. Personal Items / Accessories
    personal_items: Optional[bool] = None
    personal_items_limit: Optional[float] = None
    luggage_insurance: Optional[bool] = None
    accessories_insurance: Optional[bool] = None
    tires_insurance: Optional[bool] = None
    license_plate_insurance: Optional[bool] = None
    documents_insurance: Optional[bool] = None

    # I. Keys & Fuel & Washing
    key_theft: Optional[bool] = None
    wrong_fuel: Optional[bool] = None
    washing_damage: Optional[bool] = None

    # J. Animal / Road Risks
    animal_damage: Optional[bool] = None
    pothole_coverage: Optional[bool] = None
    wrap_paint_damage: Optional[bool] = None

    # K. Personal Accident (PA)
    personal_accident: Optional[bool] = None
    pa_death: Optional[float] = None
    pa_disability: Optional[float] = None
    pa_trauma: Optional[float] = None

    # L. Unique Extras
    extras: Optional[List[str]] = None


# ---------------------------------------
# Comparison Table Row Definition
# ---------------------------------------

class CascoComparisonRow(BaseModel):
    code: str                # internal stable ID
    label: str               # human readable
    group: str               # section (core, deductibles, mobility…)
    type: Literal["bool", "number", "text", "list"]


# ---------------------------------------
# Static Row Definitions (for FE table)
# ---------------------------------------

CASCO_COMPARISON_ROWS: List[CascoComparisonRow] = [
    # A. Core Coverage
    CascoComparisonRow(code="damage", label="Bojājumi", group="core", type="bool"),
    CascoComparisonRow(code="total_loss", label="Bojāeja", group="core", type="bool"),
    CascoComparisonRow(code="theft", label="Zādzība", group="core", type="bool"),
    CascoComparisonRow(code="partial_theft", label="Apzagšana", group="core", type="bool"),
    CascoComparisonRow(code="vandalism", label="Vandālisms", group="core", type="bool"),
    CascoComparisonRow(code="fire", label="Uguns / aizdegšanās", group="core", type="bool"),
    CascoComparisonRow(code="natural_perils", label="Dabas stihijas", group="core", type="bool"),
    CascoComparisonRow(code="water_damage", label="Ūdens bojājumi", group="core", type="bool"),

    # B. Territory
    CascoComparisonRow(code="territory", label="Teritorija", group="territory", type="text"),

    # C. Insured Value
    CascoComparisonRow(code="insured_value_type", label="Apdrošinājuma veids", group="value", type="text"),
    CascoComparisonRow(code="insured_value_eur", label="Apdrošinājuma summa EUR", group="value", type="number"),

    # D. Deductibles
    CascoComparisonRow(code="deductible_damage_eur", label="Pašrisks bojājumiem EUR", group="deductibles", type="number"),
    CascoComparisonRow(code="deductible_theft_eur", label="Pašrisks zādzībai EUR", group="deductibles", type="number"),
    CascoComparisonRow(code="deductible_glass_eur", label="Pašrisks stikliem EUR", group="deductibles", type="number"),
    CascoComparisonRow(code="no_deductible_animal", label="Bez pašriska sadursmei ar dzīvnieku", group="deductibles", type="bool"),
    CascoComparisonRow(code="no_deductible_pothole", label="Bez pašriska iebraukšanai bedrē", group="deductibles", type="bool"),

    # E. Mobility
    CascoComparisonRow(code="replacement_car", label="Maiņas / nomas auto", group="mobility", type="bool"),
    CascoComparisonRow(code="replacement_car_days", label="Maiņas auto (dienas)", group="mobility", type="number"),
    CascoComparisonRow(code="replacement_car_daily_limit", label="Dienas limits EUR", group="mobility", type="number"),
    CascoComparisonRow(code="roadside_assistance", label="Ceļa palīdzība", group="mobility", type="bool"),
    CascoComparisonRow(code="towing_limit_eur", label="Transportēšanas limits EUR", group="mobility", type="number"),

    # F. Glass
    CascoComparisonRow(code="glass_covered", label="Stiklojums apdrošināts", group="glass", type="bool"),
    CascoComparisonRow(code="glass_no_deductible", label="Stikli bez pašriska", group="glass", type="bool"),
    CascoComparisonRow(code="glass_limit_eur", label="Stiklojuma limits", group="glass", type="number"),

    # G. Mechanical / Special Risks
    CascoComparisonRow(code="hydroshock", label="Hidrotrieciens", group="special", type="bool"),
    CascoComparisonRow(code="electric_unit_damage", label="Elektronikas bojājumi", group="special", type="bool"),
    CascoComparisonRow(code="careless_usage", label="Bezrūpības risks", group="special", type="bool"),
    CascoComparisonRow(code="ferry_coverage", label="Segums uz prāmja", group="special", type="bool"),
    CascoComparisonRow(code="offroad_coverage", label="Ārpus ceļu seguma", group="special", type="bool"),

    # H. Personal Items / Accessories
    CascoComparisonRow(code="personal_items", label="Personīgās mantas", group="items", type="bool"),
    CascoComparisonRow(code="personal_items_limit", label="Personīgo mantu limits", group="items", type="number"),
    CascoComparisonRow(code="luggage_insurance", label="Bagāža", group="items", type="bool"),
    CascoComparisonRow(code="accessories_insurance", label="Papildaprīkojums", group="items", type="bool"),
    CascoComparisonRow(code="tires_insurance", label="Riepas & diski", group="items", type="bool"),
    CascoComparisonRow(code="license_plate_insurance", label="Numurzīmes", group="items", type="bool"),
    CascoComparisonRow(code="documents_insurance", label="Dokumenti", group="items", type="bool"),

    # I. Keys & Fuel & Washing
    CascoComparisonRow(code="key_theft", label="Atslēgas", group="minor", type="bool"),
    CascoComparisonRow(code="wrong_fuel", label="Degvielas sajaukšana", group="minor", type="bool"),
    CascoComparisonRow(code="washing_damage", label="Bojājumi mazgāšanā", group="minor", type="bool"),

    # J. Animal / Road Risks
    CascoComparisonRow(code="animal_damage", label="Dzīvnieku bojājumi", group="road", type="bool"),
    CascoComparisonRow(code="pothole_coverage", label="Iebraukšana bedrē", group="road", type="bool"),
    CascoComparisonRow(code="wrap_paint_damage", label="Aplīmējums / aerogrāfija", group="road", type="bool"),

    # K. Personal Accident
    CascoComparisonRow(code="personal_accident", label="Nelaimes gadījumi", group="pa", type="bool"),
    CascoComparisonRow(code="pa_death", label="PA nāve EUR", group="pa", type="number"),
    CascoComparisonRow(code="pa_disability", label="PA invaliditāte EUR", group="pa", type="number"),
    CascoComparisonRow(code="pa_trauma", label="PA traumas EUR", group="pa", type="number"),

    # L. Unique Extras
    CascoComparisonRow(code="extras", label="Papildus ekstras", group="extras", type="list"),
]

