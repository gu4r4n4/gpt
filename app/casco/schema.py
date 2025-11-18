"""
CASCO Schema - Simplified 19-Field Model

This schema defines the CASCO coverage structure using Latvian field names
and a simple string-based value system:
- "v" = coverage included
- "-" = not covered
- Any other string = specific value/limit/description

This replaces the old 60+ field typed model with a cleaner, more maintainable approach.
"""
from pydantic import BaseModel
from typing import Optional, List, Literal


# ---------------------------------------
# CASCO Coverage Data Model (19 Fields)
# ---------------------------------------

class CascoCoverage(BaseModel):
    """
    Simplified CASCO coverage model with 19 Latvian-named fields.
    All coverage fields are strings: "v" (covered), "-" (not covered), or descriptive value.
    """
    # Metadata (preserved for compatibility)
    insurer_name: str
    pdf_filename: Optional[str] = None

    # 19 Coverage Fields (Latvian names, all strings)
    Bojājumi: Optional[str] = None                              # 1. Damage coverage
    Bojāeja: Optional[str] = None                               # 2. Total loss
    Zādzība: Optional[str] = None                               # 3. Theft
    Apzagšana: Optional[str] = None                             # 4. Burglary
    Teritorija: Optional[str] = None                            # 5. Territory (value expected)
    Pašrisks_bojājumi: Optional[str] = None                     # 6. Deductible - damage (value expected, using underscore for Python compatibility)
    Stiklojums_bez_pašriska: Optional[str] = None               # 7. Glass no deductible
    Maiņas_nomas_auto_dienas: Optional[str] = None              # 8. Replacement car (value expected)
    Palīdzība_uz_ceļa: Optional[str] = None                     # 9. Roadside assistance
    Hidrotrieciens: Optional[str] = None                        # 10. Hydro strike
    Personīgās_mantas_bagāža: Optional[str] = None              # 11. Personal items / baggage
    Atslēgu_zādzība_atjaunošana: Optional[str] = None           # 12. Key theft/replacement
    Degvielas_sajaukšana_tīrīšana: Optional[str] = None         # 13. Fuel mixing/cleaning
    Riepas_diski: Optional[str] = None                          # 14. Tyres / wheels
    Numurzīmes: Optional[str] = None                            # 15. License plates
    Nelaimes_gad_vadīt_pasažieriem: Optional[str] = None        # 16. Personal accident (value expected)
    Sadursme_ar_dzīvnieku: Optional[str] = None                 # 17. Animal collision
    Uguns_dabas_stihijas: Optional[str] = None                  # 18. Fire / natural perils
    Vandālisms: Optional[str] = None                            # 19. Vandalism


# ---------------------------------------
# Comparison Table Row Definition
# ---------------------------------------

class CascoComparisonRow(BaseModel):
    code: str                # internal stable ID (matches CascoCoverage field name)
    label: str               # human readable label (Latvian)
    group: str               # section grouping
    type: Literal["text", "number"]  # "text" for coverage fields, "number" for premium/amounts


# ---------------------------------------
# Static Row Definitions (for FE table)
# ---------------------------------------

CASCO_COMPARISON_ROWS: List[CascoComparisonRow] = [
    # Core Coverage
    CascoComparisonRow(code="Bojājumi", label="Bojājumi", group="core", type="text"),
    CascoComparisonRow(code="Bojāeja", label="Bojāeja", group="core", type="text"),
    CascoComparisonRow(code="Zādzība", label="Zādzība", group="core", type="text"),
    CascoComparisonRow(code="Apzagšana", label="Apzagšana", group="core", type="text"),
    CascoComparisonRow(code="Vandālisms", label="Vandālisms", group="core", type="text"),
    CascoComparisonRow(code="Uguns_dabas_stihijas", label="Uguns / dabas stihijas", group="core", type="text"),
    CascoComparisonRow(code="Sadursme_ar_dzīvnieku", label="Sadursme ar dzīvnieku", group="core", type="text"),
    
    # Territory & Deductibles
    CascoComparisonRow(code="Teritorija", label="Teritorija", group="territory", type="text"),
    CascoComparisonRow(code="Pašrisks_bojājumi", label="Pašrisks – bojājumi", group="deductibles", type="text"),
    CascoComparisonRow(code="Stiklojums_bez_pašriska", label="Stiklojums bez pašriska", group="deductibles", type="text"),
    
    # Mobility & Services
    CascoComparisonRow(code="Maiņas_nomas_auto_dienas", label="Maiņas / nomas auto (dienas)", group="mobility", type="text"),
    CascoComparisonRow(code="Palīdzība_uz_ceļa", label="Palīdzība uz ceļa", group="mobility", type="text"),
    
    # Special Coverages
    CascoComparisonRow(code="Hidrotrieciens", label="Hidrotrieciens", group="special", type="text"),
    CascoComparisonRow(code="Personīgās_mantas_bagāža", label="Personīgās mantas / bagāža", group="special", type="text"),
    CascoComparisonRow(code="Atslēgu_zādzība_atjaunošana", label="Atslēgu zādzība/atjaunošana", group="special", type="text"),
    CascoComparisonRow(code="Degvielas_sajaukšana_tīrīšana", label="Degvielas sajaukšana/tīrīšana", group="special", type="text"),
    CascoComparisonRow(code="Riepas_diski", label="Riepas / diski", group="special", type="text"),
    CascoComparisonRow(code="Numurzīmes", label="Numurzīmes", group="special", type="text"),
    
    # Personal Accident
    CascoComparisonRow(code="Nelaimes_gad_vadīt_pasažieriem", label="Nelaimes gad. vadīt./pasažieriem", group="pa", type="text"),
]

