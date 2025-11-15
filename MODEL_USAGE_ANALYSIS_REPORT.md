# 🔍 COMPLETE MODEL USAGE ANALYSIS REPORT

**Date**: 2025-11-15  
**Task**: Scan entire repository for all OpenAI model references  
**Status**: ANALYSIS ONLY - NO CHANGES MADE

---

## 📊 EXECUTIVE SUMMARY

### Key Findings

| Component | Model Used | Source | Status |
|-----------|------------|--------|--------|
| **HEALTH Extraction** | `gpt-5` | `os.getenv("GPT_MODEL", "gpt-5")` | ⚠️ **INVALID MODEL** |
| **CASCO Extraction** | `gpt-5.1` | Hardcoded default | ❌ **INVALID MODEL** |
| **Q&A System** | `gpt-4o-mini` | `os.getenv("QA_MODEL", "gpt-4o-mini")` | ✅ Valid |
| **Translation** | `gpt-4o-mini` | `os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")` | ✅ Valid |
| **Embeddings (Q&A)** | `text-embedding-3-small` | `os.getenv("EMBED_MODEL", ...)` | ✅ Valid |
| **Embeddings (Reembed)** | `text-embedding-3-large` | Hardcoded | ✅ Valid |
| **Vector Store Script** | `gpt-4o-mini` | Hardcoded | ✅ Valid |
| **Fallback Chat** | `gpt-4o-mini` | `os.getenv("FALLBACK_CHAT_MODEL", ...)` | ✅ Valid |

### Critical Issues

1. ❌ **HEALTH extractor uses invalid model `gpt-5`** - This model does not exist in OpenAI's API
2. ❌ **CASCO extractor uses invalid model `gpt-5.1`** - This model does not exist in OpenAI's API
3. ⚠️ Both extractors will fail with authentication/model errors when called

---

## 📁 DETAILED FILE-BY-FILE ANALYSIS

---

### 1. **app/gpt_extractor.py** (HEALTH Extraction)

#### Finding #1: GPTConfig Class - Primary Model

```
FILE: app/gpt_extractor.py
LINE: 743
CLASSIFICATION: HEALTH-related
```

**CODE**:
```python
741 | @dataclass
742 | class GPTConfig:
743 |     model: str = os.getenv("GPT_MODEL", "gpt-5")
744 |     max_retries: int = int(os.getenv("GPT_MAX_RETRIES", "2"))
745 |     log_prompts: bool = os.getenv("LOG_PROMPTS", "false").lower() == "true"
746 |     fallback_chat_model: str = os.getenv("FALLBACK_CHAT_MODEL", "gpt-4o-mini")
747 |
```

**ANALYSIS**:
- **Environment Variable**: `GPT_MODEL`
- **Default Value**: `"gpt-5"` ❌ **INVALID MODEL**
- **Fallback Model**: `gpt-4o-mini` ✅ Valid
- **Usage**: This is the PRIMARY model for ALL HEALTH PDF extractions
- **Impact**: HEALTH extraction will FAIL unless `GPT_MODEL` env var is set to a valid model

---

#### Finding #2: _responses_with_pdf() - Model Parameter

```
FILE: app/gpt_extractor.py
LINE: 751, 777
CLASSIFICATION: HEALTH-related
```

**CODE**:
```python
749 | # =========================
750 | # Core: Responses API path
751 | def _responses_with_pdf(model: str, document_id: str, pdf_bytes: bytes, allow_schema: bool) -> Dict[str, Any]:
752 |     content = [
753 |         {"type": "input_text", "text": _build_user_instructions(document_id)},
...
774 |     # Use chat.completions.create() - the only API that exists in SDK 1.52.0
775 |     try:
776 |         resp = openai_client.chat.completions.create(
777 |             model=model,
778 |             messages=[{"role": "user", "content": content_text}],
779 |             response_format={"type": "json_object"} if allow_schema else None,
780 |             temperature=0,
781 |         )
```

**ANALYSIS**:
- **Parameter**: `model: str` (passed from `GPTConfig.model`)
- **Used At**: Line 777 in `chat.completions.create(model=model, ...)`
- **Value Source**: Inherits from `GPTConfig()` which defaults to `"gpt-5"`

