# 📊 Upload → Chunk → Share: Complete Analysis

## 🎯 EXECUTIVE SUMMARY

**The data flow is correct**, but there are **3 critical linking points** where failures can occur:

1. ✅ **Upload → Chunks:** Direct FK, always works if reembed succeeds
2. ⚠️ **Share → Batch:** Filename matching inference, **CAN FAIL**
3. ⚠️ **Query → Chunks:** JOIN chain, fails if link #2 fails

---

## 🔄 THE CORRECT FLOW

### Upload Phase (backend/api/routes/offers_upload.py)

```python
POST /api/offers/upload
  ↓
1. Create/resolve offer_batches (get batch_token: "bt_xxx")
  ↓
2. Save PDF to disk: /storage/offers/{batch_token}/{filename}
  ↓
3. INSERT offer_files (filename, batch_id, storage_path)
  ↓
4. _reembed_file(file_id):
   - Extract text from PDF
   - Split into 1000-char chunks (200 overlap)
   - INSERT INTO offer_chunks (file_id, chunk_index, text, metadata)
   - SET embeddings_ready = true
  ↓
SUCCESS: Chunks linked via offer_chunks.file_id → offer_files.id
```

**This part is SOLID.** Chunks are directly linked to files via FK.

### Share Creation Phase (app/main.py:1122)

```python
POST /shares
  Body: { document_ids: ["uuid::1::file.pdf", ...] }
  ↓
1. Extract filenames from document_ids
   "abc123::1::Original File.pdf" → "Original File.pdf"
  ↓
2. Apply _safe_filename() normalization
   "Original File.pdf" → "Original_File.pdf"
  ↓
3. Query database:
   SELECT ob.token, COUNT(*) as match_count
   FROM offer_files of
   JOIN offer_batches ob ON ob.id = of.batch_id
   WHERE of.filename = ANY(['Original_File.pdf', ...])
   GROUP BY ob.token
   ORDER BY match_count DESC
   LIMIT 1
  ↓
4. Store in share.payload.batch_token
```

**This is the WEAK LINK.** Inference can fail if:
- Filenames don't match exactly
- Multiple batches have same files
- No files found in database

### Ask-Share Query Phase (backend/api/routes/qa.py:333)

```python
POST /api/qa/ask-share
  Body: { share_token: "...", question: "..." }
  ↓
1. Load share record → get payload.batch_token
  ↓
2. IF batch_token is NULL:
     → infer_batch_token_for_docs(document_ids, org_id)
  ↓
3. Query chunks:
   SELECT oc.text, of.filename, ...
   FROM offer_chunks oc
   JOIN offer_files of ON of.id = oc.file_id
   JOIN offer_batches ob ON ob.id = of.batch_id
   WHERE ob.token = batch_token
     AND ob.org_id = org_id
  ↓
4. IF rows == 0:
     → 404 "No offer chunks available"
```

**Query is correct**, but fails if `batch_token` is wrong/null.

---

## 🐛 ROOT CAUSE ANALYSIS

### Why "No offer chunks available" Happens

The chunks **DO exist** in the database, but the query **can't find them** because:

1. **batch_token is NULL in share.payload**
   - Inference failed during share creation
   - Share created before files uploaded
   - Filename mismatch

2. **batch_token points to wrong batch**
   - Multiple batches with overlapping filenames
   - Inference picked wrong batch (most recent, not correct one)

3. **org_id mismatch**
   - Share has org_id = 1
   - Batch has org_id = 2
   - Query filters by org_id, no results

4. **Chunks never created**
   - reembed failed during upload
   - embeddings_ready = false
   - Storage path doesn't exist

---

## 🔍 DIAGNOSTIC PROCESS

### Quick Check (30 seconds)

