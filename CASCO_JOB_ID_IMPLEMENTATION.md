# CASCO Job ID System Implementation

## Overview

Complete refactoring of CASCO backend to use an internal job ID system (`casco_job_id`) instead of dependency on `inquiry_id`. This aligns CASCO with the HEALTH architecture where each upload creates a unique job.

## Database Changes

### 1. New Table: `casco_jobs`

```sql
CREATE TABLE IF NOT EXISTS public.casco_jobs (
    id SERIAL PRIMARY KEY,
    reg_number TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_casco_jobs_reg_number 
    ON public.casco_jobs(reg_number);

CREATE INDEX IF NOT EXISTS idx_casco_jobs_created_at 
    ON public.casco_jobs(created_at DESC);
```

**Purpose**: Track CASCO upload jobs. Each upload (single or batch) creates a new job entry.

### 2. Modified Table: `offers_casco`

```sql
-- Add casco_job_id column
ALTER TABLE public.offers_casco 
    ADD COLUMN IF NOT EXISTS casco_job_id INTEGER;

-- Create foreign key
ALTER TABLE public.offers_casco 
    ADD CONSTRAINT offers_casco_casco_job_id_fkey 
    FOREIGN KEY (casco_job_id) 
    REFERENCES public.casco_jobs(id) 
    ON DELETE CASCADE;

-- Index for performance
CREATE INDEX IF NOT EXISTS idx_offers_casco_casco_job_id 
    ON public.offers_casco(casco_job_id);
```

**Changes**:
- Added `casco_job_id` column (required)
- `inquiry_id` is no longer used for CASCO offers
- All offers from one upload batch share the same `casco_job_id`

## Backend Changes

### 1. Persistence Layer (`app/casco/persistence.py`)

#### Updated `CascoOfferRecord` Dataclass

```python
@dataclass
class CascoOfferRecord:
    insurer_name: str
    reg_number: str
    casco_job_id: int  # Required - links to casco_jobs.id
    insured_entity: Optional[str] = None
    insured_amount: Optional[Decimal] = None
    currency: str = "EUR"
    territory: Optional[str] = None
    period: Optional[str] = None
    premium_total: Optional[Decimal] = None
    premium_breakdown: Optional[Dict[str, Any]] = None
    coverage: CascoCoverage | Dict[str, Any] = None
    raw_text: Optional[str] = None
    product_line: str = "casco"
```

**Key Changes**:
- `casco_job_id` is now required (not Optional)
- `inquiry_id` removed

#### New Function: `create_casco_job()`

```python
async def create_casco_job(conn, reg_number: str) -> int:
    """
    Create a new CASCO job entry.
    Returns the new job ID.
    """
```

**Purpose**: Creates a new job entry at the start of each upload.

#### Updated Function: `save_casco_offers()`

```python
async def save_casco_offers(conn, offers: Sequence[CascoOfferRecord]) -> List[int]:
    """
    Insert CASCO offers with casco_job_id.
    All offers MUST have the same casco_job_id (from the same upload batch).
    """
```

**Changes**:
- Inserts `casco_job_id` instead of `inquiry_id`
- Validates that all offers in batch have valid `casco_job_id`

#### New Function: `fetch_casco_offers_by_job()`

```python
async def fetch_casco_offers_by_job(conn, casco_job_id: int) -> List[Dict[str, Any]]:
    """
    Fetch all CASCO offers for a given job ID.
    Filters by product_line='casco'.
    """
```

**Purpose**: Replaces `fetch_casco_offers_by_inquiry()`.

#### Deprecated Function: `fetch_casco_offers_by_reg_number()`

- Marked as deprecated
- Kept for backwards compatibility only
- Should NOT be used in new code

### 2. Routes Layer (`app/routes/casco_routes.py`)

#### New Helper: `_create_casco_job_sync()`

```python
def _create_casco_job_sync(conn, reg_number: str) -> int:
    """
    Create a new CASCO job entry (synchronous version).
    Returns the new job ID.
    """
```

#### Updated: `_save_casco_offer_sync()`

- Now requires `casco_job_id` in offer
- Inserts `casco_job_id` instead of `inquiry_id`

#### New Helper: `_fetch_casco_offers_by_job_sync()`

```python
def _fetch_casco_offers_by_job_sync(conn, casco_job_id: int) -> List[dict]:
    """
    Fetch all CASCO offers for a job (synchronous version).
    """
```

### 3. Upload Endpoints

#### `POST /casco/upload`

**Before**:
```python
async def upload_casco_offer(
    file: UploadFile,
    insurer_name: str = Form(...),
    reg_number: str = Form(...),
    inquiry_id: Optional[int] = Form(None),  # ❌ OLD
    conn = Depends(get_db),
):
```