---

#### Finding #3: _chat_with_text() - Fallback Model

```
FILE: app/gpt_extractor.py
LINE: 793, 803, 814
CLASSIFICATION: HEALTH-related
```

**CODE**:
```python
791 | # =========================
792 | # Fallback: Chat Completions with extracted text
793 | def _chat_with_text(model: str, document_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
794 |     pages = _pdf_to_text_pages(pdf_bytes)
...
801 |     try:
802 |         resp = openai_client.chat.completions.create(
803 |             model=model,
804 |             messages=[
805 |                 {"role": "system", "content": "Return STRICT JSON only. No markdown, no prose."},
806 |                 {"role": "user", "content": user},
807 |             ],
808 |             response_format={"type": "json_object"},
809 |         )
...
812 |     except TypeError:
813 |         resp = openai_client.chat.completions.create(
814 |             model=model,
815 |             messages=[
816 |                 {"role": "system", "content": "Return ONLY raw JSON that matches the required schema. No extra keys."},
817 |                 {"role": "user", "content": user},
818 |             ],
819 |         )
```

**ANALYSIS**:
- **Parameter**: `model: str` (can be primary or fallback)
- **Used At**: Lines 803 and 814 in `chat.completions.create(model=model, ...)`
- **Usage Pattern**: Called with BOTH `cfg.model` and `cfg.fallback_chat_model`

---

#### Finding #4: call_gpt_extractor() - Orchestration Logic

```
FILE: app/gpt_extractor.py
LINE: 853-908
CLASSIFICATION: HEALTH-related
```

**CODE**:
```python
853 | def call_gpt_extractor(document_id: str, pdf_bytes: bytes, cfg: Optional[GPTConfig] = None) -> Dict[str, Any]:
854 |     cfg = cfg or GPTConfig()
855 |     last_err: Optional[Exception] = None
856 |
857 |     # 1) Responses + schema
858 |     for attempt in range(cfg.max_retries + 1):
859 |         try:
860 |             payload = _responses_with_pdf(cfg.model, document_id, pdf_bytes, allow_schema=True)
...
876 |             payload = _responses_with_pdf(cfg.model, document_id, pdf_bytes, allow_schema=False)
...
887 |     # 3) Chat fallback
888 |     for attempt in range(cfg.max_retries + 1):
889 |         try:
890 |             try_models = [cfg.model, cfg.fallback_chat_model] if cfg.fallback_chat_model != cfg.model else [cfg.model]
891 |             for m in try_models:
892 |                 try:
893 |                     payload = _chat_with_text(m, document_id, pdf_bytes)
```

**ANALYSIS**:
- **Model Usage**:
  - Line 860, 876: Uses `cfg.model` (defaults to `"gpt-5"` ❌)
  - Line 890: Uses BOTH `cfg.model` and `cfg.fallback_chat_model` (`"gpt-4o-mini"` ✅)
- **Retry Logic**: 
  - First tries with `gpt-5` (INVALID) → FAILS
  - Then tries fallback with `gpt-4o-mini` (VALID) → MIGHT WORK
- **Conclusion**: HEALTH extraction MAY work due to fallback, but will always fail first attempts

---

### 2. **app/casco/extractor.py** (CASCO Extraction)

#### Finding #5: extract_casco_offers_from_text() - Default Model

```
FILE: app/casco/extractor.py
LINE: 94-99, 123-124
CLASSIFICATION: CASCO-related
```

**CODE**:
```python
 94 | def extract_casco_offers_from_text(
 95 |     pdf_text: str,
 96 |     insurer_name: str,
 97 |     pdf_filename: Optional[str] = None,
 98 |     model: str = "gpt-5.1",
 99 | ) -> List[CascoExtractionResult]:
100 |     """
101 |     Core hybrid extractor using OpenAI Chat Completions API (SDK 1.52.0).
...
120 |     # ---- Use chat.completions.create() - the actual API in SDK 1.52.0 ----
121 |     try:
122 |         resp = client.chat.completions.create(
123 |             model=model,
124 |             messages=[
125 |                 {"role": "system", "content": system_prompt},
126 |                 {"role": "user", "content": user_prompt},
127 |             ],
128 |             response_format={"type": "json_object"},
129 |             temperature=0,
130 |         )
```

