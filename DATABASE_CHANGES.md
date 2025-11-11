# 🎯 Database Changes for Multi-File Share

## ✅ GREAT NEWS: NO DATABASE CHANGES REQUIRED!

The multi-file share implementation uses the **existing schema** with no modifications needed.

---

## 📊 Current Schema (Already Sufficient)

### Tables Used

```sql
-- 1. offer_files: Stores uploaded PDFs
CREATE TABLE public.offer_files (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL,
    batch_id INTEGER REFERENCES public.offer_batches(id),
    filename TEXT NOT NULL,
    storage_path TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    embeddings_ready BOOLEAN DEFAULT FALSE,
    insurer_code TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 2. offer_chunks: Stores text chunks from PDFs
CREATE TABLE public.offer_chunks (
    id SERIAL PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES public.offer_files(id),
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

-- 3. share_links: Stores share tokens and metadata
CREATE TABLE public.share_links (
    id SERIAL PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    org_id INTEGER,
    payload JSONB DEFAULT '{}',  -- ← We store file_ids HERE
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 💡 How It Works Without Schema Changes

### Before (Single Batch)

```json
// share_links.payload
{
  "batch_token": "bt_xxx",
  "mode": "by-documents"
}
```

Query:
```sql
SELECT oc.* FROM offer_chunks oc
JOIN offer_files of ON of.id = oc.file_id
JOIN offer_batches ob ON ob.id = of.batch_id
WHERE ob.token = 'bt_xxx';
```

### After (Multi-File)

```json
// share_links.payload
{
  "file_ids": [207, 208, 209, 210],  // ← NEW field in existing JSONB
  "mode": "by-files"
}
```

Query:
```sql
SELECT oc.* FROM offer_chunks oc
JOIN offer_files of ON of.id = oc.file_id
WHERE oc.file_id = ANY(ARRAY[207, 208, 209, 210]);
```

**The `payload` column is already JSONB** - we just add a new key! No ALTER TABLE needed.

---

## 🔍 Optional: Index for Performance (Recommended)

If you'll have many shares with many files, add an index on `offer_chunks.file_id` for faster queries:

```sql
-- Check if index exists
SELECT indexname 
FROM pg_indexes 
WHERE tablename = 'offer_chunks' 
  AND indexname = 'idx_offer_chunks_file_id';

-- Create index if not exists
CREATE INDEX IF NOT EXISTS idx_offer_chunks_file_id 
ON public.offer_chunks(file_id);
```

**This is OPTIONAL** and only improves performance. The system works without it.

---

## 🎲 Alternative Approach: Junction Table (NOT IMPLEMENTED)

If you wanted a more normalized design, you COULD create:

```sql
-- NOT NEEDED, just showing the alternative
CREATE TABLE public.share_files (
    share_token TEXT NOT NULL REFERENCES public.share_links(token),
    file_id INTEGER NOT NULL REFERENCES public.offer_files(id),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (share_token, file_id)
);

-- Then query:
SELECT oc.* FROM offer_chunks oc
JOIN share_files sf ON sf.file_id = oc.file_id
WHERE sf.share_token = 'xyz';
```

**But this is overkill!** Storing file_ids in the JSONB payload is simpler and works perfectly.

---

## 📋 SQL Queries You Might Need

### 1. Check Existing Shares Structure

```sql
-- See what's in share payloads
SELECT 
    token,
    payload->>'mode' as mode,
    payload->>'batch_token' as batch_token,
    payload->'file_ids' as file_ids,
    payload->'document_ids' as document_ids,
    created_at
FROM public.share_links
ORDER BY created_at DESC
LIMIT 10;
```

### 2. Migrate Old Share to Use file_ids (Optional)

If you want to convert an existing share to use file_ids:

```sql
-- 1. Find files for the batch
SELECT id FROM public.offer_files
WHERE batch_id = (
    SELECT id FROM public.offer_batches 
    WHERE token = 'bt_old_batch_token'
);
-- Result: [101, 102, 103]

-- 2. Update share to use file_ids
UPDATE public.share_links
SET payload = jsonb_set(
    payload, 
    '{file_ids}', 
    '[101, 102, 103]'::jsonb
)
WHERE token = 'old_share_token';
```

### 3. Verify Chunks for file_ids

```sql
-- Test query: Check chunks for specific file_ids
SELECT 
    of.id as file_id,
    of.filename,
    COUNT(oc.id) as chunk_count
FROM public.offer_files of
LEFT JOIN public.offer_chunks oc ON oc.file_id = of.id
WHERE of.id = ANY(ARRAY[207, 208])
GROUP BY of.id, of.filename;
```

### 4. Find All Shares Using Multi-File Mode

```sql
-- Find shares that have file_ids
SELECT 
    token,
    payload->>'title' as title,
    jsonb_array_length(payload->'file_ids') as file_count,
    payload->'file_ids' as file_ids
FROM public.share_links
WHERE payload ? 'file_ids'
  AND jsonb_array_length(payload->'file_ids') > 0;
```

---

## ✅ SUMMARY

| Item | Status | Action |
|------|--------|--------|
| Schema changes | ❌ Not needed | None |
| New tables | ❌ Not needed | None |
| Migrations | ❌ Not needed | None |
| Index (optional) | ⚠️ Recommended | See above |
| Code changes | ✅ Done | Already committed |

---

## 🚀 Deployment Checklist

- [x] Code changes implemented
- [x] No database migrations needed
- [ ] (Optional) Create index on offer_chunks.file_id for performance
- [ ] Deploy to production
- [ ] Test with 2+ files
- [ ] Test with 10+ files

---

## 🎉 YOU'RE ALL SET!

**No SQL commands required to run.** The implementation works with your existing database schema as-is!

Just deploy the code changes and you can immediately start creating multi-file shares! 🚀

