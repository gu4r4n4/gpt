from __future__ import annotations

from typing import List, Dict, Any, Tuple

from .schema import (
    CascoCoverage,
    CASCO_COMPARISON_ROWS,
    CascoComparisonRow,
)


def build_casco_comparison_matrix(
    offers: List[CascoCoverage],
) -> Dict[str, Any]:
    """
    Build a comparison table matrix for the frontend.

    Produces:
    {
      "rows":     [row definitions from schema],
      "columns":  ["Balta", "Gjensidige", ...],
      "values":   { (row_code, insurer): value }
    }

    Objective rules:
    - If insurer has no value for a field → None (frontend shows "-")
    - Never guess missing data
    - No transformations here (normalizer already handled that)
    """

    # --------------------------------------
    # 1. Columns = insurer names
    # --------------------------------------
    columns: List[str] = [o.insurer_name for o in offers]

    # --------------------------------------
    # 2. Build values map
    # --------------------------------------
    values: Dict[Tuple[str, str], Any] = {}

    for row in CASCO_COMPARISON_ROWS:
        for offer in offers:
            insurer = offer.insurer_name
            code = row.code

            # Extract field value from CascoCoverage
            value = getattr(offer, code, None)

            # Store matrix cell
            values[(code, insurer)] = value

    # --------------------------------------
    # 3. Return structure for FE
    # --------------------------------------
    return {
        "rows": [r.model_dump() for r in CASCO_COMPARISON_ROWS],
        "columns": columns,
        "values": {
            f"{code}::{insurer}": val
            for (code, insurer), val in values.items()
        },
    }