**ANALYSIS**:
- **Default Model**: `"gpt-5.1"` ❌ **INVALID MODEL** - This model does NOT exist
- **Used At**: Line 123 in `chat.completions.create(model=model, ...)`
- **Called From**: `app/casco/service.py` → `process_casco_pdf()` (Line 39)
- **No Override**: Function is called WITHOUT passing a `model` parameter
- **Impact**: CASCO extraction will FAIL 100% of the time
- **No Fallback**: Unlike HEALTH, CASCO has NO fallback mechanism

---

### 3. **app/casco/service.py** (CASCO Service Layer)

#### Finding #6: process_casco_pdf() - Calls Extractor

```
FILE: app/casco/service.py
LINE: 39-43
CLASSIFICATION: CASCO-related
```

**CODE**:
```python
 36 |     # 1. Extract text from PDF using existing HEALTH logic
 37 |     full_text, _pages = _pdf_pages_text(file_bytes)
 38 |
 39 |     # 2. Run GPT hybrid CASCO extraction
 40 |     extracted_results = extract_casco_offers_from_text(
 41 |         pdf_text=full_text,
 42 |         insurer_name=insurer_name,
 43 |         pdf_filename=pdf_filename,
 44 |     )
```

**ANALYSIS**:
- **No Model Parameter Passed**: Uses default `"gpt-5.1"`
- **Impact**: Every CASCO extraction uses the invalid model

---

### 4. **backend/api/routes/qa.py** (Q&A System)

#### Finding #7: Embedding Model

```
FILE: backend/api/routes/qa.py
LINE: 24
CLASSIFICATION: Q&A / Embeddings
```

**CODE**:
```python
 22 | def _embed(texts: List[str]) -> List[List[float]]:
 23 |     res = client.embeddings.create(
 24 |         model=os.getenv("EMBED_MODEL", "text-embedding-3-small"),
 25 |         input=texts
 26 |     )
 27 |     return [d.embedding for d in res.data]
```

**ANALYSIS**:
- **Environment Variable**: `EMBED_MODEL`
- **Default Value**: `"text-embedding-3-small"` ✅ Valid
- **Usage**: Semantic search embeddings
- **Status**: ✅ Working correctly

---

#### Finding #8: Chat Completion Model

```
FILE: backend/api/routes/qa.py
LINE: 705
CLASSIFICATION: Q&A
```

**CODE**:
```python
702 |         )
703 |
704 |         chat = client.chat.completions.create(
705 |             model=os.getenv("QA_MODEL", "gpt-4o-mini"),
706 |             messages=[
707 |                 {"role": "system", "content": system_msg},
708 |                 {"role": "user", "content": user_msg},
709 |             ],
710 |             temperature=0.1,
711 |         )
```

**ANALYSIS**:
- **Environment Variable**: `QA_MODEL`
- **Default Value**: `"gpt-4o-mini"` ✅ Valid
- **Usage**: Q&A responses
- **Status**: ✅ Working correctly

---

### 5. **app/routes/translate.py** (Translation)

#### Finding #9: Translation Model

```
FILE: app/routes/translate.py
LINE: 10, 32
CLASSIFICATION: Translation
```

**CODE**:
```python
  8 | router = APIRouter(prefix="/api/translate", tags=["translate"])
  9 | _client: Optional[OpenAI] = None
 10 | DEFAULT_MODEL = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")
 11 |
...
 30 |     async def _call():
 31 |         resp = client.chat.completions.create(
 32 |             model=DEFAULT_MODEL,
 33 |             messages=[{"role":"system","content":system},{"role":"user","content":text}],
 34 |             temperature=0,
 35 |         )
```

**ANALYSIS**:
- **Environment Variable**: `TRANSLATE_MODEL`
- **Default Value**: `"gpt-4o-mini"` ✅ Valid
- **Usage**: Text translation
- **Status**: ✅ Working correctly

