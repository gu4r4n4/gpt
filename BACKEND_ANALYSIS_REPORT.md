# Backend Codebase Analysis Report: OnGo Insurance Platform

**Analysis Date:** 2024-11-20  
**Codebase:** FastAPI + PostgreSQL Insurance Document Processing Backend  
**Version:** 1.0.0  
**Primary Focus:** Health & CASCO Insurance Offer Extraction and Comparison

---

## 📁 Project Structure

```
├── app/                          # Main application package
│   ├── casco/                   # CASCO insurance module (vehicle insurance)
│   │   ├── comparator.py        # Comparison matrix builder for CASCO offers
│   │   ├── extractor.py         # GPT-based PDF extraction for CASCO
│   │   ├── normalizer.py        # Data normalization layer
│   │   ├── persistence.py       # Database access layer for CASCO
│   │   ├── schema.py            # Pydantic models (22-field simplified model)
│   │   └── service.py           # Business logic orchestration
│   ├── extensions/              # Extension modules
│   │   └── pas_sidecar.py       # Batch ingestion background processor
│   ├── routes/                  # FastAPI route handlers
│   │   ├── admin_insurers.py    # Insurer management endpoints
│   │   ├── admin_tc.py          # Terms & Conditions file management
│   │   ├── casco_routes.py      # CASCO upload/comparison endpoints (571 lines)
│   │   ├── debug_db.py          # Database debugging utilities
│   │   ├── ingest.py            # Document ingestion pipeline
│   │   ├── offers_by_documents.py  # Health offers by document ID
│   │   └── translate.py         # Translation service integration
│   ├── services/                # Business service layer
│   │   ├── ingest_offers.py     # Offer ingestion logic
│   │   ├── openai_client.py     # OpenAI API client wrapper
│   │   ├── openai_compat.py     # Compatibility layer for OpenAI SDK
│   │   ├── persist_offers.py    # Offer persistence service
│   │   ├── supabase_storage.py  # Supabase storage integration
│   │   ├── vector_batches.py    # Vector store batch operations
│   │   └── vectorstores.py      # Vector store management
│   ├── gpt_extractor.py         # GPT-based PDF extraction (Health)
│   ├── main.py                  # FastAPI application entry (1626 lines)
│   └── normalizer.py            # Health insurance data normalizer
├── backend/                     # Backend utilities and scripts
│   ├── api/routes/              # Additional API routes
│   │   ├── batches.py           # Batch management API
│   │   ├── offers_upload.py     # Health offer upload endpoints
│   │   ├── qa.py                # Q&A RAG system endpoints
│   │   ├── tc.py                # T&C document endpoints
│   │   └── util.py              # Shared utilities
│   ├── scripts/                 # Database migrations & utilities
│   │   ├── *.sql                # SQL migration scripts
│   │   ├── create_vector_store.py  # Vector store initialization
│   │   ├── expire_and_cleanup_batches.py  # Batch cleanup cron job
│   │   └── reembed_file.py      # Re-embedding utility
│   └── tests/                   # Unit tests
├── requirements.txt             # Python dependencies
├── package.json                 # Node.js dev dependencies
├── Dockerfile                   # Docker containerization
└── Makefile                     # Build automation
```

### Directory Purpose Summary

**`app/`** - Main application code. FastAPI application, routing, and core business logic.

**`app/casco/`** - Complete CASCO (vehicle) insurance module with extraction, normalization, comparison, and persistence layers. Uses a simplified 22-field model with Latvian field names.

**`app/routes/`** - FastAPI route handlers organized by domain (CASCO, admin, translation, etc.).

**`app/services/`** - Business service layer handling OpenAI integration, database persistence, Supabase storage, and vector store operations.

**`backend/api/routes/`** - Additional API routes for Health insurance (offers, batches, Q&A, T&C documents).

**`backend/scripts/`** - Database migrations, maintenance scripts, and utilities for vector store management.

**`backend/tests/`** - Unit and integration tests for core functionality.

---

## 🛠 Technology Stack

| Technology | Version | Purpose |
|------------|---------|---------|
| **Python** | 3.11 | Primary programming language |
| **FastAPI** | 0.111.0 | Web framework for REST API |
| **Uvicorn** | 0.30.0 | ASGI server |
| **PostgreSQL** | N/A | Primary database (via psycopg2) |
| **Supabase** | 2.7.4 | Backend-as-a-Service (storage, auth) |
| **OpenAI** | 1.52.0 | GPT-4 for PDF extraction |
| **Pydantic** | (via FastAPI) | Data validation and serialization |
| **psycopg2-binary** | 2.9.9 | PostgreSQL adapter |
| **SQLAlchemy** | 2.0.36 | ORM and query builder |
| **pypdf** | 4.2.0 | PDF text extraction |
| **httpx** | 0.27.0 | Async HTTP client |
| **Docker** | N/A | Containerization |
| **Node.js/TypeScript** | N/A | Dev tooling only (scripts) |

### Key Features
- **Dual Product Lines:** Health and CASCO (vehicle) insurance
- **AI-Powered Extraction:** OpenAI GPT-4 for structured data extraction from PDFs
- **RAG System:** Q&A over insurance documents using vector stores
- **Multi-Tenancy:** Organization-level isolation with `org_id`/`user_id` context
- **Job-Based Architecture:** Both Health and CASCO use UUID-based job tracking
- **Share Links:** Shareable comparison views with expiration and tracking
- **Batch Processing:** Background batch ingestion with ThreadPoolExecutor

