from __future__ import annotations

from typing import List, Dict, Any

from .schema import (
    CascoCoverage,
    CASCO_COMPARISON_ROWS,
    CascoComparisonRow,
)


def build_casco_comparison_matrix(
    raw_offers: List[Dict[str, Any]],  # ✅ FIX: Accept full DB records
) -> Dict[str, Any]:
    """
    Build a comparison table matrix for the frontend.

    ✅ FIXED ISSUES:
    1. Handles duplicate insurer names by using unique column IDs
    2. Includes premium_total and other metadata in comparison
    3. No value overwrites - each offer gets unique key
    
    Produces:
    {
      "rows":     [row definitions from schema + metadata rows],
      "columns":  ["BALTA #1", "BALTA #2", "BALCIA", ...],  # Unique IDs
      "values":   { (row_code, column_id): value }
      "metadata": { column_id: {premium_total, insured_amount, ...} }
    }

    Objective rules:
    - If insurer has no value for a field → None (frontend shows "-")
    - Never guess missing data
    - No transformations here (normalizer already handled that)
    """

    # --------------------------------------
    # 1. Build unique column IDs
    # --------------------------------------
    columns: List[str] = []
    column_metadata: Dict[str, Dict[str, Any]] = {}
    insurer_counts: Dict[str, int] = {}  # Track duplicates
    
    for raw_offer in raw_offers:
        insurer = raw_offer.get("insurer_name", "Unknown")
        offer_id = raw_offer.get("id")
        
        # Count occurrences of this insurer
        insurer_counts[insurer] = insurer_counts.get(insurer, 0) + 1
        count = insurer_counts[insurer]
        
        # Build unique column ID
        if count == 1:
            # First offer from this insurer - use plain name
            column_id = insurer
        else:
            # Duplicate insurer - add counter
            # Change first occurrence to have #1
            if count == 2:
                # Find and update first occurrence
                first_idx = columns.index(insurer)
                old_id = columns[first_idx]
                new_id = f"{insurer} #1"
                columns[first_idx] = new_id
                # Move metadata
                column_metadata[new_id] = column_metadata.pop(old_id)
            
            # Add current offer with counter
            column_id = f"{insurer} #{count}"
        
        columns.append(column_id)
        
        # ✅ FIX #3: Store metadata for each offer
        column_metadata[column_id] = {
            "offer_id": offer_id,
            "premium_total": raw_offer.get("premium_total"),
            "insured_amount": raw_offer.get("insured_amount"),
            "currency": raw_offer.get("currency", "EUR"),
            "territory": raw_offer.get("territory"),
            "period_from": str(raw_offer.get("period_from")) if raw_offer.get("period_from") else None,
            "period_to": str(raw_offer.get("period_to")) if raw_offer.get("period_to") else None,
            "premium_breakdown": raw_offer.get("premium_breakdown"),
            "created_at": str(raw_offer.get("created_at")) if raw_offer.get("created_at") else None,
        }

    # --------------------------------------
    # 2. Build values map with unique keys
    # --------------------------------------
    values: Dict[str, Any] = {}
    
    for idx, raw_offer in enumerate(raw_offers):
        column_id = columns[idx]
        
        # Parse coverage JSONB
        coverage_data = raw_offer.get("coverage", {})
        if not isinstance(coverage_data, dict):
            continue
            
        try:
            coverage = CascoCoverage(**coverage_data)
        except Exception as e:
            print(f"[WARN] Failed to parse coverage for {column_id}: {e}")
            continue
        
        # Extract values for each comparison row
        for row in CASCO_COMPARISON_ROWS:
            code = row.code
            
            # Extract field value from CascoCoverage
            value = getattr(coverage, code, None)
            
            # ✅ FIX #2: Use unique column_id as key (no collision)
            key = f"{code}::{column_id}"
            values[key] = value

    # --------------------------------------
    # 3. Add metadata rows for premium, etc.
    # --------------------------------------
    metadata_rows = [
        CascoComparisonRow(
            code="premium_total",
            label="Prēmija kopā EUR",
            group="pricing",
            type="number"
        ),
        CascoComparisonRow(
            code="insured_amount",
            label="Apdrošināmā summa EUR",
            group="pricing",
            type="number"
        ),
    ]
    
    # Add metadata values to values dict
    for column_id, metadata in column_metadata.items():
        values[f"premium_total::{column_id}"] = metadata.get("premium_total")
        values[f"insured_amount::{column_id}"] = metadata.get("insured_amount")
    
    # Combine all rows
    all_rows = metadata_rows + CASCO_COMPARISON_ROWS

    # --------------------------------------
    # 4. Return structure for FE
    # --------------------------------------
    return {
        "rows": [r.model_dump() for r in all_rows],
        "columns": columns,  # ✅ FIX #1: Unique column IDs
        "values": values,     # ✅ FIX #2: No collision
        "metadata": column_metadata,  # ✅ FIX #3: Full metadata for each offer
    }
