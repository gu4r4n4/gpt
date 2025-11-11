# 🎯 Multi-File Share Implementation

## ✅ IMPLEMENTED!

Your use case is now fully supported: Upload 10+ PDF files and link them all together in ONE share.

---

## 🔄 HOW IT WORKS

### 1. Upload Multiple Files

Upload files individually (they'll get different batch tokens):

```bash
# Upload file 1
curl -X POST "https://gpt-vis.onrender.com/api/offers/upload" \
  -F "pdf=@GJENSIDIGE-VA.pdf" \
  -F "org_id=1"
# Response: {"file_id": 207, "batch_token": "bt_xxx", "chunks": 49}

# Upload file 2
curl -X POST "https://gpt-vis.onrender.com/api/offers/upload" \
  -F "pdf=@IF-VA.pdf" \
  -F "org_id=1"
# Response: {"file_id": 208, "batch_token": "bt_yyy", "chunks": 9}

# Upload files 3-10...
# Response: {"file_id": 209-216, ...}
```

Collect all `file_id` values: `[207, 208, 209, 210, ...]`

---

### 2. Create Multi-File Share

When user clicks "Create Share", send all file IDs together:

**Postman / API Call:**
- Method: `POST`
- URL: `https://gpt-vis.onrender.com/shares`
- Headers:
  - `Content-Type: application/json`
  - `X-Org-Id: 1`
  - `X-User-Id: 1`
- Body:
```json
{
  "file_ids": [207, 208, 209, 210, 211, 212, 213, 214, 215, 216],
  "title": "LDZ - All Insurers Comparison",
  "company_name": "LDZ",
  "employees_count": 45,
  "editable": true
}
```

**Response:**
```json
{
  "ok": true,
  "token": "new_share_token_xyz",
  "url": "https://gpt-vis.onrender.com/share/new_share_token_xyz"
}
```

---

### 3. Ask Questions on Multi-File Share

The share now has access to chunks from ALL 10 files:

**Postman:**
- Method: `POST`
- URL: `https://gpt-vis.onrender.com/api/qa/ask-share`
- Headers: `Content-Type: application/json`
- Body:
```json
{
  "share_token": "new_share_token_xyz",
  "question": "Compare the premiums across all insurers",
  "lang": "en"
}
```

**Response:**
```json
{
  "answer": "Based on the offers, here's the premium comparison:\n- GJENSIDIGE: 471 EUR\n- IF Program A: ...\n[answers using chunks from all 10 files]",
  "sources": [
    "GJENSIDIGE-VA.pdf",
    "IF-VA.pdf",
    "BALTA-VA.pdf",
    ...
  ]
}
```

---

## 🏗️ WHAT WAS CHANGED

### 1. Share Creation (app/main.py)

**Added `file_ids` field:**
```python
class ShareCreateBody(BaseModel):
    file_ids: Optional[List[int]] = Field(None, description="Direct file IDs for multi-file shares")
    # ... existing fields
```

**Stores in payload:**
```python
payload = {
    "file_ids": body.file_ids or [],  # NEW
    "batch_token": inferred_batch_token,  # Still supported for backward compatibility
    # ... rest
}
```

### 2. Ask-Share Query (backend/api/routes/qa.py)

**Added new function:**
```python
def _select_offer_chunks_by_file_ids(
    conn,
    file_ids: List[int],
    insurer_only: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Query chunks directly by file IDs"""
    # Queries: offer_chunks JOIN offer_files WHERE file_id IN (...)
```

**Updated ask-share logic:**
```python
# Priority: file_ids > batch_token > document_ids
file_ids = payload.get("file_ids", [])

if file_ids:
    # NEW: Use file_ids directly
    rows = _select_offer_chunks_by_file_ids(conn, file_ids, insurer_only)
else:
    # OLD: Use batch_token (still works)
    rows = _select_offer_chunks_from_db(conn, org_id, batch_token, ...)
```

---

## ✅ NO DATABASE CHANGES NEEDED!

**This implementation requires ZERO database schema changes:**
- ❌ No new tables
- ❌ No new columns
- ❌ No migrations
- ✅ Uses existing `offer_files` and `offer_chunks` tables
- ✅ Stores `file_ids` in existing `share_links.payload` JSONB field

---

## 🧪 TESTING YOUR SCENARIO

### Full Workflow Test

1. **Upload 10 files:**
```bash
for i in {1..10}; do
  curl -X POST "https://gpt-vis.onrender.com/api/offers/upload" \
    -F "pdf=@file$i.pdf" \
    -F "org_id=1" | jq '.file_id'
done
# Outputs: 207, 208, 209, 210, 211, 212, 213, 214, 215, 216
```

2. **Create share with all files:**
```bash
curl -X POST "https://gpt-vis.onrender.com/shares" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: 1" -H "X-User-Id: 1" \
  -d '{
    "file_ids": [207, 208, 209, 210, 211, 212, 213, 214, 215, 216],
    "title": "10 Insurers Comparison",
    "company_name": "Test Company",
    "employees_count": 50
  }' | jq '{token, url}'
```

3. **Verify chunks are accessible:**
```bash
SHARE_TOKEN="xyz"  # From step 2

curl "https://gpt-vis.onrender.com/api/qa/chunks-report?share_token=$SHARE_TOKEN" \
  -H "X-User-Role: admin" \
  -H "X-Org-Id: 1" | jq '{
  total_chunks: .total_chunks,
  file_count: (.chunks | group_by(.file_id) | length)
}'
# Should show: total_chunks = sum of all chunks from 10 files
```

4. **Test ask-share:**
```bash
curl -X POST "https://gpt-vis.onrender.com/api/qa/ask-share" \
  -H "Content-Type: application/json" \
  -d "{
    \"share_token\": \"$SHARE_TOKEN\",
    \"question\": \"What are all the base sums?\",
    \"lang\": \"en\"
  }" | jq '{answer, sources_count: (.sources | length)}'
```

---

## 🔄 BACKWARD COMPATIBILITY

Old shares still work:
- ✅ Shares with `batch_token` (single batch)
- ✅ Shares with `document_ids` (inference)
- ✅ Snapshot shares

New shares can use:
- ✅ `file_ids` (multi-file, recommended)
- ✅ `batch_token` (single batch, still supported)
- ✅ Both together (file_ids takes priority)

---

## 💡 FRONTEND INTEGRATION

### On Upload Page

```typescript
// Track uploaded file IDs
const uploadedFileIds: number[] = [];

async function uploadFile(file: File) {
  const formData = new FormData();
  formData.append('pdf', file);
  formData.append('org_id', '1');
  
  const response = await fetch('https://gpt-vis.onrender.com/api/offers/upload', {
    method: 'POST',
    body: formData
  });
  
  const result = await response.json();
  uploadedFileIds.push(result.file_id);
  
  console.log(`Uploaded ${file.name}: file_id=${result.file_id}, chunks=${result.chunks}`);
}

// Upload all files
for (const file of selectedFiles) {
  await uploadFile(file);
}

console.log('All files uploaded:', uploadedFileIds);
```

### On Create Share Button Click

```typescript
async function createMultiFileShare() {
  const response = await fetch('https://gpt-vis.onrender.com/shares', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Org-Id': '1',
      'X-User-Id': '1'
    },
    body: JSON.stringify({
      file_ids: uploadedFileIds,  // All uploaded file IDs
      title: 'My Comparison',
      company_name: 'Company Name',
      employees_count: 45,
      editable: true
    })
  });
  
  const result = await response.json();
  
  // Redirect to share view
  window.location.href = result.url;
  // Or: window.location.href = `/share/${result.token}`;
}
```

---

## 📊 COMPARISON: Old vs New

| Aspect | OLD System | NEW System |
|--------|-----------|------------|
| Upload 10 files | 10 different batches | 10 different batches (same) |
| Create share | Uses batch_token (1 batch only) | Uses file_ids (all files) ✅ |
| Chunk query | batch → files → chunks | file_ids → chunks directly ✅ |
| Max files per share | ~10 (same batch) | Unlimited ✅ |
| DB changes | N/A | None needed ✅ |
| Backward compatible | N/A | Yes ✅ |

---

## 🎉 BENEFITS

1. **No database migrations** - Uses existing schema
2. **Backward compatible** - Old shares still work
3. **Scalable** - Support unlimited files per share
4. **Simple** - No complex join tables or denormalization
5. **Fast** - Direct query by file_ids
6. **Flexible** - Can mix file_ids + batch_token if needed

---

## 📝 EXAMPLE: Your Actual Use Case

```bash
# Your 2 uploads (already done):
# File 207: GJENSIDIGE-VA.pdf, 49 chunks
# File 208: IF-VA.pdf, 9 chunks

# Create share linking BOTH:
curl -X POST "https://gpt-vis.onrender.com/shares" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: 1" -H "X-User-Id: 1" \
  -d '{
    "file_ids": [207, 208],
    "title": "LDZ - GJENSIDIGE vs IF",
    "company_name": "LDZ",
    "employees_count": 45,
    "editable": true
  }'

# Now ask-share will query chunks from BOTH files!
# Total chunks available: 49 + 9 = 58
```

---

## 🚀 DEPLOYMENT

Changes are ready to commit:
```bash
git add app/main.py backend/api/routes/qa.py
git commit -m "feat: Support multi-file shares via file_ids

- Add file_ids field to ShareCreateBody
- Store file_ids in share payload
- Add _select_offer_chunks_by_file_ids() function
- Update ask-share to prioritize file_ids over batch_token
- Maintains backward compatibility with existing shares
- No database schema changes required"
git push
```

After deployment (5-10 min), you can create multi-file shares immediately!

---

## ✅ DONE!

Your 10+ files per share use case is now fully implemented with zero database changes! 🎉