---

## 🏗 Architecture

### Overall Architecture Pattern
**Layered Architecture** with clear separation of concerns:

```
┌─────────────────────────────────────────┐
│         FastAPI Routes (API Layer)      │
│  (casco_routes.py, offers_upload.py)    │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│       Service Layer (Business Logic)    │
│  (service.py, ingest_offers.py)         │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│    Persistence Layer (Data Access)      │
│  (persistence.py, direct SQL queries)   │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│         PostgreSQL Database             │
│  (offers, offers_casco, offer_files,    │
│   casco_jobs, share_links, etc.)        │
└─────────────────────────────────────────┘
```

### Key Architectural Patterns

#### 1. **Job-Based Grouping (CASCO)**
CASCO uses internal UUID job IDs to group offers from a single upload:

```python
# app/routes/casco_routes.py (line 42-59)
def _create_casco_job_sync(conn, reg_number: str) -> str:
    """Create a new CASCO job with UUID identifier."""
    job_id = str(uuid.uuid4())
    
    sql = """
    INSERT INTO public.casco_jobs (casco_job_id, reg_number, product_line)
    VALUES (%s, %s, 'casco')
    RETURNING casco_job_id;
    """
    
    with conn.cursor() as cur:
        cur.execute(sql, (job_id, reg_number))
        row = cur.fetchone()
        conn.commit()
        return row["casco_job_id"]
```

**Response Format:**
```json
{
  "success": true,
  "casco_job_id": "550e8400-e29b-41d4-a716-446655440000",
  "offer_ids": [123, 124, 125],
  "total_offers": 3
}
```

#### 2. **GPT-Based Extraction Pipeline**
Unified extraction flow for both Health and CASCO:

```python
# app/casco/service.py
def process_casco_pdf(file_bytes: bytes, insurer_name: str, pdf_filename: str):
    """
    1. Extract text from PDF (pypdf)
    2. Build GPT prompt with extraction rules
    3. Call OpenAI with structured output
    4. Parse JSON response
    5. Return normalized CascoCoverage objects
    """
    text = extract_text_from_pdf(file_bytes)
    coverage_list = extract_casco_offers_from_text(text, insurer_name, pdf_filename)
    return [CascoExtractionResult(coverage=c, raw_text=text) for c in coverage_list]
```

#### 3. **Comparison Matrix Builder**
CASCO comparison matrix uses a row-column-value structure:

```python
# app/casco/comparator.py
def build_casco_comparison_matrix(raw_offers: List[Dict]) -> Dict:
    """
    Build comparison matrix from raw offers.
    
    Returns:
    {
      "rows": [22 CascoComparisonRow objects],
      "columns": ["BALTA", "BALCIA", "IF"],
      "values": {
        "premium_total::BALTA": "1480.00",
        "Bojājumi::BALTA": "v",
        ...
      },
      "metadata": {
        "BALTA": {"insurer_name": "BALTA", "reg_number": "LX1234", ...}
      }
    }
    """
```

#### 4. **Multi-Tenant Context Management**
Request context extracted from headers:

```python
# app/main.py (line 72-90)
async def resolve_request_context(
    request: Request,
    x_org_id: Optional[int] = Header(None),
    x_user_id: Optional[int] = Header(None)
) -> Tuple[int, int]:
    """Extract org_id and user_id from headers or form."""
    org_id = _coalesce_int(x_org_id, request.headers.get("x-org-id"))
    user_id = _coalesce_int(x_user_id, request.headers.get("x-user-id"))
    
    if org_id is None or user_id is None:
        raise HTTPException(400, "Missing org_id or user_id")
    return org_id, user_id
```

#### 5. **Share Link System**
Shareable comparison views with product line support:

```python
# app/main.py (line 1303-1333)
if product_line == "casco":
    casco_job_id = payload.get("casco_job_id")
    if casco_job_id:
        raw_offers = _fetch_casco_offers_by_job_sync(conn, casco_job_id)
        comparison = build_casco_comparison_matrix(raw_offers)
        return {
            "offers": raw_offers,
            "comparison": comparison,
            "product_line": "casco",
            # ...
        }
# ... existing HEALTH logic ...
```

### Database Schema Highlights

**Key Tables:**
- `offers` - Health insurance offers
- `offers_casco` - CASCO insurance offers
- `casco_jobs` - CASCO job tracking (UUID PK)
- `offer_files` - Uploaded PDF files with metadata
- `offer_batches` - Upload batch tracking
- `share_links` - Shareable comparison links
- `offer_chunks` - RAG system text chunks
- `org_vector_stores` - Organization-specific vector stores

---

## 🎨 Styling and UI

**N/A - Backend Only**

This is a pure backend API service. UI/styling concerns are handled by the separate frontend application.

API follows RESTful conventions:
- JSON request/response bodies
- Standard HTTP status codes
- CORS enabled for cross-origin requests
- Consistent error response format

---

## ✅ Code Quality and Testing

### Linting & Formatting
**Status:** ⚠️ **Partial**

