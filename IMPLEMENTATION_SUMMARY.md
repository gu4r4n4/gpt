# Chunks Report Endpoint - Implementation Summary

## ✅ What Was Implemented

A new FastAPI endpoint `GET /api/qa/chunks-report` that retrieves document chunks for a given share token.

### Files Modified/Created

1. **Modified**: `backend/api/routes/qa.py`
   - Added new endpoint `GET /api/qa/chunks-report`
   - Added helper functions `_validate_share_token()` and `_check_authorization()`
   - Added Pydantic models `ChunkData` and `ChunksReportResponse`
   - Added `Header` import from FastAPI

2. **Created**: `backend/tests/test_chunks_report.py`
   - Unit tests for share token validation
   - Unit tests for authorization checks
   - Integration tests for the endpoint
   - Manual testing helper function

3. **Created**: `CHUNKS_REPORT_API.md`
   - Complete API documentation
   - Request/response examples in cURL, Python, and TypeScript
   - Database schema requirements
   - Troubleshooting guide

4. **Created**: `backend/scripts/create_offer_chunks_table.sql`
   - SQL migration to create `offer_chunks` table
   - Indexes for performance
   - Triggers for timestamp management

## 📋 Feature Checklist

All requirements from the user request have been implemented:

- ✅ New endpoint: `GET /api/qa/chunks-report`
- ✅ Accepts `share_token` as query parameter
- ✅ Validates `share_token` via `share_links` table
- ✅ Retrieves `batch_token`, `org_id`, `document_ids` from share payload
- ✅ Queries `offer_chunks` for matching documents
- ✅ Returns JSON with fields: `chunk_index`, `text` (~200 chars), `metadata`, `created_at`
- ✅ Pagination/limit support (default 100, max 500)
- ✅ Logging with tags `[qa] chunks-report start` and `[qa] chunks-report done`
- ✅ Protected endpoint: only admin role or same org can access
- ✅ FastAPI typing with Pydantic models
- ✅ Comprehensive error handling: missing token, expired token, no matching batch, etc.

## 🔐 Security & Authorization

The endpoint implements two-tier authorization:

1. **Admin Role**: Users with `X-User-Role: admin` header can access any share
2. **Same Organization**: Users with matching `X-Org-Id` can access shares from their org

Authorization is checked via the `_check_authorization()` function, which raises a 403 error if neither condition is met.

## 📊 Database Schema

The endpoint expects an `offer_chunks` table with this structure:

```sql
CREATE TABLE public.offer_chunks (
    id SERIAL PRIMARY KEY,
    file_id INTEGER REFERENCES public.offer_files(id),
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);
```

**If the table doesn't exist**, run:
```bash
psql $DATABASE_URL -f backend/scripts/create_offer_chunks_table.sql
```

## 🔄 API Flow

```
1. Client sends GET /api/qa/chunks-report?share_token=xyz
   ├─ Headers: X-Org-Id, X-User-Role (optional)
   
2. Validate share_token
   ├─ Query share_links table
   ├─ Check expiration
   └─ Extract: batch_token, org_id, document_ids
   
3. Check authorization
   ├─ Admin role? → Allow
   ├─ Same org_id? → Allow
   └─ Otherwise → 403 Forbidden
   
4. Query database
   ├─ Get file_ids from offer_files (via batch_token or document_ids)
   ├─ Count total chunks
   └─ Fetch paginated chunks with LEFT(text, 200)
   
5. Return response
   └─ JSON with chunks array and metadata
```

## 📝 Example Usage

### Basic Request
```bash
curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=abc123xyz" \
  -H "X-Org-Id: 1" \
  -H "X-User-Role: admin"
```

### Python
```python
import requests

response = requests.get(
    "http://localhost:8000/api/qa/chunks-report",
    params={"share_token": "abc123xyz", "limit": 50},
    headers={"X-Org-Id": "1", "X-User-Role": "admin"}
)

data = response.json()
print(f"Found {data['total_chunks']} chunks")
for chunk in data['chunks']:
    print(f"Chunk {chunk['chunk_index']}: {chunk['text'][:50]}...")
```

### Sample Response
```json
{
  "ok": true,
  "share_token": "abc123xyz",
  "batch_token": "bt_7f8e9d6c5b4a3f2e1d0c9b8a",
  "org_id": 1,
  "total_chunks": 245,
  "chunks": [
    {
      "chunk_index": 0,
      "text": "This is the first ~200 characters...",
      "metadata": {"page": 1},
      "created_at": "2025-10-25T12:34:56.789Z",
      "file_id": 42,
      "filename": "offer_document.pdf"
    }
  ]
}
```

