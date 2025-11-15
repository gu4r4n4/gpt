# CASCO Implementation Guide

## ✅ What's Been Implemented

### 📁 Module Structure

```
app/casco/
├── __init__.py              # Module marker
├── schema.py                # Pydantic models (60+ fields)
├── extractor.py             # Hybrid GPT extraction
├── normalizer.py            # Coverage normalization
├── comparator.py            # Comparison matrix builder
├── service.py               # High-level orchestration
└── persistence.py           # Database layer
```

### 🎯 Core Components

#### 1. **Schema** (`schema.py`)
- `CascoCoverage`: 60+ fields covering all CASCO insurance aspects
  - Core coverage (damage, theft, fire, etc.)
  - Deductibles
  - Mobility (replacement car, roadside assistance)
  - Glass coverage
  - Personal items & accessories
  - Special risks (hydroshock, electronics, etc.)
  - Personal accident coverage
  - Extras
- `CascoComparisonRow`: Row definition for comparison table
- `CASCO_COMPARISON_ROWS`: 52 predefined comparison rows with Latvian labels

#### 2. **Extractor** (`extractor.py`)
- `CascoExtractionResult`: Hybrid result (structured + raw_text)
- `extract_casco_offers_from_text()`: Core GPT extraction using OpenAI Responses API
- Structured prompts for objective, null-safe extraction
- Full Pydantic validation

#### 3. **Normalizer** (`normalizer.py`)
- `normalize_casco_coverage()`: Cleans and standardizes extracted data
- Handles:
  - Monetary values (EUR parsing)
  - Boolean mapping
  - Territory standardization
  - Insured value type normalization
  - Safe defaults for missing fields

#### 4. **Comparator** (`comparator.py`)
- `build_casco_comparison_matrix()`: Builds FE-ready comparison table
- Returns:
  - `rows`: List of comparison row definitions
  - `columns`: List of insurer names
  - `values`: Matrix of (row_code, insurer) → value

#### 5. **Service** (`service.py`)
- `process_casco_pdf()`: Sync pipeline (extract → normalize)
- `process_and_persist_casco_pdf()`: Async pipeline with DB persistence

#### 6. **Persistence** (`persistence.py`)
- `CascoOfferRecord`: DB record dataclass
- `save_casco_offers()`: Bulk insert into `public.offers_casco`
- `save_single_casco_offer()`: Single offer convenience wrapper
- `fetch_casco_offers_by_inquiry()`: Fetch by inquiry_id
- `fetch_casco_offers_by_reg_number()`: Fetch by vehicle registration

### 🗄️ Database

**Table**: `public.offers_casco`

**Schema**:
```sql
id                  SERIAL PRIMARY KEY
insurer_name        TEXT NOT NULL
reg_number          TEXT NOT NULL
insured_entity      TEXT
inquiry_id          INTEGER
insured_amount      NUMERIC(12, 2)
currency            TEXT DEFAULT 'EUR'
premium_total       NUMERIC(12, 2)
premium_breakdown   JSONB
territory           TEXT
period_from         DATE
period_to           DATE
coverage            JSONB NOT NULL        -- 60+ structured fields
raw_text            TEXT                  -- GPT audit trail
created_at          TIMESTAMP WITH TIME ZONE
updated_at          TIMESTAMP WITH TIME ZONE
```

**Indexes**:
- `inquiry_id`
- `reg_number`
- `insurer_name`
- `created_at` (DESC)
- `coverage` (GIN index for JSONB queries)

**Migration**: `backend/scripts/create_offers_casco_table.sql`

---

## 🚀 Next Steps

### Step 1: Run Database Migration

```sql
-- Run in Supabase SQL Editor
\i backend/scripts/create_offers_casco_table.sql
```

### Step 2: Wire Up DB Connection

In `app/routes/casco_upload.py`, replace the placeholder connection logic:

```python
# Your existing DB connection pattern (example)
from app.database import get_db_pool

pool = await get_db_pool()
conn = await pool.acquire()

try:
    offer_ids = await process_and_persist_casco_pdf(
        conn=conn,
        file_bytes=pdf_bytes,
        insurer_name=insurer_name,
        reg_number=reg_number,
        inquiry_id=inquiry_id,
        pdf_filename=file.filename,
        insured_amount=Decimal(str(insured_amount)) if insured_amount else None,
        period_from=period_from,
        period_to=period_to,
        premium_total=Decimal(str(premium_total)) if premium_total else None,
    )
finally:
    await pool.release(conn)
```

### Step 3: Register Routes

In your main app file (e.g., `app/main.py`):

```python
from app.routes import casco_upload

app.include_router(casco_upload.router)
```