- ✅ No `eval()`, `exec()`, or dangerous dynamic code execution detected
- ✅ Parameterized SQL queries used consistently (no SQL injection vulnerabilities)
- ⚠️ No linting configuration found (missing `.pylintrc`, `pyproject.toml`, or `setup.cfg`)
- ⚠️ No formatting tool configuration (Black, autopep8, etc.)

### Type Safety
**Status:** ⚠️ **Partial**

- ✅ Pydantic models used extensively for request/response validation
- ✅ Type hints present in key functions
- ⚠️ Type hints inconsistent across codebase
- ⚠️ No `mypy` or type checker configuration

**Example - Good Type Usage:**
```python
# app/casco/persistence.py
@dataclass
class CascoOfferRecord:
    insurer_name: str
    reg_number: str
    casco_job_id: str  # UUID string
    insured_amount: Optional[Decimal] = None
    # ...
```

### Test Coverage
**Status:** ⚠️ **Minimal**

Test files exist in `backend/tests/`:
- `test_chunks_report.py`
- `test_qa_sources.py`
- `test_reembed.py`
- `test_upload_smoke.py`

**Issues:**
- ⚠️ No test runner configuration (pytest.ini, tox.ini)
- ⚠️ No CI/CD pipeline configuration
- ⚠️ Test coverage appears limited (only 5 test files for a large codebase)
- ⚠️ No integration tests for CASCO module (recently refactored)

### Documentation
**Status:** ⚠️ **Good for CASCO, Sparse Elsewhere**

- ✅ Extensive CASCO documentation (30+ MD files in root)
- ✅ Docstrings present in most functions
- ✅ API endpoints have description strings
- ⚠️ No centralized API documentation (Swagger/OpenAPI)
- ⚠️ No developer onboarding guide
- ⚠️ README.md missing

### Error Handling
**Status:** ✅ **Good**

- ✅ 79 exception handlers across 15 files
- ✅ HTTPException used for API errors
- ✅ Try-except blocks present for external service calls
- ✅ Graceful fallbacks for optional dependencies (Supabase)

**Example:**
```python
# app/casco/extractor.py
try:
    response = client.chat.completions.create(...)
    raw_content = response.choices[0].message.content
    parsed = json.loads(raw_content)
except json.JSONDecodeError as e:
    raise ExtractionError(f"JSON parse failed: {e}")
except Exception as e:
    raise ExtractionError(f"GPT call failed: {e}")
```

---

## 🔧 Key Components

### 1. CASCO Upload Handler (`app/routes/casco_routes.py`)

**Purpose:** Handles CASCO PDF upload, extraction, and persistence with job-based grouping.

**Key Features:**
- Creates UUID job for each upload
- Processes single or batch uploads
- Returns `casco_job_id` for comparison
- Removes dependency on `inquiry_id`

**Usage Example:**
```python
@router.post("/upload")
async def upload_casco_offer(
    file: UploadFile,
    insurer_name: str = Form(...),
    reg_number: str = Form(...),
    conn = Depends(get_db),
):
    # 1. Create job
    casco_job_id = _create_casco_job_sync(conn, reg_number)
    
    # 2. Extract from PDF
    pdf_bytes = await file.read()
    extraction_results = process_casco_pdf(pdf_bytes, insurer_name, file.filename)
    
    # 3. Save offers
    for result in extraction_results:
        offer_record = CascoOfferRecord(
            casco_job_id=casco_job_id,
            coverage=result.coverage,
            # ...
        )
        offer_id = _save_casco_offer_sync(conn, offer_record)
        inserted_ids.append(offer_id)
    
    return {
        "success": True,
        "casco_job_id": casco_job_id,
        "offer_ids": inserted_ids,
        "total_offers": len(inserted_ids)
    }
```

**API:**
- `POST /casco/upload` - Single file upload
- `POST /casco/upload/batch` - Multi-file upload
- `GET /casco/job/{casco_job_id}/compare` - Comparison matrix
- `GET /casco/job/{casco_job_id}/offers` - Raw offers
- `GET /casco/vehicle/{reg}/compare` (deprecated)
- `GET /casco/vehicle/{reg}/offers` (deprecated)

**Dependencies:**
- `process_casco_pdf()` - Service layer
- `build_casco_comparison_matrix()` - Comparator
- `CascoOfferRecord` - Persistence layer

---

### 2. GPT Extractor (`app/casco/extractor.py`)

**Purpose:** OpenAI GPT-4 integration for structured data extraction from insurance PDFs.

**Key Features:**
- Custom system prompts for CASCO extraction
- Structured JSON output parsing
- Retry logic for API failures
- Key mapping for special characters (Latvian field names)

**Usage Example:**
```python
def extract_casco_offers_from_text(
    text: str,
    insurer_name: str,
    pdf_filename: str
) -> List[CascoCoverage]:
    """
    Extract CASCO coverage data using GPT-4.
    
    Returns 22-field CascoCoverage objects (19 coverage + 3 financial).
    """
    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(text, insurer_name, pdf_filename)
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.1
    )
    
    raw_json = response.choices[0].message.content
    parsed = json.loads(raw_json)
    
    # Map keys with special chars
    normalized = _normalize_json_keys(parsed)
    
    return [CascoCoverage(**normalized)]
```

