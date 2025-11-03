# Re-embedding API Documentation

## Overview

The re-embedding functionality allows manual re-processing of uploaded PDF files to extract text, chunk it, and store it in the `offer_chunks` table. This is useful for:
- Fixing failed embeddings
- Re-processing files with updated chunking parameters
- Regenerating chunks after storage path changes

## Two Ways to Re-embed Files

### 1. Admin Endpoint (API)
```
POST /api/qa/reembed-file
```

### 2. CLI Script
```bash
python backend/scripts/reembed_file.py --file-id 46
```

---

## 🌐 API Endpoint

### Endpoint Details

**URL**: `/api/qa/reembed-file`  
**Method**: `POST`  
**Auth**: Admin only (requires `X-User-Role: admin` header)

### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_id` | integer (query) | **Yes** | ID of the file in `offer_files` table |

### Headers

| Header | Type | Required | Description |
|--------|------|----------|-------------|
| `X-User-Role` | string | **Yes** | Must be "admin" |

### Response

#### Success (200 OK)
```json
{
  "ok": true,
  "file_id": 46,
  "filename": "offer_document.pdf",
  "text_length": 15420,
  "chunks_created": 18,
  "chunks_deleted": 12,
  "embeddings_ready": true
}
```

#### Error (403 Forbidden)
```json
{
  "detail": "Unauthorized: only admin users can re-embed files"
}
```

#### Error (404 Not Found)
```json
{
  "detail": "File ID 46 not found"
}
```

#### Error (500 Internal Server Error)
```json
{
  "detail": "Re-embedding failed: File not found at path: /path/to/file.pdf"
}
```

### Example Usage

#### cURL
```bash
curl -X POST "http://localhost:8000/api/qa/reembed-file?file_id=46" \
  -H "X-User-Role: admin"
```

#### Python
```python
import requests

response = requests.post(
    "http://localhost:8000/api/qa/reembed-file",
    params={"file_id": 46},
    headers={"X-User-Role": "admin"}
)

if response.status_code == 200:
    data = response.json()
    print(f"✅ Success!")
    print(f"   Created {data['chunks_created']} chunks")
    print(f"   Deleted {data['chunks_deleted']} old chunks")
    print(f"   Ready: {data['embeddings_ready']}")
else:
    print(f"❌ Error: {response.json()}")
```

#### JavaScript/TypeScript
```typescript
async function reembedFile(fileId: number): Promise<void> {
  const response = await fetch(
    `http://localhost:8000/api/qa/reembed-file?file_id=${fileId}`,
    {
      method: "POST",
      headers: {
        "X-User-Role": "admin",
      },
    }
  );

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }

  const data = await response.json();
  console.log(`Created ${data.chunks_created} chunks`);
}

// Usage
reembedFile(46);
```

---

## 💻 CLI Script

### Basic Usage

```bash
# Re-embed a single file
python backend/scripts/reembed_file.py --file-id 46

# Re-embed all files in a batch
python backend/scripts/reembed_file.py --batch-id 5

# Dry run (don't modify database)
python backend/scripts/reembed_file.py --file-id 46 --dry-run

# Custom chunking parameters
python backend/scripts/reembed_file.py --file-id 46 --chunk-size 1500 --overlap 300
```

### Arguments

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `--file-id` | integer | * | - | ID of single file to re-embed |
| `--batch-id` | integer | * | - | ID of batch (all files) to re-embed |
| `--chunk-size` | integer | No | 1000 | Target chunk size in characters |
| `--overlap` | integer | No | 200 | Overlap between chunks (chars) |
| `--dry-run` | flag | No | false | Show what would happen without modifying DB |

*One of `--file-id` or `--batch-id` is required

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | **Yes** | PostgreSQL connection string |

### Examples

#### Re-embed a single file
```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/dbname"
python backend/scripts/reembed_file.py --file-id 46
```

Output:
```
[embedding] Connected to database
[embedding] start file_id=46
[embedding] file=offer_document.pdf path=/storage/offers/bt_123/offer_document.pdf
[embedding] extracting text from /storage/offers/bt_123/offer_document.pdf
[embedding] extracted 15420 characters
[embedding] chunking text (size=1000, overlap=200)
[embedding] created 18 chunks
[embedding] deleted 12 existing chunks
[embedding] inserted 18 chunks
[embedding] done file_id=46 chunks=18 ready=True

============================================================
RESULT:
{
  "ok": true,
  "file_id": 46,
  "filename": "offer_document.pdf",
  "text_length": 15420,
  "chunks_created": 18,
  "chunks_deleted": 12,
  "embeddings_ready": true
}
============================================================
```

#### Re-embed all files in a batch
```bash
python backend/scripts/reembed_file.py --batch-id 5
```

Output:
```
[embedding] batch start batch_id=5
[embedding] Found 3 files in batch 5

[embedding] Processing file1.pdf (id=46)
[embedding] done file_id=46 chunks=18 ready=True

[embedding] Processing file2.pdf (id=47)
[embedding] done file_id=47 chunks=22 ready=True

[embedding] Processing file3.pdf (id=48)
[embedding] done file_id=48 chunks=15 ready=True

[embedding] batch done batch_id=5 success=3 errors=0