**After**:
```python
async def upload_casco_offer(
    file: UploadFile,
    insurer_name: str = Form(...),
    reg_number: str = Form(...),  # ✅ NO inquiry_id
    conn = Depends(get_db),
):
    # Create job
    casco_job_id = _create_casco_job_sync(conn, reg_number)
    
    # ... process PDF ...
    
    # Save with job ID
    offer_record = CascoOfferRecord(
        ...
        casco_job_id=casco_job_id,
        ...
    )
```

**Response**:
```json
{
  "success": true,
  "casco_job_id": 123,
  "offer_ids": [456, 457],
  "message": "Successfully processed 2 CASCO offer(s)"
}
```

**Key Changes**:
1. Removed `inquiry_id` parameter
2. Creates new `casco_job` at start
3. Returns `casco_job_id` instead of `inquiry_id`

#### `POST /casco/upload/batch`

**Before**:
```python
async def upload_casco_offers_batch(
    request: Request,
    reg_number: str = Form(...),
    inquiry_id: Optional[int] = Form(None),  # ❌ OLD
    conn = Depends(get_db),
):
```

**After**:
```python
async def upload_casco_offers_batch(
    request: Request,
    reg_number: str = Form(...),  # ✅ NO inquiry_id
    conn = Depends(get_db),
):
    # Create ONE job for entire batch
    casco_job_id = _create_casco_job_sync(conn, reg_number)
    
    # All offers in batch share this job ID
    for file, insurer in zip(files_list, insurers_list):
        # ... process ...
        offer_record = CascoOfferRecord(
            ...
            casco_job_id=casco_job_id,
            ...
        )
```

**Response**:
```json
{
  "success": true,
  "casco_job_id": 123,
  "offer_ids": [456, 457, 458],
  "total_offers": 3
}
```

**Key Changes**:
1. Removed `inquiry_id` parameter
2. Creates ONE job per batch (all offers share same `casco_job_id`)
3. Returns `casco_job_id`

### 4. Comparison Endpoints

#### New: `GET /casco/job/{casco_job_id}/compare`

**Replaces**: `GET /casco/inquiry/{inquiry_id}/compare`

```python
@router.get("/job/{casco_job_id}/compare")
async def casco_compare_by_job(
    casco_job_id: int,
    conn = Depends(get_db),
):
    """
    Get CASCO comparison matrix for all offers in a job.
    """
    raw_offers = _fetch_casco_offers_by_job_sync(conn, casco_job_id)
    comparison = build_casco_comparison_matrix(raw_offers)
    
    return {
        "offers": raw_offers,
        "comparison": comparison,
        "offer_count": len(raw_offers)
    }
```

**Usage**:
```
GET /casco/job/123/compare
```

**Response**: 22-row comparison matrix with all offers from job 123.

#### New: `GET /casco/job/{casco_job_id}/offers`

**Replaces**: `GET /casco/inquiry/{inquiry_id}/offers`

```python
@router.get("/job/{casco_job_id}/offers")
async def casco_offers_by_job(
    casco_job_id: int,
    conn = Depends(get_db),
):
    """
    Get raw CASCO offers for a job without comparison matrix.
    """
    offers = _fetch_casco_offers_by_job_sync(conn, casco_job_id)
    return {"offers": offers, "count": len(offers)}
```

#### Deprecated: Vehicle Endpoints

- `GET /casco/vehicle/{reg_number}/compare` - Marked `deprecated=True`
- `GET /casco/vehicle/{reg_number}/offers` - Marked `deprecated=True`

**Reason**: These endpoints fetch offers across multiple jobs, which is not the intended behavior. Frontend should use job-based comparison.

### 5. Share Links (`app/main.py`)

#### Updated `ShareCreateBody`

```python
class ShareCreateBody(BaseModel):
    # ... existing fields ...
    product_line: Optional[str] = Field(None, description="Product line: 'casco' or 'health' (default)")
    casco_job_id: Optional[int] = Field(None, description="CASCO job ID for CASCO shares")
```

#### Updated `POST /shares`

```python
payload = {
    # ... existing fields ...
    "product_line": body.product_line or "health",
    "casco_job_id": body.casco_job_id,
}

row = {
    # ... existing fields ...
    "product_line": body.product_line or "health",
}
```

**Usage**:
```json
POST /shares
{
  "title": "CASCO Comparison for LX1234",
  "product_line": "casco",
  "casco_job_id": 123,
  "expires_in_hours": 720
}
```

#### Updated `GET /shares/{token}`

```python
product_line = payload.get("product_line") or share.get("product_line") or "health"

if product_line == "casco":
    casco_job_id = payload.get("casco_job_id")
    if casco_job_id:
        # Fetch CASCO offers using job ID
        raw_offers = _fetch_casco_offers_by_job_sync(conn, casco_job_id)
        comparison = build_casco_comparison_matrix(raw_offers)
        
        return {
            "token": token,
            "payload": payload,
            "offers": raw_offers,
            "comparison": comparison,
            "offer_count": len(raw_offers),
            "product_line": "casco",
            "stats": updated_stats,
        }
# ... existing HEALTH logic ...
```