**GPT Prompt Structure:**
```
You are a CASCO insurance expert. Extract the following 22 fields:

1. Premium Total: Look for "Kopējā prēmija", "1 maksājums", ...
2. Insured Amount: Look for "Apdrošinājuma summa", ...
3. Period: ALWAYS return "12 mēneši"
4-22. Coverage fields: Return "v" (covered), "-" (not covered), or specific value

Return ONLY valid JSON with these exact keys: {...}
```

**Dependencies:**
- OpenAI Python SDK 1.52.0
- `CascoCoverage` schema
- Custom key mapping for Latvian characters

---

### 3. Comparison Builder (`app/casco/comparator.py`)

**Purpose:** Builds comparison matrix from raw CASCO offers for frontend table rendering.

**Key Features:**
- 22-row structure (19 coverage + 3 financial)
- Column-per-insurer layout
- Metadata extraction (reg number, dates)
- Consistent ordering

**Usage Example:**
```python
def build_casco_comparison_matrix(raw_offers: List[Dict]) -> Dict:
    """
    Build comparison matrix.
    
    Input: Raw offers from database (with JSONB coverage field)
    Output: Structured comparison object
    """
    rows = CASCO_COMPARISON_ROWS  # 22 predefined rows
    columns = [offer["insurer_name"] for offer in raw_offers]
    
    values = {}
    metadata = {}
    
    for offer in raw_offers:
        insurer = offer["insurer_name"]
        coverage = offer.get("coverage", {})
        
        # Extract metadata
        metadata[insurer] = {
            "insurer_name": insurer,
            "reg_number": offer.get("reg_number"),
            "premium_total": offer.get("premium_total"),
            "period": coverage.get("period", "12 mēneši")
        }
        
        # Map field values
        for row in rows:
            key = f"{row['code']}::{insurer}"
            values[key] = coverage.get(row["code"], "-")
    
    return {
        "rows": [row.dict() for row in rows],
        "columns": columns,
        "values": values,
        "metadata": metadata
    }
```

**Output Format:**
```json
{
  "rows": [
    {"code": "premium_total", "label": "Kopējā prēmija", "group": "financial", "type": "number"},
    {"code": "Bojājumi", "label": "Bojājumi", "group": "core", "type": "text"},
    ...
  ],
  "columns": ["BALTA", "BALCIA", "IF"],
  "values": {
    "premium_total::BALTA": "1480.00",
    "Bojājumi::BALTA": "v",
    "Bojājumi::BALCIA": "v",
    ...
  },
  "metadata": {
    "BALTA": {"reg_number": "LX1234", "premium_total": 1480.00, ...}
  }
}
```

**Dependencies:**
- `CASCO_COMPARISON_ROWS` from schema.py
- Raw offers from database

---

### 4. Share Link System (`app/main.py`)

**Purpose:** Generate and retrieve shareable comparison links with product line support.

**Key Features:**
- Token-based access (URL-safe random tokens)
- Expiration support
- View/edit tracking
- Product line differentiation (Health vs CASCO)
- CASCO job ID support

**Usage Example:**
```python
# Create share
@app.post("/shares")
def create_share_token_only(body: ShareCreateBody, request: Request):
    token = _gen_token()  # secrets.token_urlsafe(16)
    
    payload = {
        "mode": "snapshot",
        "title": body.title,
        "product_line": body.product_line or "health",
        "casco_job_id": body.casco_job_id,  # UUID for CASCO
        # ...
    }
    
    row = {
        "token": token,
        "payload": payload,
        "expires_at": expires_at,
        "org_id": org_id,
        "product_line": body.product_line or "health"
    }
    
    _supabase.table("share_links").insert(row).execute()
    
    return {"ok": True, "token": token, "url": f"/share/{token}"}

# Retrieve share
@app.get("/shares/{token}")
def get_share_token_only(token: str, request: Request):
    share = _load_share_record(token)
    payload = share.get("payload", {})
    product_line = payload.get("product_line", "health")
    
    # Handle CASCO shares
    if product_line == "casco":
        casco_job_id = payload.get("casco_job_id")
        raw_offers = _fetch_casco_offers_by_job_sync(conn, casco_job_id)
        comparison = build_casco_comparison_matrix(raw_offers)
        return {"offers": raw_offers, "comparison": comparison, ...}
    
    # Handle HEALTH shares
    # ... existing logic ...
```

**API:**
- `POST /shares` - Create share link
- `GET /shares/{token}` - Retrieve share content
- `POST /shares/{token}` - Update share metadata

**Dependencies:**
- `share_links` table in database
- `_fetch_casco_offers_by_job_sync()` for CASCO
- `build_casco_comparison_matrix()`

---

### 5. Q&A RAG System (`backend/api/routes/qa.py`)

**Purpose:** Retrieval-Augmented Generation for answering questions about insurance documents.

**Key Features:**
- Vector store per organization
- OpenAI embeddings + GPT-4 generation
- Source citation with page numbers
- Chunk-based retrieval