---

### 6. **backend/scripts/reembed_file.py** (Embedding Script)

#### Finding #10: Embedding Model (Script)

```
FILE: backend/scripts/reembed_file.py
LINE: 79
CLASSIFICATION: Embeddings / Utility Script
```

**CODE**:
```python
 76 |     out = []
 77 |     for tx in texts:
 78 |         tx = tx or ""
 79 |         e = client.embeddings.create(model="text-embedding-3-large", input=tx)
 80 |         out.append(e.data[0].embedding)
 81 |     return out
```

**ANALYSIS**:
- **Hardcoded Model**: `"text-embedding-3-large"` ✅ Valid
- **Usage**: Document re-embedding for vector search
- **Status**: ✅ Working correctly

---

### 7. **scripts/probe_vector_store.py** (Vector Store Test Script)

#### Finding #11: Vector Store Query Model

```
FILE: scripts/probe_vector_store.py
LINE: 13
CLASSIFICATION: Testing / Utility
```

**CODE**:
```python
 10 | # NOTE: Vector store file_search requires Assistants API, not chat.completions
 11 | # Using chat.completions.create() instead (standard API in SDK 1.52.0)
 12 | resp = client.chat.completions.create(
 13 |     model="gpt-4o-mini",
 14 |     messages=[{"role": "user", "content": QUESTION}],
 15 |     temperature=0,
 16 | )
```

**ANALYSIS**:
- **Hardcoded Model**: `"gpt-4o-mini"` ✅ Valid
- **Usage**: Vector store probing (test script)
- **Status**: ✅ Working correctly
- **Note**: Cannot actually use vector store (requires Assistants API)

---

### 8. **app/main.py** (Health Check Endpoint)

#### Finding #12: Health Check Model Display

```
FILE: app/main.py
LINE: 200
CLASSIFICATION: Other (Info Display)
```

**CODE**:
```python
197 |         "ok": True,
198 |         "app": APP_NAME,
199 |         "version": APP_VERSION,
200 |         "model": os.getenv("GPT_MODEL", "gpt-4o-mini"),
201 |         "supabase": bool(_supabase),
202 |         "offers_table": _OFFERS_TABLE,
203 |         "share_table": _SHARE_TABLE,
```

**ANALYSIS**:
- **Environment Variable**: `GPT_MODEL`
- **Default Value**: `"gpt-4o-mini"` (different from `gpt_extractor.py`!)
- **Usage**: Display only (not used for actual API calls)
- **Note**: Inconsistent default with `gpt_extractor.py` which uses `"gpt-5"`

---

## 🔍 ENVIRONMENT VARIABLE INVENTORY

| Variable Name | Used In | Default Value | Valid? | Purpose |
|--------------|---------|---------------|--------|---------|
| `GPT_MODEL` | `app/gpt_extractor.py` | `"gpt-5"` | ❌ NO | HEALTH extraction |
| `GPT_MODEL` | `app/main.py` | `"gpt-4o-mini"` | ✅ YES | Health check display |
| `FALLBACK_CHAT_MODEL` | `app/gpt_extractor.py` | `"gpt-4o-mini"` | ✅ YES | HEALTH fallback |
| `QA_MODEL` | `backend/api/routes/qa.py` | `"gpt-4o-mini"` | ✅ YES | Q&A responses |
| `TRANSLATE_MODEL` | `app/routes/translate.py` | `"gpt-4o-mini"` | ✅ YES | Translation |
| `EMBED_MODEL` | `backend/api/routes/qa.py` | `"text-embedding-3-small"` | ✅ YES | Q&A embeddings |

---

## 📊 HARDCODED MODEL INVENTORY

| Model | Location | Valid? | Purpose |
|-------|----------|--------|---------|
| `"gpt-5.1"` | `app/casco/extractor.py:98` | ❌ NO | CASCO extraction |
| `"gpt-4o-mini"` | `scripts/probe_vector_store.py:13` | ✅ YES | Vector store test |
| `"text-embedding-3-large"` | `backend/scripts/reembed_file.py:79` | ✅ YES | Document embeddings |

