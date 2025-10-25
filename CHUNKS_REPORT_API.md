# Chunks Report API Documentation

## Overview

The `/api/qa/chunks-report` endpoint retrieves document chunks for a given share token, providing visibility into how documents have been chunked for vector search.

## Endpoint

```
GET /api/qa/chunks-report
```

## Authentication & Authorization

**Protected Endpoint**: Accessible only to:
- Users with **admin** role (via `X-User-Role: admin` header)
- Users from the **same organization** as the share (via `X-Org-Id` header)

## Request Parameters

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `share_token` | string | **Yes** | - | The share token identifying the batch |
| `limit` | integer | No | 100 | Maximum chunks to return (1-500) |
| `offset` | integer | No | 0 | Pagination offset |

### Headers

| Header | Type | Required | Description |
|--------|------|----------|-------------|
| `X-Org-Id` | integer | Optional* | Organization ID of requesting user |
| `X-User-Role` | string | Optional* | User role (e.g., "admin", "user") |

*At least one must be provided for authorization

## Response

### Success Response (200 OK)

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
      "text": "This is the first ~200 characters of the chunk text...",
      "metadata": {
        "page": 1,
        "position": "top"
      },
      "created_at": "2025-10-25T12:34:56.789Z",
      "file_id": 42,
      "filename": "offer_document.pdf"
    },
    {
      "chunk_index": 1,
      "text": "This is the second chunk...",
      "metadata": {
        "page": 1,
        "position": "middle"
      },
      "created_at": "2025-10-25T12:34:56.890Z",
      "file_id": 42,
      "filename": "offer_document.pdf"
    }
  ]
}
```

### Error Responses

#### 400 Bad Request
```json
{
  "detail": "share_token is required"
}
```

#### 403 Forbidden
```json
{
  "detail": "Share token expired"
}
```
or
```json
{
  "detail": "Unauthorized: only admin or same organization can access chunks report"
}
```

#### 404 Not Found
```json
{
  "detail": "Share token not found"
}
```

#### 500 Internal Server Error
```json
{
  "detail": "Failed to retrieve chunks report: <error details>"
}
```

## Examples

### cURL Example

```bash
# Basic request with admin role
curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=abc123xyz&limit=10" \
  -H "X-Org-Id: 1" \
  -H "X-User-Role: admin"

# Paginated request
curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=abc123xyz&limit=50&offset=50" \
  -H "X-Org-Id: 1" \
  -H "X-User-Role: admin"

# Request from same organization (non-admin)
curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=abc123xyz" \
  -H "X-Org-Id: 1"
```

### Python Example

```python
import requests

# Configuration
BASE_URL = "http://localhost:8000"
SHARE_TOKEN = "your_share_token_here"

# Request with admin role
response = requests.get(
    f"{BASE_URL}/api/qa/chunks-report",
    params={
        "share_token": SHARE_TOKEN,
        "limit": 100,
        "offset": 0
    },
    headers={
        "X-Org-Id": "1",
        "X-User-Role": "admin"
    }
)

if response.status_code == 200:
    data = response.json()
    print(f"Found {data['total_chunks']} total chunks")
    print(f"Returned {len(data['chunks'])} chunks")
    
    for chunk in data['chunks']:
        print(f"\nChunk {chunk['chunk_index']}:")
        print(f"  File: {chunk['filename']}")
        print(f"  Text: {chunk['text'][:100]}...")
else:
    print(f"Error: {response.status_code}")
    print(response.json())
```

### JavaScript/TypeScript Example

```typescript
interface ChunkData {
  chunk_index: number;
  text: string;
  metadata: Record<string, any>;
  created_at: string | null;
  file_id: number | null;
  filename: string | null;
}

interface ChunksReportResponse {
  ok: boolean;
  share_token: string;
  batch_token: string | null;
  org_id: number;
  total_chunks: number;
  chunks: ChunkData[];
}