**Usage Example:**
```python
@router.post("/qa")
async def ask_question(body: QARequest):
    # 1. Get vector store for org
    vector_store = get_or_create_vector_store(body.org_id)
    
    # 2. Retrieve relevant chunks
    chunks = vector_store.similarity_search(body.question, k=5)
    
    # 3. Build context from chunks
    context = "\n\n".join([chunk.page_content for chunk in chunks])
    
    # 4. Generate answer with GPT
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Answer based on context only."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {body.question}"}
        ]
    )
    
    answer = response.choices[0].message.content
    
    # 5. Extract sources
    sources = [{"filename": c.metadata["filename"], "page": c.metadata["page"]} 
               for c in chunks]
    
    return {"answer": answer, "sources": sources}
```

**Dependencies:**
- `org_vector_stores` table
- `offer_chunks` table
- OpenAI embeddings + completions
- Vector store library (LangChain/custom)

---

## 🧩 Patterns and Best Practices

### 1. Dependency Injection
FastAPI's dependency injection used for database connections:

```python
def get_db():
    db_url = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

@router.post("/upload")
async def upload_casco_offer(
    file: UploadFile,
    conn = Depends(get_db),  # ✅ Injected dependency
):
    # Use conn...
```

### 2. Graceful Fallbacks
Optional dependencies handled with try-except:

```python
try:
    from supabase import create_client, Client
except Exception:
    create_client = None
    Client = None

# Later...
_supabase: Optional[Client] = None
if _SUPABASE_URL and _SUPABASE_KEY and create_client is not None:
    try:
        _supabase = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    except Exception:
        _supabase = None
```

### 3. Parameterized SQL Queries
Consistent use of placeholders prevents SQL injection:

```python
# ✅ GOOD - Parameterized
cur.execute(
    "SELECT * FROM offers_casco WHERE casco_job_id = %s AND product_line = %s",
    (job_id, "casco")
)

# ❌ BAD - Would be vulnerable (not found in codebase)
# cur.execute(f"SELECT * FROM offers WHERE id = {user_input}")
```

### 4. Hybrid Data Storage
Stores both structured and raw data for auditability:

```python
@dataclass
class CascoOfferRecord:
    coverage: CascoCoverage  # Structured (Pydantic)
    raw_text: Optional[str]  # Raw PDF text
    # ...

# Database stores both:
# coverage JSONB - structured data for queries
# raw_text TEXT - full PDF content for audit trail
```

### 5. ThreadPoolExecutor for Batch Processing
Background processing for expensive operations:

```python
EXTRACT_WORKERS = int(os.getenv("EXTRACT_WORKERS", "4"))
EXEC: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=EXTRACT_WORKERS)

# Usage:
future = EXEC.submit(extract_offer_from_pdf_bytes, pdf_bytes)
results = future.result()
```

### 6. Idempotent Operations
Database operations designed to be safely retried:

```python
# app/services/ingest_offers.py
with engine.begin() as conn:
    # Delete existing before inserting (idempotent)
    conn.execute(text("DELETE FROM offers WHERE filename = :f"), {"f": filename})
    
    # Insert new data
    for program in programs:
        conn.execute(text("INSERT INTO offers (...)"), {...})
```

### 7. Request Context Propagation
Org/user context extracted and passed through call stack:

```python
# 1. Extract from headers
org_id, user_id = await resolve_request_context(request)

# 2. Pass to service layer
batch_token, batch_id = create_offer_batch(org_id, user_id, title)

# 3. Store in database
cur.execute("INSERT INTO offer_batches (org_id, created_by_user_id, ...)")
```

---

## ⚙️ Development Infrastructure

### Package Scripts
```json
{
  "scripts": {
    "dev": "uvicorn app.main:app --reload --host 0.0.0.0 --port 8000",
    "start": "uvicorn app.main:app --host 0.0.0.0 --port 8000",
    "create:vector-stores": "tsx backend/scripts/create-vector-stores.ts"
  }
}
```

### Makefile Targets
```makefile
cleanup-batches:
    python backend/scripts/expire_and_cleanup_batches.py

create-vector-store:
    export ORG_ID=$(ORG_ID) && python backend/scripts/create_vector_store.py
```

### Docker Configuration
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y build-essential
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Issues:**
- ⚠️ No multi-stage build (larger image size)
- ⚠️ Installs build tools but doesn't clean up
- ⚠️ No health check configured
- ⚠️ Runs as root (security concern)

### Environment Variables
Required environment variables:
- `DATABASE_URL` - PostgreSQL connection string
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_ANON_KEY` or `SUPABASE_SERVICE_ROLE_KEY` - Auth key
- `OPENAI_API_KEY` - OpenAI API key
- `GPT_MODEL` - Model name (default: gpt-4o-mini)
- `DEFAULT_ORG_ID` - Fallback organization ID
- `DEFAULT_USER_ID` - Fallback user ID
- `EXTRACT_WORKERS` - ThreadPoolExecutor worker count (default: 4)

**Issues:**
- ⚠️ No `.env.example` file
- ⚠️ No validation that required vars are set on startup
- ⚠️ Sensitive keys could be exposed in logs

### CI/CD
**Status:** ❌ **Missing**

- ❌ No GitHub Actions workflows
- ❌ No GitLab CI configuration
- ❌ No automated testing on PR
- ❌ No automated deployments
- ❌ No pre-commit hooks

### Database Migrations
**Status:** ⚠️ **Manual SQL Scripts**

Migration files in `backend/scripts/`:
- `create_casco_jobs_table.sql`
- `create_offer_chunks_table.sql`
- `create_offers_casco_table.sql`
- `add_share_links_stats_columns.sql`

**Issues:**
- ⚠️ No migration tool (Alembic, Flyway)
- ⚠️ No versioning or rollback capability
- ⚠️ Manual execution required
- ⚠️ No migration history tracking

---

## ⚠️ Bug & Issue Report

### 🔴 Critical Issues

#### 1. SQL Query String Formatting in admin_tc.py
**File:** `app/routes/admin_tc.py`  
**Lines:** 266-267, 294

**Problem:** Using f-string formatting in SQL queries with user-controlled input.

```python
# Line 266
cur.execute(f"""
    SELECT id, org_id, filename, insurer_code, product_line,
           to_char(effective_from, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as effective_from,
    ...
    WHERE org_id={org_id} AND insurer_code={insurer_code}
    ...
""")

