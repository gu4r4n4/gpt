# CASCO `insured_amount` Always Returns "Tirgus vērtība"

## Summary

Modified the entire CASCO backend to ensure `insured_amount` is **ALWAYS** set to the text value `"Tirgus vērtība"` (Market value) across all layers:

1. ✅ Extractor - Overrides GPT output
2. ✅ Persistence - Stores as TEXT (not Decimal)
3. ✅ Routes - Passes string value (no conversion)
4. ✅ Comparator - Displays as text field

---

## Changes Made

### 1. **app/casco/extractor.py** (Line 642-643)

**Added override after JSON parsing:**

```python
# Override insured_amount to always be "Tirgus vērtība"
mapped_payload["insured_amount"] = "Tirgus vērtība"
```

**Location:** Inside `extract_casco_offers_from_text()` function  
**Execution:** After GPT response is parsed and keys are mapped, but before Pydantic validation

**Effect:** Regardless of what GPT extracts from the PDF, `insured_amount` is always forced to `"Tirgus vērtība"`

---

### 2. **app/casco/persistence.py** (Line 31)

**Changed field type from Decimal to string:**

```python
# Before:
insured_amount: Optional[Decimal] = None

# After:
insured_amount: Optional[str] = None  # Always "Tirgus vērtība" for CASCO
```

**Effect:** Database persistence layer now accepts and stores text value instead of numeric

---

### 3. **app/routes/casco_routes.py**

#### Single Upload Endpoint (Lines 253, 267, 275)

**Removed Decimal conversion:**

```python
# Line 253 - Added fallback
insured_amount_str = coverage.insured_amount if hasattr(coverage, 'insured_amount') else "Tirgus vērtība"

# Line 267 - Removed: insured_amount_decimal = to_decimal(insured_amount_str)
# Added comment: insured_amount is always "Tirgus vērtība" (text, not converted to Decimal)

# Line 275 - Changed assignment
insured_amount=insured_amount_str,  # Always "Tirgus vērtība"
```

#### Batch Upload Endpoint (Lines 383, 397, 404)

**Same changes as single upload:**

```python
# Line 383 - Added fallback
insured_amount_str = coverage.insured_amount if hasattr(coverage, 'insured_amount') else "Tirgus vērtība"

# Line 397 - Removed: insured_amount_decimal = to_decimal(insured_amount_str)
# Added comment: insured_amount is always "Tirgus vērtība" (text, not converted to Decimal)

# Line 404 - Changed assignment
insured_amount=insured_amount_str,  # Always "Tirgus vērtība"
```

**Effect:** Routes no longer attempt to parse insured_amount as numeric value; they pass the text string directly to `CascoOfferRecord`

---

### 4. **app/casco/comparator.py** (Line 129)

**Changed field type from "number" to "text":**

```python
CascoComparisonRow(
    code="insured_amount",
    label="Apdrošinājuma summa",
    group="financial",
    type="text"  # Always "Tirgus vērtība"
),
```

**Effect:** Frontend comparison table now treats `insured_amount` as text field, not numeric

---

### 5. **app/casco/service.py** (Lines 70, 104)

**Updated deprecated function for consistency:**

```python
# Line 70 - Changed parameter type
insured_amount: Optional[str] = None,  # Always "Tirgus vērtība" for CASCO

# Line 104 - Changed to use extractor value
insured_amt = coverage.insured_amount if hasattr(coverage, 'insured_amount') else "Tirgus vērtība"
```

**Note:** This function (`process_and_persist_casco_pdf`) appears to be unused/deprecated (uses old `inquiry_id` system), but updated for consistency.

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. EXTRACTOR (app/casco/extractor.py)                      │
│    - GPT extracts all fields from PDF                       │
│    - Override applied: insured_amount = "Tirgus vērtība"    │
│    - Returns CascoCoverage with text value                  │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ROUTES (app/routes/casco_routes.py)                     │
│    - Receives extraction result                             │
│    - Extracts insured_amount_str from coverage              │
│    - NO conversion to Decimal (kept as string)              │
│    - Creates CascoOfferRecord with string value             │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. PERSISTENCE (app/casco/persistence.py)                  │
│    - CascoOfferRecord.insured_amount: Optional[str]         │
│    - Stores TEXT value in database column                   │
│    - Database: insured_amount = "Tirgus vērtība"            │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. COMPARATOR (app/casco/comparator.py)                    │
│    - Reads insured_amount from database (TEXT)              │
│    - Displays in comparison matrix as text field            │
│    - Frontend shows: "Tirgus vērtība"                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Database Schema

**Table:** `public.offers_casco`  
**Column:** `insured_amount`  
**Type:** `TEXT` (not NUMERIC)  
**Value:** Always `"Tirgus vērtība"`

---

## Verification

✅ All linting passes  
✅ No type errors (changed from `Decimal` to `str`)  
✅ Extractor override confirmed  
✅ Routes no longer convert to numeric  
✅ Persistence stores as TEXT  
✅ Comparator displays as text field  
✅ Database column stores text value  

---

## Testing

### Expected Behavior

1. **Upload any CASCO PDF** → `insured_amount = "Tirgus vērtība"`
2. **Check database** → `SELECT insured_amount FROM offers_casco` → Returns `"Tirgus vērtība"`
3. **Compare offers** → Comparison matrix shows `"Tirgus vērtība"` for all insurers
4. **API response** → `{ "insured_amount": "Tirgus vērtība" }`

### What Changed

**Before:**
- Extractor tried to extract numeric value from PDF
- Routes converted to `Decimal`
- Database stored numeric value (e.g., `15000`, `20000`)
- Comparison displayed as number

**After:**
- Extractor always returns `"Tirgus vērtība"`
- Routes keep it as string (no conversion)
- Database stores text `"Tirgus vērtība"`
- Comparison displays as text field

---

## Impact

✅ **CASCO Only** - Health insurance logic unchanged  
✅ **All Endpoints** - Single upload, batch upload, comparison  
✅ **All Layers** - Extractor, routes, persistence, comparator  
✅ **Database Compatible** - TEXT column accepts string value  
✅ **Frontend Compatible** - Receives consistent text value  

---

## Notes

- The GPT extraction prompt still includes instructions for extracting `insured_amount`, but the Python code **overrides** the result
- This ensures consistent behavior even if GPT output varies
- The value `"Tirgus vērtība"` means "Market value" in Latvian
- This standardizes the display across all CASCO offers

---

**Implementation Date:** 2024-11-20  
**Status:** ✅ Complete and Verified