---

## ❓ ROOT CAUSE ANALYSIS

### Why HEALTH Extraction MIGHT Work

1. **Primary Model**: Uses `gpt-5` (INVALID) ❌
2. **Fallback Chain**:
   - First attempts with `gpt-5` → FAILS
   - Falls back to `gpt-4o-mini` (VALID) → WORKS ✅
3. **Result**: Eventually works due to fallback, but wastes API calls on invalid model

### Why CASCO Extraction ALWAYS FAILS

1. **Primary Model**: Uses `gpt-5.1` (INVALID) ❌
2. **No Fallback**: No retry or fallback mechanism
3. **Result**: Immediate failure on every call

### Why Other Components Work

1. **Q&A**: Uses `gpt-4o-mini` ✅
2. **Translation**: Uses `gpt-4o-mini` ✅
3. **Embeddings**: Uses valid embedding models ✅

---

## 🎯 CALL FLOW ANALYSIS

### HEALTH Extraction Flow

```
User uploads PDF
    ↓
app/main.py (route handler)
    ↓
call_gpt_extractor(pdf_bytes, document_id)  ← Creates GPTConfig() (model="gpt-5")
    ↓
Step 1: _responses_with_pdf(cfg.model="gpt-5", ...)  ❌ FAILS
    ↓
Step 2: _responses_with_pdf(cfg.model="gpt-5", ...)  ❌ FAILS
    ↓
Step 3: _chat_with_text(cfg.model="gpt-5", ...)  ❌ FAILS
    ↓
Step 3: _chat_with_text(cfg.fallback_chat_model="gpt-4o-mini", ...)  ✅ WORKS
```

### CASCO Extraction Flow

```
User uploads PDF
    ↓
app/routes/casco_routes.py
    ↓
process_casco_pdf(file_bytes, insurer_name)
    ↓
extract_casco_offers_from_text(pdf_text, insurer_name)  ← Uses model="gpt-5.1"
    ↓
client.chat.completions.create(model="gpt-5.1", ...)  ❌ FAILS IMMEDIATELY
    ↓
No fallback → Exception raised
```

---

## 📝 VALID OPENAI MODELS (Reference)

### Chat Completion Models
- ✅ `gpt-4o`
- ✅ `gpt-4o-mini`
- ✅ `gpt-4-turbo`
- ✅ `gpt-4`
- ✅ `gpt-3.5-turbo`

### Embedding Models
- ✅ `text-embedding-3-large`
- ✅ `text-embedding-3-small`
- ✅ `text-embedding-ada-002`

### INVALID Models Found in This Codebase
- ❌ `gpt-5` (does not exist)
- ❌ `gpt-5.1` (does not exist)

---

## 🚨 PRIORITY ISSUES

### Issue #1: CASCO Extractor - BROKEN

**Severity**: 🔴 CRITICAL  
**Location**: `app/casco/extractor.py:98`  
**Problem**: Hardcoded invalid model `"gpt-5.1"`  
**Impact**: 100% failure rate on all CASCO extractions

### Issue #2: HEALTH Extractor - INEFFICIENT

**Severity**: 🟡 MODERATE  
**Location**: `app/gpt_extractor.py:743`  
**Problem**: Default model `"gpt-5"` (invalid), requires fallback  
**Impact**: Wasted API calls, slower extraction, confusing error logs

### Issue #3: Inconsistent Defaults

**Severity**: 🟡 MODERATE  
**Locations**: 
- `app/gpt_extractor.py:743` → `"gpt-5"`
- `app/main.py:200` → `"gpt-4o-mini"`  
**Problem**: Same env var (`GPT_MODEL`) has different defaults  
**Impact**: Confusing configuration, misleading health check

---

## ✅ ANALYSIS COMPLETE

**Total Model References Found**: 12  
**Invalid Models**: 2 (`gpt-5`, `gpt-5.1`)  
**Valid Models**: 10  
**Critical Issues**: 2 (CASCO broken, HEALTH inefficient)

---

**NO CHANGES MADE - ANALYSIS ONLY** ✅