# Line 294
cur.execute(f"UPDATE public.offer_files SET {', '.join(sets)} WHERE id=%s RETURNING id", (*vals, id))
```

**Risk:** Potential SQL injection if org_id/insurer_code are not properly validated.

**Suggested Fix:**
```python
# Use parameterized queries
cur.execute("""
    SELECT id, org_id, filename, insurer_code, product_line,
           to_char(effective_from, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as effective_from,
    ...
    WHERE org_id=%s AND insurer_code=%s
    ...
""", (org_id, insurer_code))
```

---

#### 2. Missing Database Connection Error Handling
**File:** `app/main.py`  
**Line:** 92-97

**Problem:** `get_db_connection()` raises RuntimeError but doesn't handle connection failures.

```python
def get_db_connection():
    import psycopg2
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url)  # No error handling
```

**Risk:** Unhandled `psycopg2.OperationalError` crashes the application.

**Suggested Fix:**
```python
def get_db_connection():
    import psycopg2
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    
    try:
        return psycopg2.connect(db_url)
    except psycopg2.OperationalError as e:
        raise HTTPException(503, f"Database unavailable: {e}")
```

---

#### 3. Deprecated Endpoints Still Functional
**File:** `app/routes/casco_routes.py`  
**Lines:** 484, 550

**Problem:** Deprecated endpoints `/vehicle/{reg}/compare` and `/vehicle/{reg}/offers` still return data.

```python
@router.get("/vehicle/{reg_number}/compare", deprecated=True)
async def casco_compare_by_vehicle(reg_number: str, ...):
    # Still fully functional - fetches across multiple jobs
```

**Risk:** Frontend might still use deprecated endpoints, causing confusion.

**Suggested Fix:**
```python
@router.get("/vehicle/{reg_number}/compare", deprecated=True)
async def casco_compare_by_vehicle(reg_number: str, ...):
    raise HTTPException(
        410,
        "This endpoint is deprecated. Use GET /casco/job/{casco_job_id}/compare instead."
    )
```

---

### 🟡 High Priority Issues

#### 4. No Input Validation for File Uploads
**File:** `app/routes/casco_routes.py`  
**Line:** 229

**Problem:** Only checks file extension, not actual file content or size.

```python
if not file.filename.lower().endswith('.pdf'):
    raise HTTPException(400, "Only PDF files are supported")
```

**Risk:** Could upload malicious files with `.pdf` extension.

**Suggested Fix:**
```python
# Check file size
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
pdf_bytes = await file.read()
if len(pdf_bytes) > MAX_FILE_SIZE:
    raise HTTPException(413, "File too large (max 50MB)")

# Validate PDF magic bytes
if not pdf_bytes.startswith(b'%PDF'):
    raise HTTPException(400, "Invalid PDF file")
```

---

#### 5. Race Condition in Job Creation
**File:** `app/routes/casco_routes.py`  
**Line:** 42-59

**Problem:** UUID generation and insertion not atomic.

```python
job_id = str(uuid.uuid4())
sql = "INSERT INTO public.casco_jobs (...) VALUES (%s, ...)"
cur.execute(sql, (job_id, ...))
conn.commit()
return job_id
```

**Risk:** Duplicate UUID (extremely unlikely but possible).

**Suggested Fix:**
```python
# Use database-generated UUID
sql = """
INSERT INTO public.casco_jobs (casco_job_id, reg_number, product_line)
VALUES (gen_random_uuid()::text, %s, 'casco')
RETURNING casco_job_id;
"""
cur.execute(sql, (reg_number,))
row = cur.fetchone()
conn.commit()
return row["casco_job_id"]
```

---

#### 6. Hardcoded Thread Pool Size
**File:** `app/main.py`  
**Line:** 120-121

**Problem:** ThreadPoolExecutor size set at module level, can't adjust at runtime.

```python
EXTRACT_WORKERS = int(os.getenv("EXTRACT_WORKERS", "4"))
EXEC: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=EXTRACT_WORKERS)
```

**Risk:** Insufficient workers under high load, too many workers waste resources.

**Suggested Fix:**
```python
# Use ProcessPoolExecutor for CPU-bound PDF extraction
from concurrent.futures import ProcessPoolExecutor

def get_executor():
    workers = int(os.getenv("EXTRACT_WORKERS", "4"))
    return ProcessPoolExecutor(max_workers=workers)

