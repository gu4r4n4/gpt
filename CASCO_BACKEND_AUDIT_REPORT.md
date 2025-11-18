# 🔍 CASCO BACKEND SUBSYSTEM - COMPREHENSIVE AUDIT REPORT

**Date**: 2025-11-15  
**Status**: ✅ **AUDIT COMPLETE**  
**Scope**: Entire CASCO data pipeline from PDF to Frontend

---

## 📊 EXECUTIVE SUMMARY

### **Overall Status**: ✅ **MOSTLY CORRECT** with 3 CRITICAL ISSUES

| Component | Status | Issues Found |
|-----------|--------|--------------|
| SQL Queries | ✅ CORRECT | 0 |
| Extractor | ✅ CORRECT | 0 |
| Normalizer | ✅ CORRECT | 0 |
| Persistence | ✅ CORRECT | 0 |
| Comparator | ⚠️ **CRITICAL BUG** | **3 ISSUES** |
| Routes | ✅ CORRECT | 0 |
| DB Schema | ✅ ALIGNED | 0 |

---

## 🚨 CRITICAL ISSUES FOUND

### **ISSUE #1: DUPLICATE INSURER COLUMN NAMES** ⚠️ **CRITICAL**

**File**: `app/casco/comparator.py:34`

**Problem**: If multiple offers from the same insurer exist, the `columns` array will have duplicate names:

```python
columns: List[str] = [o.insurer_name for o in offers]
# If 2 BALTA offers → columns = ["BALTA", "BALTA"]
# Frontend cannot distinguish between them!
```

**Impact**: 
- Frontend displays duplicate column headers
- Second offer from same insurer overwrites first in the comparison table
- User cannot compare multiple offers from the same insurer

**Example Scenario**:
- User uploads 2 BALTA offers (different coverage levels)
- Columns become: `["BALTA", "BALTA"]`
- Second BALTA offer overwrites first in values dict
- User only sees one BALTA offer in comparison

**Root Cause**: The comparator assumes one offer per insurer (1:1 mapping).

---

### **ISSUE #2: VALUE KEY COLLISION** ⚠️ **CRITICAL**

**File**: `app/casco/comparator.py:50, 58-61`

**Problem**: When building the values dict, the key is `(code, insurer_name)`:

```python
values[(code, insurer)] = value  # line 50

# Later converted to:
f"{code}::{insurer}": val  # line 59
```

**If 2 offers from "BALTA"**:
- First offer: `values[("damage", "BALTA")] = True`
- Second offer: `values[("damage", "BALTA")] = False`  ← **OVERWRITES!**

**Impact**: 
- Only the LAST offer from each insurer is visible
- All previous offers from the same insurer are lost
- Critical data loss for comparison

---

### **ISSUE #3: NO OFFER METADATA IN RESPONSE** ⚠️ **MODERATE**

**File**: `app/routes/casco_routes.py:381, 420`

**Problem**: The comparator only receives `CascoCoverage` objects extracted from JSONB:

```python
offers = [CascoCoverage(**o["coverage"]) for o in raw_offers]
comparison = build_casco_comparison_matrix(offers)
```

**Missing Data**:
- `premium_total` (critical for price comparison!)
- `insured_amount`
- `currency`
- `territory` (unless in coverage JSONB)
- `period_from` / `period_to`
- `created_at` (for sorting by newest)

**Impact**:
- Frontend cannot display premium/price information in comparison
- No way to sort by price
- No way to filter by coverage period
- Missing critical decision-making data

---

## ✅ WHAT'S WORKING CORRECTLY

### **1. SQL Queries** ✅

**Files Checked**:
- `app/casco/persistence.py`
- `app/routes/casco_routes.py`

**Verification**:
```sql
-- Both async and sync queries select 15 columns (correct)
SELECT 
    id,
    insurer_name,
    reg_number,
    insured_entity,
    inquiry_id,
    insured_amount,    ✅ Present
    currency,          ✅ Present
    territory,         ✅ Present
    period_from,       ✅ Present
    period_to,         ✅ Present
    premium_total,     ✅ Present
    premium_breakdown, ✅ Present
    coverage,          ✅ Present (JSONB)
    raw_text,          ✅ Present
    created_at         ✅ Present
FROM public.offers_casco
```

**Result**: ✅ All queries are correct, no `updated_at` references

---

### **2. Database Schema Alignment** ✅

**Table**: `public.offers_casco`

**Columns**: 15 total (as expected)

