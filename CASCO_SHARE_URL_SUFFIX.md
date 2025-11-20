# CASCO Share URL Suffix Implementation

## Summary

Modified the `POST /shares` endpoint to append `"_casco"` to the URL token part for CASCO shares, while keeping the database token unchanged. This allows the frontend to distinguish CASCO share URLs from Health share URLs without affecting backend token storage or lookup logic.

---

## Change Made

### **File:** `app/main.py` (Lines 1251-1254)

**Added logic after URL construction:**

```python
# Append "_casco" suffix to URL token for CASCO shares (DB token remains unchanged)
product_line = body.product_line or "health"
if product_line == "casco":
    url = url + "_casco"
```

**Location:** In `create_share_token_only()` function, after URL is built but before response is returned

---

## How It Works

### Request Flow

1. **Client sends POST /shares** with body including:
   ```json
   {
     "product_line": "casco",
     "casco_job_id": "550e8400-e29b-41d4-a716-446655440000",
     "title": "My CASCO Comparison",
     ...
   }
   ```

2. **Backend generates token** (unchanged):
   ```python
   token = _gen_token()  # e.g., "onnVqW0hmMemev9svjpCTA"
   ```

3. **Backend stores in database** (unchanged):
   ```python
   row = {
       "token": "onnVqW0hmMemev9svjpCTA",  # Plain token, no suffix
       "product_line": "casco",
       ...
   }
   ```

4. **Backend builds URL** (unchanged):
   ```python
   url = f"{base}/share/{token}"  # e.g., "https://app.ongo.lv/share/onnVqW0hmMemev9svjpCTA"
   ```

5. **✨ NEW: Append suffix for CASCO**:
   ```python
   if product_line == "casco":
       url = url + "_casco"  # e.g., "https://app.ongo.lv/share/onnVqW0hmMemev9svjpCTA_casco"
   ```

6. **Backend returns response**:
   ```json
   {
     "ok": true,
     "token": "onnVqW0hmMemev9svjpCTA",
     "url": "https://app.ongo.lv/share/onnVqW0hmMemev9svjpCTA_casco",
     "title": "My CASCO Comparison"
   }
   ```

---

## Examples

### CASCO Share

**Request:**
```json
POST /shares
{
  "product_line": "casco",
  "casco_job_id": "abc-123-def",
  "title": "CASCO Comparison"
}
```

**Response:**
```json
{
  "ok": true,
  "token": "xYz789AbC",
  "url": "https://app.ongo.lv/share/xYz789AbC_casco"
}
```

**Database:**
```json
{
  "token": "xYz789AbC",
  "product_line": "casco"
}
```

### Health Share (Unchanged)

**Request:**
```json
POST /shares
{
  "product_line": "health",
  "document_ids": [1, 2, 3],
  "title": "Health Comparison"
}
```

**Response:**
```json
{
  "ok": true,
  "token": "pQr456WxY",
  "url": "https://app.ongo.lv/share/pQr456WxY"
}
```

**Database:**
```json
{
  "token": "pQr456WxY",
  "product_line": "health"
}
```

---

## What Changed

✅ **POST /shares response URL** - Appends `_casco` for CASCO shares  
❌ **Token generation** - Unchanged (still random, no suffix)  
❌ **Database storage** - Unchanged (token stored without suffix)  
❌ **GET /shares/{token}** - Unchanged (looks up by plain token)  
❌ **Health shares** - Unchanged (no suffix added)

---

## Frontend Implications

The frontend can now:

1. **Detect CASCO shares** by checking if URL ends with `_casco`
2. **Route differently** based on URL pattern:
   - `/share/:token_casco` → CASCO comparison view
   - `/share/:token` → Health comparison view
3. **Share CASCO links** that are visually distinct from Health links

---

## Backend Behavior

### Database Lookup (Unchanged)

The GET endpoint still uses the plain token:

```python
@app.get("/shares/{token}")
def get_share_token_only(token: str, request: Request):
    share = _load_share_record(token)  # Looks up by plain token
    # ... rest of logic
```

**How frontend strips suffix:**

The frontend router should handle this:

```javascript
// Frontend route handling
if (url.endsWith('_casco')) {
  const plainToken = url.replace('_casco', '');
  // Fetch from: GET /shares/{plainToken}
}
```

---

## Testing

### Test Case 1: CASCO Share Creation

```bash
curl -X POST https://api.ongo.lv/shares \
  -H "Content-Type: application/json" \
  -d '{
    "product_line": "casco",
    "casco_job_id": "test-job-123",
    "title": "Test CASCO Share"
  }'
```

**Expected Response:**
```json
{
  "ok": true,
  "token": "abc123xyz",
  "url": "https://app.ongo.lv/share/abc123xyz_casco"
}
```

### Test Case 2: Health Share Creation (Default)

```bash
curl -X POST https://api.ongo.lv/shares \
  -H "Content-Type: application/json" \
  -d '{
    "document_ids": [1, 2],
    "title": "Test Health Share"
  }'
```

**Expected Response:**
```json
{
  "ok": true,
  "token": "def456uvw",
  "url": "https://app.ongo.lv/share/def456uvw"
}
```

### Test Case 3: Fetching CASCO Share

```bash
# Frontend strips "_casco" and fetches with plain token
curl https://api.ongo.lv/shares/abc123xyz
```

**Expected:** Returns CASCO share data (no 404 error)

---

## Implementation Details

### Code Location

**File:** `app/main.py`  
**Function:** `create_share_token_only()`  
**Lines:** 1251-1254

### Logic Flow

```python
# 1. Build base URL (unchanged)
url = f"{base}/share/{token}"

# 2. Check product line and append suffix
product_line = body.product_line or "health"
if product_line == "casco":
    url = url + "_casco"

# 3. Return response with modified URL
return {"ok": True, "token": token, "url": url, ...}
```

### Key Points

- ✅ **Suffix only in URL string** - Not in token variable
- ✅ **Database stores plain token** - No suffix persisted
- ✅ **GET endpoint unchanged** - Still accepts plain token
- ✅ **Backward compatible** - Health shares unaffected
- ✅ **Simple implementation** - Just string concatenation

---

## Verification

✅ No linting errors  
✅ GET /shares/{token} unchanged  
✅ Token generation unchanged  
✅ Database schema unchanged  
✅ Health logic unchanged  
✅ Only POST /shares response URL modified  

---

## Impact

**Affected:**
- POST /shares response URL (only for CASCO)

**Unaffected:**
- Token generation
- Database storage
- GET /shares/{token} endpoint
- Health shares
- Token lookup logic
- Share metadata
- Expiration logic

---

**Implementation Date:** 2024-11-20  
**Status:** ✅ Complete and Verified