# Or implement adaptive pool sizing
```

---

#### 7. Missing CORS Origin Validation
**File:** `app/main.py`  
**Line:** 140-148

**Problem:** CORS allows all origins (`allow_origins=["*"]`).

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ Allows any origin
    allow_credentials=True,
    # ...
)
```

**Risk:** CSRF attacks, unauthorized API access.

**Suggested Fix:**
```python
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    # ...
)
```

---

### 🟢 Medium Priority Issues

#### 8. No Rate Limiting
**File:** All routes

**Problem:** No rate limiting on API endpoints.

**Risk:** DoS attacks, OpenAI API bill explosion.

**Suggested Fix:**
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@router.post("/upload")
@limiter.limit("10/minute")
async def upload_casco_offer(...):
    # ...
```

---

#### 9. Large In-Memory Dictionaries
**File:** `app/main.py`  
**Lines:** 168-171

**Problem:** Global dictionaries for jobs/results can grow unbounded.

```python
_jobs: Dict[str, Dict[str, Any]] = {}
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}
_SHARES_FALLBACK: Dict[str, Dict[str, Any]] = {}
_INSERTED_IDS: Dict[str, List[int]] = {}
```

**Risk:** Memory leak under high load.

**Suggested Fix:**
```python
from cachetools import TTLCache

# Auto-expire after 1 hour
_jobs = TTLCache(maxsize=1000, ttl=3600)
_LAST_RESULTS = TTLCache(maxsize=1000, ttl=3600)
```

---

#### 10. No Logging Configuration
**File:** All files

**Problem:** Logging uses print statements instead of proper logging.

```python
# Found in multiple files
print(f"[warn] Failed to fetch CASCO offers: {e}")
```

**Risk:** No log levels, no structured logging, difficult debugging.

**Suggested Fix:**
```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)
logger.warning(f"Failed to fetch CASCO offers: {e}")
```

---

#### 11. Missing API Versioning
**File:** All route files

**Problem:** No API versioning strategy.

**Risk:** Breaking changes affect all clients.

**Suggested Fix:**
```python
# Add version prefix
app.include_router(casco_router, prefix="/api/v1/casco")
app.include_router(offers_upload_router, prefix="/api/v1/offers")
```

---

#### 12. No Request ID Tracing
**File:** All routes

**Problem:** No request correlation IDs for debugging.

**Risk:** Difficult to trace requests across logs.

**Suggested Fix:**
```python
from fastapi import Request
import uuid

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```

---

#### 13. Hardcoded OpenAI Model Names
**File:** `app/casco/extractor.py`

**Problem:** Model names hardcoded in code.

```python
response = client.chat.completions.create(
    model="gpt-4o",  # Hardcoded
    # ...
)
```

**Risk:** Difficult to switch models, no A/B testing.

**Suggested Fix:**
```python
GPT_MODEL = os.getenv("GPT_EXTRACTION_MODEL", "gpt-4o")

response = client.chat.completions.create(
    model=GPT_MODEL,
    # ...
)
```

---

### 🔵 Low Priority Issues

#### 14. Missing Type Hints in Many Functions
**File:** Multiple files

**Problem:** Inconsistent type hints.

**Example:**
```python
# Missing return type
def _gen_token():
    return secrets.token_urlsafe(16)

# Should be:
def _gen_token() -> str:
    return secrets.token_urlsafe(16)
```

**Suggested Fix:** Add type hints systematically, run `mypy`.

---

#### 15. No Health Check Endpoint Details
**File:** `app/main.py`  
**Line:** 194-201

**Problem:** Health check doesn't verify dependencies.

```python
@app.get("/healthz")
def healthz():
    return {"ok": True, "app": APP_NAME, ...}
```

**Suggested Fix:**
```python
@app.get("/healthz")
def healthz():
    checks = {
        "database": check_database_connection(),
        "openai": check_openai_api(),
        "supabase": bool(_supabase),
    }
    
    ok = all(checks.values())
    status_code = 200 if ok else 503
    
    return JSONResponse(
        content={"ok": ok, "checks": checks, ...},
        status_code=status_code
    )
