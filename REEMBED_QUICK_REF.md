# Re-embedding Quick Reference

## 🚀 Quick Start

### API Endpoint
```bash
curl -X POST "http://localhost:8000/api/qa/reembed-file?file_id=46" \
  -H "X-User-Role: admin"
```

### CLI Script
```bash
# Single file
python backend/scripts/reembed_file.py --file-id 46

# Entire batch
python backend/scripts/reembed_file.py --batch-id 5

# Dry run (preview)
python backend/scripts/reembed_file.py --file-id 46 --dry-run
```

---

## 📍 Endpoint Details

- **URL**: `POST /api/qa/reembed-file`
- **Auth**: Admin only (`X-User-Role: admin` header)
- **Param**: `file_id` (query parameter)
- **Location**: `backend/api/routes/qa.py` (lines 817-845)

---

## 💻 CLI Options

```bash
python backend/scripts/reembed_file.py \
  --file-id 46 \              # OR --batch-id 5
  --chunk-size 1000 \         # Default: 1000
  --overlap 200 \             # Default: 200
  --dry-run                   # Optional: preview only
```

**Environment**: Requires `DATABASE_URL` environment variable

---

## ✅ What It Does

1. Loads file from `offer_files` table
2. Extracts text from PDF at `storage_path`
3. Chunks text (smart boundary detection)
4. Deletes old chunks for this file
5. Inserts new chunks into `offer_chunks`
6. Sets `embeddings_ready = true` if successful

---

## 🔍 Logging

Look for `[embedding]` tags in logs:
```
[embedding] start file_id=46
[embedding] extracted 15420 characters
[embedding] created 18 chunks
[embedding] done file_id=46 chunks=18 ready=True
```

---

## 📊 Example Response

```json
{
  "ok": true,
  "file_id": 46,
  "filename": "offer.pdf",
  "text_length": 15420,
  "chunks_created": 18,
  "chunks_deleted": 12,
  "embeddings_ready": true
}
```

---

## 🐛 Common Issues

| Error | Solution |
|-------|----------|
| File not found | Check `SELECT * FROM offer_files WHERE id = X` |
| storage_path is blank | Update DB or re-upload file |
| Unauthorized | Add `-H "X-User-Role: admin"` |
| DATABASE_URL not set | `export DATABASE_URL="postgresql://..."` |

---

## 📚 Full Docs

- **API Reference**: `REEMBED_API.md`
- **Implementation**: `REEMBED_SUMMARY.md`
- **Tests**: `backend/tests/test_reembed.py`
- **Swagger**: `http://localhost:8000/docs`

---

## 🎯 Quick Test

```bash
# 1. Set env
export DATABASE_URL="postgresql://user:pass@localhost/db"

# 2. Dry run
python backend/scripts/reembed_file.py --file-id 46 --dry-run

# 3. Actually run
python backend/scripts/reembed_file.py --file-id 46

# 4. Check results
psql $DATABASE_URL -c "SELECT COUNT(*) FROM offer_chunks WHERE file_id=46"
```



