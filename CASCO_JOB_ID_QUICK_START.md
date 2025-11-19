# CASCO Job ID System - Quick Start Guide

## What Changed?

CASCO backend now uses an **internal job ID system** (`casco_job_id`) instead of relying on `inquiry_id`. This makes CASCO consistent with the HEALTH architecture.

## Key Changes

### Backend

1. **New table**: `casco_jobs` - tracks each upload batch
2. **Modified table**: `offers_casco` - now uses `casco_job_id` instead of `inquiry_id`
3. **New endpoints**:
   - `GET /casco/job/{casco_job_id}/compare` (replaces inquiry-based)
   - `GET /casco/job/{casco_job_id}/offers` (replaces inquiry-based)
4. **Updated upload endpoints**: No longer accept `inquiry_id` parameter
5. **Share links**: Support `product_line="casco"` and `casco_job_id`

### Frontend Impact

#### 1. Upload Flow (BREAKING CHANGE)

**Before**:
```javascript
// OLD - with inquiry_id
formData.append('inquiry_id', inquiryId);  // ❌ Remove this

const response = await fetch('/casco/upload', {
  method: 'POST',
  body: formData
});
```

**After**:
```javascript
// NEW - no inquiry_id needed
// DO NOT send inquiry_id

const response = await fetch('/casco/upload', {
  method: 'POST',
  body: formData
});

const { casco_job_id, offer_ids } = await response.json();
// Store casco_job_id for comparison
```

#### 2. Comparison Flow (BREAKING CHANGE)

**Before**:
```javascript
// OLD
const url = `/casco/inquiry/${inquiryId}/compare`;
```

**After**:
```javascript
// NEW - use casco_job_id from upload response
const url = `/casco/job/${cascoJobId}/compare`;
```

#### 3. Share Creation

**Before**:
```javascript
// OLD - generic share
await fetch('/shares', {
  method: 'POST',
  body: JSON.stringify({
    title: 'My Comparison'
  })
});
```

**After**:
```javascript
// NEW - CASCO share with job ID
await fetch('/shares', {
  method: 'POST',
  body: JSON.stringify({
    title: 'CASCO Comparison',
    product_line: 'casco',
    casco_job_id: cascoJobId,  // From upload response
    expires_in_hours: 720
  })
});
```

## Migration Steps

### 1. Database

```bash
psql $DATABASE_URL < backend/scripts/create_casco_jobs_table.sql
```

### 2. Frontend

1. **Remove `inquiry_id` from upload requests**
2. **Store `casco_job_id` from upload response**
3. **Use `/casco/job/{id}/compare` instead of `/casco/inquiry/{id}/compare`**
4. **Pass `product_line` and `casco_job_id` when creating CASCO shares**

## Response Format Changes

### Upload Response

**Before**:
```json
{
  "success": true,
  "inquiry_id": 42,
  "offer_ids": [101, 102]
}
```

**After**:
```json
{
  "success": true,
  "casco_job_id": 123,
  "offer_ids": [101, 102]
}
```

### Comparison Response

Format remains the same, but endpoint path changes:
- Old: `GET /casco/inquiry/{inquiry_id}/compare`
- New: `GET /casco/job/{casco_job_id}/compare`

## Backwards Compatibility

- ✅ HEALTH endpoints: Completely untouched
- ⚠️ Vehicle endpoints: Deprecated (still work but marked as deprecated)
- ❌ Inquiry-based CASCO endpoints: Removed (breaking change)
- ❌ Old `inquiry_id` in upload: No longer accepted (breaking change)

## Testing Checklist

Frontend developers should test:

- [ ] Upload single file → Get `casco_job_id` in response
- [ ] Upload batch → Get `casco_job_id` in response
- [ ] Compare using `/casco/job/{id}/compare` → Get 22-row matrix
- [ ] Create share with `product_line="casco"` → Share stores job ID
- [ ] Open shared link → CASCO comparison loads correctly
- [ ] HEALTH flow still works (no regression)

## Example: Complete Flow

```javascript
// 1. Upload CASCO offers
const formData = new FormData();
formData.append('file', pdfFile);
formData.append('insurer_name', 'BALTA');
formData.append('reg_number', 'LX1234');
// NO inquiry_id

const uploadResponse = await fetch('/casco/upload', {
  method: 'POST',
  body: formData
});

const { casco_job_id } = await uploadResponse.json();

// 2. Get comparison
const compareResponse = await fetch(`/casco/job/${casco_job_id}/compare`);
const { offers, comparison, offer_count } = await compareResponse.json();

// 3. Create share
const shareResponse = await fetch('/shares', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    title: `CASCO for LX1234`,
    product_line: 'casco',
    casco_job_id: casco_job_id,
    expires_in_hours: 720
  })
});

const { token, url } = await shareResponse.json();
console.log('Share URL:', url);
```

## Troubleshooting

### Error: "inquiry_id is required"
**Solution**: Remove `inquiry_id` from your upload request. It's no longer used.

### Error: "404 Not Found" on comparison
**Solution**: Use `/casco/job/{id}/compare` instead of `/casco/inquiry/{id}/compare`

### Old CASCO data not showing
**Solution**: Old offers with `inquiry_id` but no `casco_job_id` won't appear in new job-based queries. They remain in the database but need manual migration if needed.

### HEALTH endpoints broken
**Solution**: This shouldn't happen - HEALTH logic is completely untouched. If you see issues, please report immediately.

## Support

- Full documentation: `CASCO_JOB_ID_IMPLEMENTATION.md`
- Verification script: `python verify_casco_job_id.py`

## Rollback Plan (Emergency)

If critical issues arise:

1. Revert backend files:
   - `app/casco/persistence.py`
   - `app/routes/casco_routes.py`
   - `app/main.py`

2. Frontend can temporarily use deprecated vehicle endpoints:
   - `GET /casco/vehicle/{reg_number}/compare` (deprecated but functional)

3. Database rollback (if needed):
   ```sql
   ALTER TABLE offers_casco DROP COLUMN casco_job_id;
   DROP TABLE casco_jobs;
   ```

**Note**: Only use rollback in emergencies. Forward migration is recommended.

