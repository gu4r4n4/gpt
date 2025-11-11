# 📊 Upload → Chunks → Share Linking Analysis

## 🔄 COMPLETE DATA FLOW

### Phase 1: File Upload → Chunks Creation

```
┌──────────────────────────────────────────────────────────────┐
│ 1. FILE UPLOAD                                               │
│    POST /api/offers/upload                                   │
│    (backend/api/routes/offers_upload.py:68)                  │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. CREATE/RESOLVE BATCH                                      │
│    _resolve_or_create_batch()                                │
│    → Creates offer_batches row with token (bt_xxx)           │
│    → Returns: batch_id, batch_token                          │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. SAVE FILE TO DISK                                         │
│    Path: /storage/offers/{batch_token}/{filename}           │
│    Safe filename applied                                     │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. INSERT offer_files RECORD                                 │
│    INSERT INTO public.offer_files                            │
│    (filename, storage_path, batch_id, org_id, ...)           │
│    RETURNING id  ← file_id                                   │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. CHUNK CREATION (_reembed_file)                            │
│    a) Read PDF from storage_path                             │
│    b) Extract text (PyPDF)                                   │
│    c) Split into chunks (1000 chars, 200 overlap)            │
│    d) DELETE FROM offer_chunks WHERE file_id = ?             │
│    e) INSERT chunks into offer_chunks                        │
│       - file_id (FK to offer_files)                          │
│       - chunk_index                                          │
│       - text                                                 │
│       - metadata {chunk_index, start_pos, end_pos, length}   │
│    f) UPDATE offer_files SET embeddings_ready = true         │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
                 ✅ SUCCESS
    Chunks linked: offer_chunks.file_id → offer_files.id
                   offer_files.batch_id → offer_batches.id
```

### Phase 2: Share Creation → Batch Token Linking

```
┌──────────────────────────────────────────────────────────────┐
│ 1. CREATE SHARE                                              │
│    POST /shares                                              │
│    Body: { document_ids: ["uuid::1::file.pdf", ...] }       │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. INFER BATCH TOKEN                                         │
│    _infer_batch_token_via_doc_ids(document_ids)              │
│    (app/main.py:1078)                                        │
│                                                              │
│    Logic:                                                    │
│    a) Extract filename from doc_id: "uuid::idx::FILE.pdf"   │
│    b) Apply _safe_filename() to normalize                   │
│    c) Query:                                                 │
│       SELECT ob.token, COUNT(*) as match_count              │
│       FROM offer_files of                                    │
│       JOIN offer_batches ob ON ob.id = of.batch_id          │
│       WHERE of.filename = ANY(filenames_array)              │
│       GROUP BY ob.token                                      │
│       ORDER BY match_count DESC                             │
│       LIMIT 1                                                │
│                                                              │
│    → Returns: batch_token (or NULL if no match)             │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. CREATE SHARE RECORD                                       │
│    INSERT INTO share_links                                   │
│    payload: {                                                │
│      batch_token: "bt_xxx"  ← From inference                │
│      document_ids: [...]                                     │
│      mode: "by-documents"                                    │
│    }                                                         │
└──────────────────────────────────────────────────────────────┘
```

### Phase 3: Ask-Share Query → Chunks Retrieval

