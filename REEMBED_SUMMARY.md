# Re-embedding Feature - Implementation Summary

## ✅ What Was Implemented

A complete re-embedding system that allows manual re-processing of PDF files to extract text, chunk it, and store chunks in the `offer_chunks` table.

### Two Access Methods

1. **Admin API Endpoint**: `POST /api/qa/reembed-file?file_id=46`
2. **CLI Script**: `python backend/scripts/reembed_file.py --file-id 46`

---

## 📋 Feature Checklist

All requirements have been implemented:

- ✅ Given a file_id (e.g., 46), load from offer_files
- ✅ Re-extract text from storage_path with validation (not blank)
- ✅ Split into chunks using smart boundary detection
- ✅ Insert chunks into offer_chunks table
- ✅ Set embeddings_ready = true only if at least 1 chunk stored
- ✅ Wrapped in try/except with error handling
- ✅ Logging with [embedding] tag throughout
- ✅ Admin endpoint: POST /api/qa/reembed-file?file_id=46
- ✅ CLI script for manual/batch operations

---

## 📁 Files Modified/Created

### Modified
1. **`backend/api/routes/qa.py`** (lines 1, 9, 610-845)
   - Added `from pypdf import PdfReader` import
   - Added `_extract_text_from_pdf()` function
   - Added `_chunk_text()` function with smart boundary detection
   - Added `_reembed_file()` core function
   - Added `POST /api/qa/reembed-file` endpoint

### Created
1. **`backend/scripts/reembed_file.py`**
   - Full-featured CLI script
   - Supports single file or batch processing
   - Includes dry-run mode
   - Configurable chunk size and overlap

2. **`backend/tests/test_reembed.py`**
   - Unit tests for text extraction
   - Unit tests for chunking logic
   - Integration tests for re-embedding
   - Manual testing helper

3. **`REEMBED_API.md`**
   - Complete API documentation
   - Usage examples in cURL, Python, TypeScript
   - CLI script documentation
   - Troubleshooting guide

4. **`REEMBED_SUMMARY.md`** (this file)
   - Implementation summary
   - Quick reference

---

## 🔧 Core Functionality

### Text Extraction

Uses PyPDF to extract text from PDF files:
```python
def _extract_text_from_pdf(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)
```

### Chunking Algorithm

Smart chunking with boundary detection:
- **Default**: 1000 characters per chunk, 200 character overlap
- **Configurable**: Adjust via CLI args or can be modified in endpoint
- **Smart boundaries**: Tries to break at paragraphs (`\n\n`), then sentences (`. `, `! `, `? `)
- **Metadata**: Each chunk includes index, start/end positions, and length

```python
def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[dict]:
    # Splits text with smart boundary detection
    # Returns list of dicts with 'text' and 'metadata'
```

### Database Operations

1. **Load file** from `offer_files`
2. **Validate** storage_path exists
3. **Extract** text from PDF
4. **Chunk** text into segments
5. **Delete** existing chunks for file_id
6. **Insert** new chunks
7. **Update** `embeddings_ready` flag

---

## 🌐 API Endpoint

### Quick Start

```bash
curl -X POST "http://localhost:8000/api/qa/reembed-file?file_id=46" \
  -H "X-User-Role: admin"
```

### Response

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

### Authorization

- **Protected**: Only users with `X-User-Role: admin` header can access
- Returns `403 Forbidden` if not admin

---

## 💻 CLI Script

### Basic Usage

```bash
# Single file
python backend/scripts/reembed_file.py --file-id 46

# Entire batch
python backend/scripts/reembed_file.py --batch-id 5

# Dry run
python backend/scripts/reembed_file.py --file-id 46 --dry-run

# Custom parameters
python backend/scripts/reembed_file.py --file-id 46 --chunk-size 1500 --overlap 300
```

### Features

- ✅ Single file processing
- ✅ Batch processing (all files in a batch)
- ✅ Dry-run mode (preview without changes)
- ✅ Configurable chunk size and overlap
- ✅ Detailed progress logging
- ✅ Error handling with graceful failures

---

## 🔍 Logging

All operations log with the `[embedding]` tag:

### Success Flow
```
[embedding] start file_id=46
[embedding] file=offer_document.pdf path=/storage/offers/bt_123/offer_document.pdf
[embedding] extracting text from /storage/offers/bt_123/offer_document.pdf
[embedding] extracted 15420 characters
[embedding] chunking text
[embedding] created 18 chunks
[embedding] deleted 12 existing chunks
[embedding] inserted 18 chunks
[embedding] done file_id=46 chunks=18 ready=True
```

### Error Flow
```
[embedding] start file_id=46
[embedding] file=test.pdf path=/missing/path.pdf
[embedding] error file_id=46: File not found at path: /missing/path.pdf
```

---

## 🧪 Testing

### Run Unit Tests

```bash
python -m pytest backend/tests/test_reembed.py -v
```

Tests cover:
- ✅ Text extraction from PDF
- ✅ Chunking logic (boundaries, overlap, metadata)
- ✅ Re-embedding process
- ✅ Error handling

