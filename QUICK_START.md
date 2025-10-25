# Quick Start: Chunks Report Endpoint

## 🚀 Test the Endpoint Now

### 1. Verify Server is Running
```bash
uvicorn app.main:app --reload
# Server should start on http://localhost:8000
```

### 2. Check the Endpoint is Available
```bash
curl http://localhost:8000/docs
# Visit this URL in your browser to see Swagger UI with the new endpoint
```

### 3. Test with Your Share Token
```bash
curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=YOUR_SHARE_TOKEN&limit=10" \
  -H "X-Org-Id: 1" \
  -H "X-User-Role: admin"
```

Replace `YOUR_SHARE_TOKEN` with an actual token from your database.

---

## 📍 Endpoint Details

- **URL**: `/api/qa/chunks-report`
- **Method**: `GET`
- **Location**: `backend/api/routes/qa.py` (lines 472-606)

### Required Parameters
- `share_token` (query param) - The share token to lookup

### Optional Parameters
- `limit` (query param, default: 100, max: 500)
- `offset` (query param, default: 0)
- `X-Org-Id` (header) - Organization ID
- `X-User-Role` (header) - User role (e.g., "admin")

---

## 🔑 Get a Share Token

If you don't have a share token yet, create one:

```bash
curl -X POST "http://localhost:8000/shares" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: 1" \
  -H "X-User-Id: 1" \
  -d '{
    "document_ids": ["your_doc_id_1", "your_doc_id_2"],
    "batch_token": "your_batch_token",
    "expires_in_hours": 24
  }'
```

This returns a token you can use for testing.

---

## 📊 Database Setup

If you get errors about missing `offer_chunks` table:

```bash
# Run the migration script
psql $DATABASE_URL -f backend/scripts/create_offer_chunks_table.sql
```

Or manually create the table - see `backend/scripts/create_offer_chunks_table.sql`

---

## ✅ Expected Response

Success (200):
```json
{
  "ok": true,
  "share_token": "your_token",
  "batch_token": "bt_123...",
  "org_id": 1,
  "total_chunks": 245,
  "chunks": [
    {
      "chunk_index": 0,
      "text": "First 200 chars of chunk...",
      "metadata": {"page": 1},
      "created_at": "2025-10-25T12:34:56",
      "file_id": 42,
      "filename": "document.pdf"
    }
  ]
}
```

---

## 🐛 Common Errors

### "Share token not found" (404)
→ Check if token exists: `SELECT * FROM share_links WHERE token = 'your_token';`

### "Share token expired" (403)
→ Create a new share with longer expiration

### "Unauthorized" (403)
→ Add admin header: `-H "X-User-Role: admin"` OR match org_id

### "offer_chunks does not exist" (500)
→ Run the SQL migration: `psql $DATABASE_URL -f backend/scripts/create_offer_chunks_table.sql`

---

## 📚 Full Documentation

- **API Reference**: See `CHUNKS_REPORT_API.md`
- **Implementation Details**: See `IMPLEMENTATION_SUMMARY.md`
- **Tests**: Run `pytest backend/tests/test_chunks_report.py -v`
- **OpenAPI Docs**: Visit `http://localhost:8000/docs`

---

## 🎯 Quick Testing Checklist

- [ ] Server is running (`uvicorn app.main:app --reload`)
- [ ] Database table exists (run migration if needed)
- [ ] Have a valid share_token (create one if needed)
- [ ] Test with curl or Swagger UI
- [ ] Check logs for `[qa] chunks-report` messages
- [ ] Verify response has chunks array

---

## 💡 Pro Tips

1. **Use Swagger UI** (`/docs`) for interactive testing
2. **Check server logs** for `[qa] chunks-report start/done` messages
3. **Start with small limit** (e.g., `limit=5`) for faster testing
4. **Admin role** (`X-User-Role: admin`) bypasses org_id checks
5. **Pagination**: Use `offset` and `limit` for large datasets

---

## 📞 Need Help?

Check the full documentation:
- `CHUNKS_REPORT_API.md` - Complete API documentation
- `IMPLEMENTATION_SUMMARY.md` - Implementation details
- `backend/tests/test_chunks_report.py` - Test examples

Or check the code directly:
- Endpoint: `backend/api/routes/qa.py`, lines 472-606
- Main app: `app/main.py`, line 120 (router inclusion)