```
┌──────────────────────────────────────────────────────────────┐
│ 1. ASK-SHARE REQUEST                                         │
│    POST /api/qa/ask-share                                    │
│    Body: { share_token: "...", question: "..." }            │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. LOAD SHARE RECORD                                         │
│    _load_share_record(share_token)                           │
│    → Returns: { payload: { batch_token, document_ids } }    │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. GET ORG_ID & BATCH_TOKEN                                  │
│    org_id = share.org_id                                     │
│    batch_token = share.payload.batch_token                   │
│                                                              │
│    IF batch_token is NULL:                                   │
│      → infer_batch_token_for_docs(document_ids, org_id)     │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. QUERY CHUNKS (_select_offer_chunks_from_db)              │
│    (backend/api/routes/qa.py:29)                             │
│                                                              │
│    IF batch_token exists:                                    │
│      SELECT oc.file_id, of.filename, oc.chunk_index,        │
│             oc.text, of.insurer_code                         │
│      FROM offer_chunks oc                                    │
│      JOIN offer_files of ON of.id = oc.file_id              │
│      JOIN offer_batches ob ON ob.id = of.batch_id           │
│      WHERE ob.token = %batch_token%                          │
│        AND ob.org_id = %org_id%                              │
│                                                              │
│    ELSE (fallback to document_ids):                          │
│      SELECT ... FROM offer_chunks oc                         │
│      JOIN offer_files of ON of.id = oc.file_id              │
│      WHERE of.org_id = %org_id%                              │
│        AND of.filename = ANY(%document_ids%)                 │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. RESULT CHECK                                              │
│    IF rows.length == 0:                                      │
│      → HTTPException(404, "No offer chunks available")       │
│    ELSE:                                                     │
│      → Embed question, rank chunks, generate answer          │
└──────────────────────────────────────────────────────────────┘
```

---

## 🔍 CRITICAL LINKING POINTS

### ✅ Point 1: File Upload → Chunks (Direct Link)
```sql
offer_chunks.file_id → offer_files.id
```
**Created by:** `_reembed_file()` during upload  
**Always works** if reembed succeeds

### ⚠️ Point 2: Share → Batch Token (Inference)
```sql
document_ids → filename matching → offer_files.filename
                                    → offer_files.batch_id
                                    → offer_batches.token
```
**Created by:** `_infer_batch_token_via_doc_ids()`  
**CAN FAIL** if:
- Filename mismatch (doc_id has different name than offer_files.filename)
- No matching files in database
- Multiple batches, wrong one selected

### ⚠️ Point 3: Ask-Share → Chunks (Via Batch)
```sql
batch_token → offer_batches.id
           → offer_files.batch_id
           → offer_chunks.file_id
```
**CAN FAIL** if:
- batch_token is NULL (inference failed)
- batch_token points to wrong batch
- org_id mismatch
- Chunks weren't created

---

## 🐛 COMMON FAILURE MODES

### Failure 1: Filename Mismatch
**Symptom:** `total_chunks = 0` but files exist

```python
# Document ID from old upload:
doc_id = "abc123::1::Original File Name.pdf"

# After upload with safe_filename:
offer_files.filename = "Original_File_Name.pdf"

# Inference extracts:
extracted = "Original File Name.pdf"  # ← No match!
```

**Fix:** Use `_safe_filename()` during inference (already implemented in main.py:1092)

### Failure 2: Batch Token NULL in Share
**Symptom:** Share has `batch_token: null`, inference fails at query time

**Causes:**
- Share created before files uploaded
- document_ids don't match any offer_files
- Inference logic bug

**Debug:**
```sql
-- Check what's in share
SELECT payload->>'batch_token', payload->>'document_ids' 
FROM share_links 
WHERE token = 'SHARE_TOKEN';

-- Check what's in offer_files
SELECT filename, batch_id 
FROM offer_files 
WHERE org_id = X;
```

### Failure 3: Chunks Never Created
**Symptom:** `embeddings_ready = false`, chunk count = 0

**Causes:**
- Reembed failed during upload
- PDF text extraction failed
- File not found on disk

**Debug:**
```sql
-- Check embedding status
SELECT id, filename, embeddings_ready, storage_path
FROM offer_files
WHERE batch_id = (SELECT id FROM offer_batches WHERE token = 'BATCH_TOKEN');

-- Check chunk count
SELECT file_id, COUNT(*) 
FROM offer_chunks 
WHERE file_id IN (SELECT id FROM offer_files WHERE batch_id = X)
GROUP BY file_id;
```

### Failure 4: Wrong Batch Selected
**Symptom:** Query runs but returns empty (wrong org_id or batch)

**Cause:** Inference picks wrong batch when multiple batches have same filenames