```

---

#### 16. Commented Out Code
**Problem:** (Not found - good!)

No commented-out code detected.

---

#### 17. No API Documentation Generation
**File:** `app/main.py`

**Problem:** No Swagger UI customization or API documentation.

**Suggested Fix:**
```python
app = FastAPI(
    title="OnGo Insurance API",
    version="1.0.0",
    description="AI-powered insurance document processing and comparison",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "CASCO", "description": "Vehicle insurance operations"},
        {"name": "Health", "description": "Health insurance operations"},
        {"name": "Admin", "description": "Administrative endpoints"},
    ]
)
```

---

## 📋 Summary & Recommendations

### Strengths

✅ **Well-Structured Architecture**
- Clear separation of concerns (routes, services, persistence)
- Consistent patterns across modules
- Good use of dependency injection

✅ **Robust CASCO Implementation**
- Complete job-based architecture matching HEALTH
- UUID-based job tracking
- Comprehensive comparison matrix builder
- Extensive documentation (30+ markdown files)

✅ **Security-Conscious SQL**
- Consistent use of parameterized queries
- No eval/exec detected
- No obvious SQL injection vulnerabilities (except 2 cases in admin_tc.py)

✅ **Good Error Handling**
- 79 exception handlers across codebase
- Graceful fallbacks for optional dependencies
- HTTPException used consistently

✅ **Modern Tech Stack**
- FastAPI (high performance, auto-validation)
- Pydantic (type safety)
- OpenAI GPT-4 (state-of-the-art extraction)
- PostgreSQL (reliable, scalable)

### Weaknesses

❌ **Limited Test Coverage**
- Only 5 test files for large codebase
- No CI/CD pipeline
- No integration tests for CASCO module

❌ **Production Readiness Gaps**
- No rate limiting
- No request tracing
- CORS allows all origins
- No structured logging
- Missing environment variable validation

❌ **Database Management**
- Manual SQL migrations (no Alembic/Flyway)
- No rollback capability
- No migration history tracking

❌ **Code Quality Tools**
- No linting configuration
- No type checker (mypy)
- No code formatter (Black/autopep8)
- Inconsistent type hints

❌ **Operational Gaps**
- No health check for dependencies
- No metrics/monitoring instrumentation
- In-memory dictionaries can leak memory
- No API versioning strategy

### Recommendations

#### 🔴 Critical (Do Immediately)

1. **Fix SQL Injection Risks**
   - Replace f-strings with parameterized queries in `admin_tc.py`
   - Add input validation for all user-controlled parameters

2. **Implement Rate Limiting**
   - Use `slowapi` or similar
   - Protect upload endpoints (10 req/min)
   - Protect OpenAI endpoints (5 req/min)

3. **Fix CORS Configuration**
   - Replace `allow_origins=["*"]` with whitelist
   - Set via environment variable

4. **Add Database Connection Error Handling**
   - Wrap `psycopg2.connect()` with try-except
   - Return 503 on connection failure

#### 🟡 High Priority (Within 2 Weeks)

5. **Add Structured Logging**
   - Replace print statements with `logging`
   - Add request correlation IDs
   - Configure log levels per environment

6. **Implement Request Tracing**
   - Add middleware for request IDs
   - Include in all log messages
   - Return in `X-Request-ID` header

7. **Add Input Validation**
   - Validate file sizes (max 50MB)
   - Check PDF magic bytes
   - Sanitize filenames

8. **Set Up CI/CD**
   - GitHub Actions workflow
   - Run tests on PR
   - Auto-deploy to staging

9. **Add Comprehensive Tests**
   - Integration tests for CASCO upload flow
   - Unit tests for comparator
   - E2E tests for share links

#### 🟢 Medium Priority (Within 1 Month)

10. **Database Migration Tool**
    - Implement Alembic
    - Version all migrations
    - Add rollback scripts

11. **Add Monitoring**
    - Prometheus metrics
    - Health check for dependencies
    - APM integration (Sentry/New Relic)

12. **Memory Management**
    - Replace global dicts with TTL caches
    - Implement proper cleanup
    - Monitor memory usage

13. **API Versioning**
    - Add `/api/v1/` prefix
    - Document deprecation policy
    - Support multiple versions

14. **Code Quality Tools**
    - Add `mypy` for type checking
    - Configure `Black` for formatting
    - Set up `pylint` or `ruff`

#### 🔵 Low Priority (Nice to Have)

15. **Improve Docker Image**
    - Multi-stage build
    - Non-root user
    - Health check directive

16. **Add API Documentation**
    - Customize Swagger UI
    - Add request/response examples
    - Document error codes

17. **Performance Optimization**
    - Connection pooling
    - Caching layer (Redis)
    - Async PostgreSQL (asyncpg)

18. **Developer Experience**
    - Add README.md
    - Create .env.example
    - Add CONTRIBUTING.md
    - Set up pre-commit hooks

### Complexity Assessment

**Overall Complexity:** ⭐⭐⭐⭐ (Senior-Level)

**Reasoning:**
- Multi-product architecture (Health + CASCO)
- AI integration (OpenAI GPT-4)
- RAG system with vector stores
- Multi-tenant architecture
- Complex comparison matrix logic

**Team Recommendations:**
- **Junior Developers:** Can handle isolated bug fixes, add tests, improve documentation
- **Mid-Level Developers:** Can implement new routes, add features to existing modules
- **Senior Developers:** Required for architecture decisions, performance optimization, security hardening

### Estimated Effort

| Task Category | Effort (Person-Days) |
|---------------|---------------------|
| Critical Fixes | 3-5 days |
| High Priority | 10-15 days |
| Medium Priority | 20-30 days |
| Low Priority | 15-20 days |
| **Total** | **48-70 days (2-3 months)** |

---

## Conclusion

The OnGo Insurance Platform backend is a **well-architected, functional system** with strong foundations. The CASCO module implementation is particularly impressive, with comprehensive job-based tracking and a clean 22-field extraction model.

However, **production readiness gaps exist** in areas like security (rate limiting, CORS), observability (logging, tracing), and operational concerns (monitoring, migrations). Addressing the **critical and high-priority issues** will significantly improve system reliability and security.

The codebase is maintainable and follows consistent patterns, making it suitable for team collaboration. With the recommended improvements, this platform will be robust, scalable, and production-ready.

**Overall Grade: B+ (Good, with room for improvement)**

---

*Report generated by AI analysis. Manual verification recommended for security-critical findings.*