## 🧪 Testing

### Run Unit Tests
```bash
cd backend
python -m pytest tests/test_chunks_report.py -v
```

### Manual Testing
1. Start the server: `uvicorn app.main:app --reload`
2. Visit Swagger UI: `http://localhost:8000/docs`
3. Find `/api/qa/chunks-report` endpoint
4. Click "Try it out" and enter a valid share_token

### Test with Real Data
```bash
# 1. Create a share (if needed)
curl -X POST "http://localhost:8000/shares" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: 1" \
  -d '{
    "document_ids": ["doc1", "doc2"],
    "batch_token": "bt_123",
    "expires_in_hours": 24
  }'

# 2. Use the returned token to test chunks-report
curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=TOKEN_FROM_STEP_1" \
  -H "X-Org-Id: 1" \
  -H "X-User-Role: admin"
```

## 🚀 Deployment

### Prerequisites
1. Ensure `offer_chunks` table exists (run SQL migration if needed)
2. Environment variables are set: `DATABASE_URL`, etc.
3. Server has access to PostgreSQL database

### Deploy Steps
1. Pull the updated code
2. Restart the FastAPI server:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
3. Verify endpoint is accessible:
   ```bash
   curl http://localhost:8000/healthz
   ```

### Production Considerations
- Add rate limiting to prevent abuse
- Monitor query performance for large chunk sets
- Consider caching for frequently accessed shares
- Set up proper logging/monitoring for `[qa] chunks-report` tags

## 🐛 Error Handling

The endpoint handles all specified error cases:

| Error | Status Code | Example |
|-------|-------------|---------|
| Missing share_token | 400 | `{"detail": "share_token is required"}` |
| Invalid share_token | 404 | `{"detail": "Share token not found"}` |
| Expired share_token | 403 | `{"detail": "Share token expired"}` |
| Unauthorized access | 403 | `{"detail": "Unauthorized: only admin..."}` |
| Database errors | 500 | `{"detail": "Failed to retrieve chunks..."}` |

## 📊 Logging

All operations are logged with the `[qa]` tag:

```
[qa] chunks-report start share_token=abc123xyz
[qa] chunks-report validated share org_id=1 batch_token=bt_123
[qa] chunks-report found 3 files
[qa] chunks-report done total=245 returned=100
```

Or in case of errors:
```
[qa] chunks-report error: share token expired
```

## 📚 Documentation

Complete documentation is available in:
- **API Docs**: `CHUNKS_REPORT_API.md` - Full API reference
- **OpenAPI**: `http://localhost:8000/docs` - Interactive Swagger UI
- **Tests**: `backend/tests/test_chunks_report.py` - Test examples

## ⚠️ Important Notes

1. **Database Table Required**: The `offer_chunks` table must exist. If not, create it using the provided SQL migration.

2. **Chunk Population**: This endpoint only *reads* chunks. You need a separate process to populate `offer_chunks` from your vector stores or document processing pipeline.

3. **Performance**: For large datasets (>10k chunks), consider:
   - Adding database indexes (already included in migration)
   - Implementing caching for popular shares
   - Using connection pooling

4. **Text Truncation**: Chunk text is truncated to ~200 characters using SQL `LEFT(text, 200)` for performance. Full text is not returned.

## 🎯 Next Steps

1. **Test the endpoint** with a real share_token:
   ```bash
   export SHARE_TOKEN="your_actual_token"
   curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=$SHARE_TOKEN" \
     -H "X-Org-Id: 1" \
     -H "X-User-Role: admin"
   ```

2. **Verify database table** exists:
   ```bash
   psql $DATABASE_URL -c "SELECT COUNT(*) FROM public.offer_chunks;"
   ```

3. **Check logs** for `[qa] chunks-report` messages

4. **Run tests**:
   ```bash
   python -m pytest backend/tests/test_chunks_report.py -v
   ```

## ✨ Summary

The chunks-report endpoint is **fully implemented** with:
- ✅ Share token validation
- ✅ Authorization checks (admin or same org)
- ✅ Database queries with pagination
- ✅ Comprehensive error handling
- ✅ Proper logging with tags
- ✅ FastAPI typing
- ✅ Unit tests
- ✅ Documentation

**Ready to deploy and test!** 🚀