async function getChunksReport(
  shareToken: string,
  orgId: number,
  userRole: string = "admin",
  limit: number = 100,
  offset: number = 0
): Promise<ChunksReportResponse> {
  const url = new URL("/api/qa/chunks-report", "http://localhost:8000");
  url.searchParams.set("share_token", shareToken);
  url.searchParams.set("limit", limit.toString());
  url.searchParams.set("offset", offset.toString());

  const response = await fetch(url.toString(), {
    method: "GET",
    headers: {
      "X-Org-Id": orgId.toString(),
      "X-User-Role": userRole,
    },
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }

  return response.json();
}

// Usage
getChunksReport("abc123xyz", 1)
  .then((data) => {
    console.log(`Total chunks: ${data.total_chunks}`);
    data.chunks.forEach((chunk) => {
      console.log(`Chunk ${chunk.chunk_index}: ${chunk.text.substring(0, 50)}...`);
    });
  })
  .catch((error) => {
    console.error("Error:", error);
  });
```

## Implementation Details

### Database Tables Used

The endpoint queries the following tables:
- `public.share_links` - Validates share token and retrieves batch/org info
- `public.offer_batches` - Maps batch token to batch ID
- `public.offer_files` - Gets file records for the batch
- `public.offer_chunks` - Retrieves chunk data

### Query Flow

1. **Validate share token**: Query `share_links` table, check expiration
2. **Check authorization**: Verify user has admin role or same org_id
3. **Get file IDs**: Query `offer_files` joined with `offer_batches` by batch_token
4. **Count chunks**: Get total count from `offer_chunks` for pagination
5. **Fetch chunks**: Retrieve paginated chunks with metadata

### Logging

The endpoint logs with the `[qa]` tag:
- `[qa] chunks-report start` - Request initiated
- `[qa] chunks-report validated share` - Share token validated
- `[qa] chunks-report found N files` - File lookup complete
- `[qa] chunks-report done` - Request completed
- `[qa] chunks-report error` - Error occurred

## Database Schema Requirements

The endpoint expects an `offer_chunks` table with the following structure:

```sql
CREATE TABLE public.offer_chunks (
    id SERIAL PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES public.offer_files(id),
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(file_id, chunk_index)
);

CREATE INDEX idx_offer_chunks_file_id ON public.offer_chunks(file_id);
CREATE INDEX idx_offer_chunks_file_chunk ON public.offer_chunks(file_id, chunk_index);
```

If the table doesn't exist, you'll need to create it or populate it from OpenAI's vector stores.

## Testing

### Automated Tests

Run the test suite:

```bash
cd backend
python -m pytest tests/test_chunks_report.py -v
```

### Manual Testing

1. Start the FastAPI server:
   ```bash
   uvicorn app.main:app --reload
   ```

2. Get a valid share_token from your database or create one via POST `/shares`

3. Test the endpoint:
   ```bash
   curl -X GET "http://localhost:8000/api/qa/chunks-report?share_token=YOUR_TOKEN" \
     -H "X-Org-Id: 1" \
     -H "X-User-Role: admin"
   ```

4. Check the logs for `[qa] chunks-report` messages

### OpenAPI Documentation

Visit `http://localhost:8000/docs` to see the interactive API documentation and test the endpoint through Swagger UI.

## Troubleshooting

### "Share token not found"
- Verify the share_token exists in the `share_links` table
- Check for typos in the token

### "Share token expired"
- Check the `expires_at` field in the `share_links` table
- Create a new share with a longer expiration

### "Unauthorized: only admin or same organization"
- Ensure `X-Org-Id` header matches the share's org_id, OR
- Set `X-User-Role: admin` header

### "No chunks found" (empty array)
- Verify the `offer_chunks` table exists and has data
- Check if files are properly linked to the batch
- Ensure chunks were created when files were uploaded

### Database connection errors
- Verify `DATABASE_URL` environment variable is set
- Check database connectivity
- Ensure required tables exist

## Notes

- The `text` field is truncated to ~200 characters for performance
- Pagination is enforced with a maximum limit of 500 chunks per request
- The endpoint is read-only and does not modify any data
- Metadata structure may vary depending on how chunks were created