============================================================
BATCH RESULT:
Total files: 3
Success: 3
Errors: 0
============================================================
```

#### Dry run
```bash
python backend/scripts/reembed_file.py --file-id 46 --dry-run
```

Output:
```
[embedding] start file_id=46
[embedding] file=offer_document.pdf path=/storage/offers/bt_123/offer_document.pdf
[embedding] extracting text from /storage/offers/bt_123/offer_document.pdf
[embedding] extracted 15420 characters
[embedding] chunking text (size=1000, overlap=200)
[embedding] created 18 chunks
[embedding] DRY RUN - not modifying database
[embedding] Would delete existing chunks and insert 18 new chunks

============================================================
RESULT:
{
  "ok": true,
  "dry_run": true,
  "file_id": 46,
  "filename": "offer_document.pdf",
  "text_length": 15420,
  "chunks_would_create": 18
}
============================================================
```

---

## 🔧 How It Works

### Processing Steps

1. **Load file record** from `offer_files` table
2. **Verify storage path** exists and is not blank
3. **Extract text** from PDF using PyPDF
4. **Validate text** (must be > 10 characters)
5. **Split into chunks** with smart sentence/paragraph boundary detection
6. **Delete existing chunks** for this file_id
7. **Insert new chunks** into `offer_chunks` table
8. **Update `embeddings_ready`** flag to true (if chunks inserted)

### Chunking Algorithm

The chunking algorithm:
- Target chunk size: 1000 characters (configurable)
- Overlap: 200 characters (configurable)
- Smart boundary detection:
  1. First tries to break at paragraph boundaries (`\n\n`)
  2. Falls back to sentence boundaries (`. `, `! `, `? `)
  3. If neither found, breaks at target size

Each chunk includes metadata:
```json
{
  "chunk_index": 0,
  "start_pos": 0,
  "end_pos": 1023,
  "length": 1023
}
```

### Error Handling

On error:
- Sets `embeddings_ready = false` in `offer_files`
- Logs error with `[embedding]` tag
- Returns detailed error message
- Rolls back transaction (no partial updates)

---

## 📊 Database Schema

### offer_files Table
```sql
-- Updated fields
embeddings_ready BOOLEAN DEFAULT FALSE
```

### offer_chunks Table
```sql
CREATE TABLE public.offer_chunks (
    id SERIAL PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES public.offer_files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(file_id, chunk_index)
);
```

---

## 🔍 Logging

All operations log with the `[embedding]` tag:

```
[embedding] start file_id=46
[embedding] file=offer_document.pdf path=/storage/offers/...
[embedding] extracting text from /storage/offers/...
[embedding] extracted 15420 characters
[embedding] chunking text (size=1000, overlap=200)
[embedding] created 18 chunks
[embedding] deleted 12 existing chunks
[embedding] inserted 18 chunks
[embedding] done file_id=46 chunks=18 ready=True
```

Or on error:
```
[embedding] error file_id=46: File not found at path: /storage/...
```

---

## 🧪 Testing

### Test the API Endpoint

```bash
# 1. Start the server
uvicorn app.main:app --reload

# 2. Test with a valid file_id
curl -X POST "http://localhost:8000/api/qa/reembed-file?file_id=46" \
  -H "X-User-Role: admin"

# 3. Check the logs for [embedding] messages
```

### Test the CLI Script

```bash
# 1. Set DATABASE_URL
export DATABASE_URL="postgresql://user:pass@localhost:5432/dbname"

# 2. Test with dry-run first
python backend/scripts/reembed_file.py --file-id 46 --dry-run

# 3. Run actual re-embedding
python backend/scripts/reembed_file.py --file-id 46

# 4. Verify chunks were created
psql $DATABASE_URL -c "SELECT COUNT(*) FROM offer_chunks WHERE file_id = 46;"
```

---

## 🚨 Common Issues

### "File ID X not found"
- Check if file exists: `SELECT * FROM offer_files WHERE id = X;`

### "storage_path is blank"
- File record has no storage_path
- Re-upload the file or update the record

### "File not found at path"
- Storage path in DB doesn't match actual file location
- Check file system: `ls -la /path/from/storage_path`
- Update storage_path in DB if file was moved

### "Extracted text is empty or too short"
- PDF may be image-based (scanned) without text layer
- Try OCR processing first
- Check PDF manually: `pdftotext file.pdf -`

### "Unauthorized: only admin users can re-embed files"
- Add admin header: `-H "X-User-Role: admin"`

### CLI script: "DATABASE_URL environment variable not set"
```bash
export DATABASE_URL="postgresql://..."
```

---

## 💡 Best Practices

1. **Always dry-run first** when testing new chunking parameters:
   ```bash
   python backend/scripts/reembed_file.py --file-id 46 --dry-run
   ```

2. **Use batch processing** for multiple files:
   ```bash
   python backend/scripts/reembed_file.py --batch-id 5
   ```

3. **Monitor logs** for issues:
   ```bash
   grep "[embedding]" server.log
   ```

4. **Adjust chunk size** based on your use case:
   - Smaller chunks (500-800): Better for precise search
   - Larger chunks (1500-2000): Better context preservation

5. **Verify results** after re-embedding:
   ```sql
   SELECT 
       f.id, 
       f.filename, 
       COUNT(c.id) as chunk_count,
       f.embeddings_ready
   FROM offer_files f
   LEFT JOIN offer_chunks c ON c.file_id = f.id
   WHERE f.id = 46
   GROUP BY f.id;
   ```

---

## 📚 See Also

- `backend/api/routes/qa.py` - Endpoint implementation (lines 610-845)
- `backend/scripts/reembed_file.py` - CLI script
- `backend/scripts/create_offer_chunks_table.sql` - Database schema
- `/api/qa/chunks-report` - View created chunks