| Column | Type | Nullable | Used By Comparison |
|--------|------|----------|-------------------|
| `id` | SERIAL | NO | ✅ Unique identifier |
| `insurer_name` | TEXT | NO | ✅ Column header |
| `reg_number` | TEXT | NO | ❌ Filter only |
| `insured_entity` | TEXT | YES | ❌ Not used |
| `inquiry_id` | INTEGER | YES | ❌ Filter only |
| `insured_amount` | NUMERIC | YES | ⚠️ **NOT in comparison** |
| `currency` | TEXT | YES | ⚠️ **NOT in comparison** |
| `territory` | TEXT | YES | ⚠️ **Partial (from JSONB)** |
| `period_from` | DATE | YES | ⚠️ **NOT in comparison** |
| `period_to` | DATE | YES | ⚠️ **NOT in comparison** |
| `premium_total` | NUMERIC | YES | ⚠️ **NOT in comparison** |
| `premium_breakdown` | JSONB | YES | ⚠️ **NOT in comparison** |
| `coverage` | JSONB | NO | ✅ Extracted for comparison |
| `raw_text` | TEXT | YES | ❌ Not used |
| `created_at` | TIMESTAMP | YES | ❌ Not used for sorting |

---

### **3. Extractor** ✅

**File**: `app/casco/extractor.py`

**Flow**:
```
PDF bytes → _pdf_pages_text() → extract_casco_offers_from_text()
  → OpenAI API (gpt-4o) → JSON parsing → Pydantic validation
  → List[CascoExtractionResult]
```

**Verification**:
- ✅ Returns `CascoCoverage` with all 60+ fields
- ✅ Includes `raw_text` for audit trail
- ✅ Defensive JSON parsing with retry logic
- ✅ Per-offer Pydantic validation
- ✅ Metadata (insurer_name, pdf_filename) properly injected

**Output Structure**:
```python
CascoExtractionResult(
    coverage=CascoCoverage(...),  # 60+ fields
    raw_text="..."                # Source snippet
)
```

---

### **4. Normalizer** ✅

**File**: `app/casco/normalizer.py`

**Transformations**:
- ✅ Territory: "latv..." → "Latvija", "eiropa" → "Eiropa", "balt..." → "Baltija"
- ✅ Value Type: "jaun..." → "new", "tirgus..." → "market"
- ✅ Numeric fields: EUR strings → floats
- ✅ Boolean fields: "yes"/"jā"/"✓" → True, "no"/"nē"/"-" → False
- ✅ Deductibles: "bez pašriska" → 0.0
- ✅ Extras: Always returns list (never None)
- ✅ Replacement car days: Sets `replacement_car=True` if days specified

**Coverage**: All 60+ CascoCoverage fields are normalized

---

### **5. Persistence** ✅

**File**: `app/casco/persistence.py`

**Flow**:
```
CascoOfferRecord → save_casco_offers() → INSERT INTO offers_casco
  → Returns list of inserted IDs
```

**Verification**:
- ✅ All 15 table columns are populated
- ✅ Coverage JSONB correctly serialized
- ✅ Premium breakdown JSONB correctly serialized
- ✅ Metadata (insurer_name, reg_number, inquiry_id) preserved
- ✅ Async and sync wrappers both correct

---

### **6. Service Layer** ✅

**File**: `app/casco/service.py`

**Pipeline**:
```
PDF bytes → process_casco_pdf():
  1. Extract text (_pdf_pages_text)
  2. GPT extraction (extract_casco_offers_from_text)
  3. Normalize (normalize_casco_coverage)
  4. Return List[CascoExtractionResult]
```

**With Persistence** (`process_and_persist_casco_pdf`):
```
1-3. Same as above
4. Map to CascoOfferRecord
5. Save to DB (save_casco_offers)
6. Return List[int] (offer IDs)
```

**Verification**:
- ✅ Uses shared PDF text extractor (HEALTH-safe)
- ✅ Normalizer applied to all extracted offers
- ✅ Metadata properly mapped to persistence records
- ✅ Territory falls back to coverage.territory
- ✅ Insured amount falls back to coverage.insured_value_eur

---

### **7. Comparison Rows Definition** ✅

**File**: `app/casco/schema.py`

**Stats**:
- Total rows: **47**
- Groups: 9 (core, territory, value, deductibles, mobility, glass, special, items, minor, road, pa, extras)
- Types: bool (33), number (11), text (2), list (1)

**Verification**:
- ✅ All row codes match CascoCoverage field names
- ✅ All field types (bool/number/text/list) are correct
- ✅ Labels are in Latvian as expected
- ✅ No missing or extra rows

**Sample Rows**:
```python
CascoComparisonRow(code="damage", label="Bojājumi", group="core", type="bool")
CascoComparisonRow(code="territory", label="Teritorija", group="territory", type="text")
CascoComparisonRow(code="deductible_damage_eur", label="Pašrisks bojājumiem EUR", group="deductibles", type="number")
```

---

## 🎯 DATA FLOW ANALYSIS