**Behavior**:
- If `product_line == "casco"`: Fetch from `casco_jobs` using `casco_job_id`
- If `product_line == "health"`: Use existing document-based logic
- Backwards compatible: NULL `product_line` defaults to "health"

## Migration Steps

### 1. Run Database Migration

```bash
psql $DATABASE_URL < backend/scripts/create_casco_jobs_table.sql
```

This will:
- Create `casco_jobs` table
- Add `casco_job_id` to `offers_casco`
- Create necessary indexes and foreign keys

### 2. Update Frontend

#### Upload Flow

**Before**:
```typescript
// OLD: Send inquiry_id
const formData = new FormData();
formData.append('file', file);
formData.append('insurer_name', 'BALTA');
formData.append('reg_number', 'LX1234');
formData.append('inquiry_id', '42');  // ❌ Remove this
```

**After**:
```typescript
// NEW: No inquiry_id needed
const formData = new FormData();
formData.append('file', file);
formData.append('insurer_name', 'BALTA');
formData.append('reg_number', 'LX1234');  // ✅ Only this

const response = await fetch('/casco/upload', { method: 'POST', body: formData });
const { casco_job_id } = await response.json();  // Use this for comparison
```

#### Comparison Flow

**Before**:
```typescript
// OLD: Use inquiry_id
const response = await fetch(`/casco/inquiry/${inquiry_id}/compare`);
```

**After**:
```typescript
// NEW: Use casco_job_id from upload response
const response = await fetch(`/casco/job/${casco_job_id}/compare`);
```

#### Share Creation

**Before**:
```typescript
// OLD: Generic share
await fetch('/shares', {
  method: 'POST',
  body: JSON.stringify({
    title: 'My Share',
    document_ids: [...]
  })
});
```

**After**:
```typescript
// NEW: CASCO share with job ID
await fetch('/shares', {
  method: 'POST',
  body: JSON.stringify({
    title: 'CASCO Comparison for LX1234',
    product_line: 'casco',
    casco_job_id: 123,  // From upload response
    expires_in_hours: 720
  })
});
```

### 3. Backwards Compatibility

#### Existing Data

- Existing `offers_casco` rows with `inquiry_id` but no `casco_job_id` will remain in DB
- They will NOT be returned by new job-based queries
- If you need to migrate old data:

```sql
-- Option 1: Create synthetic jobs for old offers grouped by inquiry_id
INSERT INTO public.casco_jobs (reg_number, created_at)
SELECT DISTINCT reg_number, MIN(created_at) as created_at
FROM public.offers_casco
WHERE casco_job_id IS NULL AND inquiry_id IS NOT NULL
GROUP BY reg_number, inquiry_id;

-- Option 2: Create individual jobs for each old offer
-- (only if grouping is not possible)
```

#### Vehicle Endpoints

- Marked as `deprecated=True`
- Still functional but should not be used
- Will not be removed immediately for backwards compatibility

## Testing Checklist

- [ ] Database migration runs successfully
- [ ] `POST /casco/upload` creates `casco_job` and returns `casco_job_id`
- [ ] `POST /casco/upload/batch` creates ONE job for entire batch
- [ ] All offers in batch have same `casco_job_id`
- [ ] `GET /casco/job/{job_id}/compare` returns correct 22-row matrix
- [ ] `GET /casco/job/{job_id}/offers` returns raw offers
- [ ] Share creation with `product_line="casco"` stores `casco_job_id`
- [ ] Share retrieval with `product_line="casco"` fetches CASCO data
- [ ] HEALTH endpoints remain untouched and functional
- [ ] Deprecated vehicle endpoints still work but show deprecation warning

## Summary

✅ **CASCO now uses internal job system**
- Each upload creates a unique job
- No dependency on `inquiry_id`
- Consistent with HEALTH architecture

✅ **All endpoints updated**
- Upload: Returns `casco_job_id`
- Compare: Uses `/job/{job_id}/compare`
- Offers: Uses `/job/{job_id}/offers`
- Vehicle endpoints: Deprecated

✅ **Share links support CASCO**
- Store `product_line` and `casco_job_id`
- Fetch CASCO data when `product_line="casco"`
- Backwards compatible with HEALTH shares

✅ **HEALTH system untouched**
- No changes to HEALTH logic
- No breaking changes to existing functionality

## Files Changed

1. `backend/scripts/create_casco_jobs_table.sql` - Database migration
2. `app/casco/persistence.py` - Persistence layer with job functions
3. `app/routes/casco_routes.py` - Upload and comparison endpoints
4. `app/main.py` - Share link support for CASCO
5. `CASCO_JOB_ID_IMPLEMENTATION.md` - This documentation