**Debug:**
```sql
-- Find all batches with matching files
SELECT ob.token, ob.org_id, COUNT(*) as file_count
FROM offer_files of
JOIN offer_batches ob ON ob.id = of.batch_id
WHERE of.filename IN ('file1.pdf', 'file2.pdf')
GROUP BY ob.token, ob.org_id;
```

---

## 🧪 DEBUGGING CHECKLIST

### Step 1: Check Share → Batch Link
```bash
curl "https://gpt-vis.onrender.com/shares/SHARE_TOKEN" | jq '{
  batch_token: .payload.batch_token,
  document_ids: .payload.document_ids,
  org_id: .org_id
}'
```

**Expected:** `batch_token` should be a `bt_xxx` string, not null

### Step 2: Check Batch → Files Link
```sql
-- Use batch_token from above
SELECT id, filename, embeddings_ready, storage_path
FROM offer_files
WHERE batch_id = (SELECT id FROM offer_batches WHERE token = 'bt_xxx')
ORDER BY id;
```

**Expected:** Multiple files, `embeddings_ready = true`

### Step 3: Check Files → Chunks Link
```sql
-- Use file IDs from above
SELECT file_id, COUNT(*) as chunk_count
FROM offer_chunks
WHERE file_id IN (SELECT id FROM offer_files WHERE batch_id = X)
GROUP BY file_id;
```

**Expected:** Each file has > 0 chunks

### Step 4: Test Chunks Query Directly
```sql
-- This is what ask-share runs
SELECT oc.file_id, of.filename, oc.chunk_index, LEFT(oc.text, 100) as preview
FROM offer_chunks oc
JOIN offer_files of ON of.id = oc.file_id
JOIN offer_batches ob ON ob.id = of.batch_id
WHERE ob.token = 'bt_xxx'
  AND ob.org_id = 1
LIMIT 5;
```

**Expected:** Rows returned with text previews

---

## 🩹 FIXES

### Fix 1: Re-Infer Batch Token for Share
```sql
-- Get document IDs from share
SELECT payload->>'document_ids' FROM share_links WHERE token = 'SHARE_TOKEN';

-- Manually find correct batch
SELECT ob.token, COUNT(*) as matches
FROM offer_files of
JOIN offer_batches ob ON ob.id = of.batch_id
WHERE of.filename IN ('file1.pdf', 'file2.pdf')  -- From document_ids
GROUP BY ob.token
ORDER BY matches DESC
LIMIT 1;

-- Update share with correct batch_token
UPDATE share_links
SET payload = jsonb_set(payload, '{batch_token}', '"bt_correct_token"')
WHERE token = 'SHARE_TOKEN';
```

### Fix 2: Re-Embed Files
```bash
# For each file that has embeddings_ready = false
curl -X POST "https://gpt-vis.onrender.com/api/qa/reembed-file?file_id=FILE_ID" \
  -H "X-User-Role: admin"
```

### Fix 3: Recreate Share with Explicit Batch Token
```bash
curl -X POST "https://gpt-vis.onrender.com/shares" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: 1" \
  -H "X-User-Id: 1" \
  -d '{
    "batch_token": "bt_correct_token",
    "title": "Recreated Share",
    "editable": true
  }'
```

---

## 📋 SUMMARY

### The Chain
```
Upload → offer_files.batch_id → offer_batches.token (STORED)
                                          ↓
Share.payload.batch_token (INFERRED from document_ids)
                                          ↓
Query JOINs: batch_token → offer_batches → offer_files → offer_chunks
```

### Weak Points
1. **Filename matching** during batch_token inference
2. **Null batch_token** in share.payload
3. **Missing chunks** (reembed failures)
4. **Org_id mismatches**

### Key Fix
**Always provide `batch_token` explicitly when creating shares** if you know it, rather than relying on inference:

```json
{
  "batch_token": "bt_known_token",
  "document_ids": [...]
}
```

This bypasses the filename matching logic entirely.