### Manual Testing

#### 1. Test the API Endpoint
```bash
# Start server
uvicorn app.main:app --reload

# Test endpoint
curl -X POST "http://localhost:8000/api/qa/reembed-file?file_id=46" \
  -H "X-User-Role: admin"
```

#### 2. Test the CLI Script
```bash
# Set environment
export DATABASE_URL="postgresql://user:pass@localhost:5432/dbname"

# Dry run first
python backend/scripts/reembed_file.py --file-id 46 --dry-run

# Actual run
python backend/scripts/reembed_file.py --file-id 46
```

#### 3. Verify Results
```sql
-- Check chunks were created
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

## 🚨 Error Handling

The implementation handles all edge cases:

| Error Scenario | Behavior |
|----------------|----------|
| File ID not found | Returns 404 with clear message |
| storage_path is blank | Returns 500 with "storage_path is blank" |
| File not at path | Returns 500 with file path |
| Text extraction fails | Returns 500 with extraction error |
| Text too short (<10 chars) | Returns 500 with "text too short" |
| No chunks created | Returns 500 with "No chunks created" |
| Database error | Rolls back, sets embeddings_ready=false |
| Unauthorized (API) | Returns 403 Forbidden |

On any error:
- `embeddings_ready` is set to `false`
- Error is logged with `[embedding]` tag
- Transaction is rolled back (no partial updates)

---

## 📊 Example Outputs

### API Success Response
```json
{
  "ok": true,
  "file_id": 46,
  "filename": "health_insurance_offer.pdf",
  "text_length": 15420,
  "chunks_created": 18,
  "chunks_deleted": 12,
  "embeddings_ready": true
}
```

### CLI Success Output
```
[embedding] Connected to database
[embedding] start file_id=46
[embedding] file=health_insurance_offer.pdf path=/storage/offers/bt_abc123/health_insurance_offer.pdf
[embedding] extracting text from /storage/offers/bt_abc123/health_insurance_offer.pdf
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
  "filename": "health_insurance_offer.pdf",
  "text_length": 15420,
  "chunks_created": 18,
  "chunks_deleted": 12,
  "embeddings_ready": true
}
============================================================
```

### CLI Batch Output
```
[embedding] batch start batch_id=5
[embedding] Found 3 files in batch 5

[embedding] Processing health_offer_1.pdf (id=46)
[embedding] done file_id=46 chunks=18 ready=True

[embedding] Processing health_offer_2.pdf (id=47)
[embedding] done file_id=47 chunks=22 ready=True

[embedding] Processing health_offer_3.pdf (id=48)
[embedding] done file_id=48 chunks=15 ready=True

[embedding] batch done batch_id=5 success=3 errors=0

============================================================
BATCH RESULT:
Total files: 3
Success: 3
Errors: 0
============================================================
```

---

## 🎯 Use Cases

### 1. Fix Failed Embeddings
```bash
# Re-process a file that failed during upload
python backend/scripts/reembed_file.py --file-id 46
```

### 2. Batch Re-processing
```bash
# Re-process all files in a batch after fixing storage paths
python backend/scripts/reembed_file.py --batch-id 5
```

### 3. Adjust Chunking Parameters
```bash
# Re-chunk with larger chunks for better context
python backend/scripts/reembed_file.py --file-id 46 --chunk-size 1500 --overlap 300
```

### 4. Preview Changes
```bash
# See what would happen without modifying database
python backend/scripts/reembed_file.py --file-id 46 --dry-run
```

---

## 📚 Documentation

Complete documentation available in:

- **`REEMBED_API.md`** - Full API and CLI reference
  - API endpoint details
  - Request/response examples
  - CLI script usage
  - Troubleshooting guide
  - Best practices

- **`backend/tests/test_reembed.py`** - Test examples
  - Unit tests
  - Integration tests
  - Manual testing helper

- **OpenAPI Docs** - `http://localhost:8000/docs`
  - Interactive Swagger UI
  - Try the endpoint live

---

## 🚀 Deployment

### Prerequisites

1. Database table exists (run migration if needed):
   ```bash
   psql $DATABASE_URL -f backend/scripts/create_offer_chunks_table.sql
   ```

2. PyPDF is installed (already in requirements.txt)

3. Files have valid storage_path in database

### Deploy Steps

1. Pull updated code
2. Restart server: `uvicorn app.main:app --reload`
3. Verify endpoint: `curl http://localhost:8000/docs`

---

## ✨ Summary

The re-embedding feature is **fully implemented** with:

- ✅ Admin API endpoint (`POST /api/qa/reembed-file`)
- ✅ CLI script with batch support
- ✅ Smart text chunking with boundary detection
- ✅ Comprehensive error handling
- ✅ Detailed logging with `[embedding]` tags
- ✅ Unit and integration tests
- ✅ Complete documentation

**Ready to use!** 🎉

### Quick Test

```bash
# API
curl -X POST "http://localhost:8000/api/qa/reembed-file?file_id=46" -H "X-User-Role: admin"

# CLI
python backend/scripts/reembed_file.py --file-id 46
```