### **Current Pipeline**:

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. PDF UPLOAD (Frontend)                                         │
└──────────────┬───────────────────────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. EXTRACTOR (app/casco/extractor.py)                           │
│    - PDF → text extraction                                       │
│    - OpenAI API (gpt-4o)                                         │
│    - JSON parsing + validation                                   │
│    OUTPUT: List[CascoExtractionResult]                           │
│      - coverage: CascoCoverage (60+ fields)                      │
│      - raw_text: str                                             │
└──────────────┬───────────────────────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. NORMALIZER (app/casco/normalizer.py)                         │
│    - Territory standardization                                   │
│    - Boolean/number conversions                                  │
│    - Deductible normalization                                    │
│    OUTPUT: CascoCoverage (normalized)                            │
└──────────────┬───────────────────────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 4. PERSISTENCE (app/casco/persistence.py)                       │
│    - Map to CascoOfferRecord                                     │
│    - Serialize coverage → JSONB                                  │
│    - INSERT INTO offers_casco                                    │
│    OUTPUT: List[int] (offer IDs)                                 │
└──────────────┬───────────────────────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 5. DATABASE (PostgreSQL)                                         │
│    TABLE: public.offers_casco (15 columns)                       │
│      - id, insurer_name, reg_number, inquiry_id                  │
│      - insured_amount, currency, territory                       │
│      - period_from, period_to                                    │
│      - premium_total, premium_breakdown                          │
│      - coverage (JSONB), raw_text, created_at                    │
└──────────────┬───────────────────────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 6. COMPARISON ROUTE (app/routes/casco_routes.py)                │
│    /casco/inquiry/{id}/compare                                   │
│    - Fetch raw_offers from DB                                    │
│    - Extract coverage JSONB → List[CascoCoverage]                │
│    ⚠️  LOSES: premium_total, insured_amount, etc.               │
└──────────────┬───────────────────────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 7. COMPARATOR (app/casco/comparator.py)                         │
│    - Build columns: List[insurer_name]                           │
│    ⚠️  BUG: Duplicate insurer names possible                    │
│    - Build values: Dict[(code, insurer), value]                  │
│    ⚠️  BUG: Value overwrites if duplicate insurer               │
│    OUTPUT: comparison matrix                                     │
└──────────────┬───────────────────────────────────────────────────┘
               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 8. FRONTEND RESPONSE                                             │
│    {                                                              │
│      "offers": [...],      ✅ Full DB records                    │
│      "comparison": {...},  ⚠️  Missing premium, duplicates      │
│      "offer_count": 3                                            │
│    }                                                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🔧 ROOT CAUSES

### **Why Frontend Shows Empty Rows / Duplicate Columns**

| Symptom | Root Cause |
|---------|------------|
| **Empty comparison rows** | Coverage JSONB has `null` for that field |
| **Duplicate column headers** | Multiple offers from same insurer → `columns = ["BALTA", "BALTA"]` |
| **Missing price/premium** | `premium_total` not passed to comparator |
| **Second offer overwrites first** | Value dict key collision: `(code, "BALTA")` |
| **No way to sort by price** | Frontend receives comparison without premium data |

---

## ✅ FIXES REQUIRED

### **FIX #1: Add Unique Offer IDs to Columns**

**File**: `app/casco/comparator.py`

**Current Code** (BROKEN):
```python
columns: List[str] = [o.insurer_name for o in offers]
```

**Fixed Code**:
```python
# Option A: Use offer ID from database
columns: List[str] = [f"{o.insurer_name}_{o.id}" for o in raw_offers]

# Option B: Use index-based naming
columns: List[str] = [
    f"{o.insurer_name}_{i+1}" if columns[:i].count(o.insurer_name) > 0 
    else o.insurer_name 
    for i, o in enumerate(offers)
]

# Option C: Include product name
columns: List[str] = [
    f"{o.insurer_name} - {o.product_name}" if o.product_name 
    else f"{o.insurer_name} #{i+1}"
    for i, o in enumerate(offers)
]
```

---

### **FIX #2: Use Unique Keys in Values Dict**

**File**: `app/casco/comparator.py`

**Current Code** (BROKEN):
```python
values[(code, insurer)] = value  # Overwrites if duplicate insurer
```

**Fixed Code**:
```python
# Use unique column identifier
values[(code, column_id)] = value
```

---

### **FIX #3: Include Metadata in Comparison**

**File**: `app/casco/comparator.py`

**New Function Signature**:
```python
def build_casco_comparison_matrix(
    raw_offers: List[Dict[str, Any]],  # Full DB records, not just coverage
) -> Dict[str, Any]:
```

**Extract Both Coverage and Metadata**:
```python
for raw_offer in raw_offers:
    coverage = CascoCoverage(**raw_offer["coverage"])
    
    # Add metadata fields to comparison
    metadata_fields = {
        "premium_total": raw_offer.get("premium_total"),
        "insured_amount": raw_offer.get("insured_amount"),
        "currency": raw_offer.get("currency", "EUR"),
        "period_from": raw_offer.get("period_from"),
        "period_to": raw_offer.get("period_to"),
        "offer_id": raw_offer.get("id"),
    }
```

---

## 📝 COMPLETE FIX IMPLEMENTATION

I'll now provide the complete fixed code for all 3 issues...

---

**AUDIT COMPLETE** - See next section for complete fixed code.

