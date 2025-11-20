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

---

## ⚠️ Bug & Issue Report

### 🔴 Critical Issues

#### 1. SQL Query String Formatting in admin_tc.py
**File:** `app/routes/admin_tc.py`  
**Lines:** 266-267, 294

**Problem:** Using f-string formatting in SQL queries.

**Suggested Fix:** Use parameterized queries with %s placeholders.

#### 2. Missing Database Connection Error Handling
**File:** `app/main.py`  
**Line:** 92-97

**Problem:** No error handling for connection failures.

**Suggested Fix:** Wrap `psycopg2.connect()` with try-except.

#### 3. CORS Allows All Origins
**File:** `app/main.py`  
**Line:** 142

**Problem:** `allow_origins=["*"]` allows any origin.

**Suggested Fix:** Use whitelist from environment variable.

### 🟡 High Priority Issues

#### 4. No Input Validation for File Uploads
**File:** `app/routes/casco_routes.py`

**Problem:** Only checks extension, not content or size.

**Suggested Fix:** Validate file size and PDF magic bytes.

#### 5. No Rate Limiting
**Problem:** All endpoints unprotected.

**Suggested Fix:** Implement slowapi rate limiting.

#### 6. No Structured Logging
**Problem:** Using print statements instead of logging module.

**Suggested Fix:** Configure Python logging with levels.

### 🟢 Medium Priority Issues

#### 7. Missing API Versioning
**Problem:** No version prefix on routes.

**Suggested Fix:** Add `/api/v1/` prefix.

#### 8. No Request Tracing
**Problem:** No correlation IDs for debugging.

**Suggested Fix:** Add middleware for request IDs.

#### 9. Manual Database Migrations
**Problem:** No migration tool like Alembic.

**Suggested Fix:** Implement Alembic for version control.

---

## 📋 Summary & Recommendations

### Strengths

✅ **Well-Structured Architecture** - Clear separation of concerns  
✅ **Robust CASCO Implementation** - Complete job-based system  
✅ **Security-Conscious SQL** - Parameterized queries (mostly)  
✅ **Good Error Handling** - 79 exception handlers  
✅ **Modern Tech Stack** - FastAPI, Pydantic, GPT-4

### Weaknesses

❌ **Limited Test Coverage** - Only 5 test files  
❌ **No CI/CD Pipeline** - Manual testing and deployment  
❌ **Production Gaps** - No rate limiting, logging, monitoring  
❌ **Manual Migrations** - No Alembic or versioning  
❌ **No Code Quality Tools** - Missing linter, formatter, type checker

### Priority Recommendations

**Critical (Immediate):**
1. Fix SQL injection risks in admin_tc.py
2. Implement rate limiting
3. Fix CORS configuration
4. Add database error handling

**High (2 Weeks):**
5. Add structured logging
6. Implement request tracing
7. Add file upload validation
8. Set up CI/CD
9. Add comprehensive tests

**Medium (1 Month):**
10. Database migration tool (Alembic)
11. Add monitoring (Prometheus)
12. Fix memory management
13. API versioning
14. Code quality tools (mypy, Black, pylint)

### Complexity Assessment

**Overall:** ⭐⭐⭐⭐ (Senior-Level)

- Multi-product architecture
- AI integration (GPT-4)
- RAG system with vector stores
- Multi-tenant architecture

**Estimated Effort:** 48-70 person-days (2-3 months) for all recommendations

### Overall Grade

**B+ (Good, with room for improvement)**

The system is well-architected and functional, but needs production hardening in security, observability, and operational areas.

---

*Full detailed report available in BACKEND_ANALYSIS_REPORT.md*
