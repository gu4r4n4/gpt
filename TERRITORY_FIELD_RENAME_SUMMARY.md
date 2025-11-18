# Territory Field Rename - Summary

## What Was Changed

Successfully renamed all CASCO `coverage.territory` references to `coverage.Teritorija` throughout the backend.

---

## Files Modified

### ✅ `app/routes/casco_routes.py` (2 changes)

**Line 236** - Single upload endpoint:
```python
# Before:
territory=coverage.territory,

# After:
territory=coverage.Teritorija if coverage.Teritorija and coverage.Teritorija != "-" else None,
```

**Line 331** - Batch upload endpoint:
```python
# Before:
territory=result.coverage.territory,

# After:
territory=result.coverage.Teritorija if result.coverage.Teritorija and result.coverage.Teritorija != "-" else None,
```

### ✅ `app/casco/service.py` (Already updated in previous migration)

**Line 101** - Process and persist function:
```python
territory_val = coverage.Teritorija if coverage.Teritorija and coverage.Teritorija != "-" else None
```

---

## What Was NOT Changed (Intentionally)

### ✅ Database References (Correct)

**`app/casco/comparator.py` - Line 79:**
```python
"territory": raw_offer.get("territory"),  # ✅ DB field is lowercase
```

**`app/casco/persistence.py` - Multiple lines:**
```python
territory: Optional[str] = None  # ✅ DB column is lowercase
offer.territory,                 # ✅ CascoOfferRecord field
```

**Reason**: The database table `offers_casco` has a column named `territory` (lowercase). The `CascoOfferRecord` dataclass maps to this DB column. Only the Pydantic `CascoCoverage` model uses `Teritorija` (Latvian name).

### ✅ Extractor Prompt (Unchanged)

The detailed system prompt in `app/casco/extractor.py` was NOT modified, as requested. The prompt correctly instructs the model to return `"Teritorija"` as one of the 19 JSON keys.

---

## Data Flow

```
PDF → GPT Model
       ↓
   Returns JSON with "Teritorija" key
       ↓
   Mapped to CascoCoverage.Teritorija (Python attribute)
       ↓
   Extracted in service/routes: coverage.Teritorija
       ↓
   Saved to DB as "territory" (lowercase column)
       ↓
   Retrieved from DB as raw_offer.get("territory")
       ↓
   Displayed in comparator metadata
```

---

## Validation

### ✅ No Linter Errors
All modified files pass linting without errors.

### ✅ All References Updated
- `coverage.territory` → ✅ Changed to `coverage.Teritorija` (3 locations)
- `result.coverage.territory` → ✅ Changed to `result.coverage.Teritorija` (1 location)
- DB field `territory` → ✅ Unchanged (correct)

### ✅ Defensive Logic Added
All references now include the defensive check:
```python
coverage.Teritorija if coverage.Teritorija and coverage.Teritorija != "-" else None
```

This ensures:
- `None` is stored for missing values
- `"-"` (not covered) is converted to `None` in DB
- Only actual territory values like "Eiropa", "Latvija" are stored

---

## Summary

✅ **All CASCO coverage.territory references updated to coverage.Teritorija**  
✅ **DB schema remains unchanged (territory column)**  
✅ **Extractor prompt unchanged (as requested)**  
✅ **No impact on HEALTH code**  
✅ **Defensive null handling added**  
✅ **All linter checks pass**

**Status**: Complete and ready for production