```bash
# 1. Check share
curl "https://gpt-vis.onrender.com/shares/Q4ZIHIb9OYtPuEbm1mP2mQ" | jq '{
  batch_token: .payload.batch_token,
  document_ids: .payload.document_ids,
  org_id: .org_id
}'

# 2. Check chunks
curl "https://gpt-vis.onrender.com/api/qa/chunks-report?share_token=Q4ZIHIb9OYtPuEbm1mP2mQ" | jq '{
  total_chunks: .total_chunks,
  batch_token: .batch_token
}'
```

**Expected:**
- `batch_token`: "bt_xxx" (not null)
- `total_chunks`: > 0

### Deep Dive (SQL)

Run the comprehensive diagnostic: `diagnose_share.sql`

```bash
psql $DATABASE_URL -f diagnose_share.sql
```

This traces the entire chain:
- Share → Batch token status
- Batch → Files list
- Files → Chunk counts
- Filename matching issues
- Files needing re-embed

---

## ✅ CORRECT CHUNKING

### Chunk Creation is Working

Looking at `backend/api/routes/qa.py:980-1018`:

```python
def _reembed_file(file_id, conn):
    # 1. Load file record from offer_files
    # 2. Verify storage_path exists
    # 3. Extract text from PDF (PyPDF)
    # 4. Split into chunks:
    text = _chunk_text(text, chunk_size=1000, overlap=200)
    # 5. Smart boundary detection:
    #    - Paragraph breaks (\n\n)
    #    - Sentence breaks (. ! ?)
    #    - Hard break at 1000 chars
    # 6. DELETE existing chunks for this file_id
    # 7. INSERT new chunks with metadata
    # 8. SET embeddings_ready = true
```

**This is CORRECT and ROBUST.**

### Chunk Metadata

Each chunk stores:
```json
{
  "chunk_index": 0,
  "start_pos": 0,
  "end_pos": 1023,
  "length": 1023
}
```

### Chunk Linking

```sql
-- Direct FK relationship
offer_chunks.file_id → offer_files.id (NEVER FAILS)

-- Via batch (for queries)
offer_files.batch_id → offer_batches.id → batch_token
```

**The FK link is solid. The batch_token link is the problem.**

---

## 🔗 HOW SHARES LINK TO CHUNKS

### Method 1: Via batch_token (Preferred)

```sql
share.payload.batch_token = "bt_xxx"
  ↓
SELECT * FROM offer_chunks oc
JOIN offer_files of ON of.id = oc.file_id
JOIN offer_batches ob ON ob.id = of.batch_id
WHERE ob.token = 'bt_xxx'
```

**Fast, reliable, direct.**

### Method 2: Via document_ids (Fallback)

```sql
share.payload.document_ids = ["uuid::1::file.pdf", ...]
  ↓
Extract filenames, apply _safe_filename()
  ↓
SELECT * FROM offer_chunks oc
JOIN offer_files of ON of.id = oc.file_id
WHERE of.filename = ANY(['file.pdf', ...])
  AND of.org_id = org_id
```

**Slower, fragile, relies on exact filename match.**

### Problem: Filename Mismatch

```python
# During upload (offers_upload.py:86)
filename = safe_filename("Original File Name (v2).pdf")
# Result: "Original_File_Name__v2_.pdf"

# In document_id
doc_id = "abc123::1::Original File Name (v2).pdf"
# Extracted: "Original File Name (v2).pdf"

# After _safe_filename in inference
inferred = safe_filename("Original File Name (v2).pdf")  
# Result: "Original_File_Name__v2_.pdf"  ← MATCH! ✅
```

**The code DOES apply `_safe_filename()` during inference (main.py:1092), so this should work!**

### But... Different Upload Paths

```python
# Path 1: /api/offers/upload
# Uses: safe_filename() from offers_upload.py

# Path 2: /extract/multiple-async
# Uses: _safe_filename() from main.py

# Are they the same function? Let's check...
```

---

## ⚠️ POTENTIAL BUG: Multiple safe_filename Functions?

<function_calls>
<invoke name="grep">
<parameter name="pattern">def.*safe.*filename