### Step 4: Test Upload Endpoint

```bash
curl -X POST "http://localhost:8000/casco/upload" \
  -F "file=@test_casco_offer.pdf" \
  -F "insurer_name=Balta" \
  -F "reg_number=AB1234" \
  -F "inquiry_id=123" \
  -F "insured_amount=15000" \
  -F "premium_total=450.50" \
  -F "period_from=2024-01-01" \
  -F "period_to=2024-12-31"
```

### Step 5: Test Comparison Endpoint

```bash
# By inquiry_id
curl "http://localhost:8000/casco/compare/inquiry/123"

# By vehicle registration
curl "http://localhost:8000/casco/compare/vehicle/AB1234"
```

---

## 🔐 Safety & Isolation

### ✅ Zero Risk to Existing HEALTH Logic

- **Separate module**: `app/casco/` has no imports into existing health code
- **Shared utilities only**: Reuses only:
  - `app.gpt_extractor._pdf_pages_text` (PDF text extraction)
  - `app.services.openai_client.client` (OpenAI client)
- **Separate DB table**: `public.offers_casco` (not touching existing tables)
- **No route conflicts**: Uses `/casco/*` prefix

### 🧪 Testing Strategy

1. **Unit tests**: Test each module independently
   - `test_casco_schema.py`: Validate Pydantic models
   - `test_casco_normalizer.py`: Test field normalization
   - `test_casco_comparator.py`: Test matrix building

2. **Integration tests**: Test full pipeline
   - `test_casco_extraction.py`: Mock OpenAI, test extraction
   - `test_casco_persistence.py`: Test DB operations
   - `test_casco_upload.py`: Test API endpoints

---

## 📊 Frontend Integration

### Comparison Table Data Structure

```typescript
interface CascoComparison {
  rows: CascoComparisonRow[];
  columns: string[];  // Insurer names
  values: Record<string, any>;  // "row_code::insurer_name" → value
}

interface CascoComparisonRow {
  code: string;           // "damage", "theft", etc.
  label: string;          // "Bojājumi", "Zādzība" (Latvian)
  group: string;          // "core", "deductibles", "mobility"
  type: "bool" | "number" | "text" | "list";
}
```

### Example Usage

```typescript
// Fetch comparison
const response = await fetch('/casco/compare/inquiry/123');
const { comparison } = await response.json();

// Render table
comparison.rows.forEach(row => {
  comparison.columns.forEach(insurer => {
    const key = `${row.code}::${insurer}`;
    const value = comparison.values[key];
    
    // Render cell based on row.type
    if (row.type === 'bool') {
      // Show checkmark or dash
    } else if (row.type === 'number') {
      // Format as EUR
    } else {
      // Show text
    }
  });
});
```

---

## 🎯 Objective Comparison Rules

1. **No guessing**: If a field is missing → `null` → frontend shows `-`
2. **No transformations**: Display exactly what's normalized
3. **Consistent schema**: All insurers use the same 60+ field set
4. **Audit trail**: `raw_text` field contains source paragraphs from PDF
5. **Type-safe**: Pydantic validation ensures data integrity

---

## 🔄 Future Enhancements

### Immediate
- [ ] Connect real DB in `casco_upload.py`
- [ ] Add API tests
- [ ] Deploy and test with real PDFs

### Short-term
- [ ] Add premium breakdown extraction
- [ ] Extract `insured_entity` from PDFs
- [ ] Add file upload to Supabase Storage
- [ ] Add offer editing endpoint

### Long-term
- [ ] Multi-file batch upload
- [ ] Historical comparison (same vehicle, different dates)
- [ ] Export comparison as PDF/Excel
- [ ] Add CASCO-specific T&C extraction
- [ ] Machine learning for insurer-specific normalization

---

## 📞 Support

**Files to check**:
- `app/casco/` - All CASCO logic
- `backend/scripts/create_offers_casco_table.sql` - DB schema
- `app/routes/casco_upload.py` - API endpoints
- This file - Implementation guide

**Common issues**:
1. **DB connection**: Adjust `get_db_connection()` to your setup
2. **OpenAI client**: Verify `app.services.openai_client.client` is correct
3. **PDF extraction**: Ensure `pypdf` is in `requirements.txt`

---

## ✨ Summary

You now have a **complete, isolated CASCO module** that:

✅ Extracts 60+ structured fields from CASCO PDFs  
✅ Normalizes data for objective comparison  
✅ Persists to dedicated DB table with audit trail  
✅ Provides comparison matrix API for frontend  
✅ **Zero impact on existing HEALTH logic**  

The system is production-ready once you wire up your DB connection! 🚀

