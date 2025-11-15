# 🔍 OpenAI API Usage Audit Report

## Executive Summary

**Total Issues Found**: 9 locations  
**Critical Bugs**: 1 (HEALTH extractor)  
**Fixed**: 1 (CASCO extractor)  
**Safe**: 7 (correct API usage)

---

## 🚨 CRITICAL: Active Bug in HEALTH Extractor

### Location: `app/gpt_extractor.py` (Lines 752-757)

**Function**: `_responses_with_pdf()`  
**Status**: ❌ **ACTIVE BUG** - Will fail with updated OpenAI SDK

**Code**:
```python
def _responses_with_pdf(model: str, document_id: str, pdf_bytes: bytes, allow_schema: bool) -> Dict[str, Any]:
    # Line 750
    kwargs: Dict[str, Any] = {"model": model, "input": [{"role": "user", "content": content}]}
    if allow_schema:
        # Line 752 - ❌ FORBIDDEN PARAMETER
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "InsurerOfferExtraction_v1", "schema": INSURER_OFFER_SCHEMA, "strict": True},
        }
    
    # Line 757 - ❌ WILL FAIL
    resp = openai_client.responses.create(**kwargs)
```

**Impact**:
- ❌ HEALTH PDF extraction will fail
- ❌ Same bug that broke CASCO
- ❌ Error: `Responses.create() got an unexpected keyword argument 'response_format'`

**Who Calls This**:
- `call_gpt_extractor()` → `extract_offer_from_pdf_bytes()` → Used by HEALTH routes

**Why CASCO Inherited This**:
CASCO was initially created by copying HEALTH's pattern, which is why both had the same bug. CASCO is now fixed.

---

## ✅ FIXED: CASCO Extractor

### Location: `app/casco/extractor.py` (Lines 121-128)

**Function**: `extract_casco_offers_from_text()`  
**Status**: ✅ **FIXED** - Using modern API

**Code**:
```python
# Define Pydantic models inline
class Offer(BaseModel):
    structured: CascoCoverage
    raw_text: str

class ResponseRoot(BaseModel):
    offers: List[Offer]

# ✅ CORRECT: New API
parsed = client.responses.parse(
    model=model,
    input=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    schema=ResponseRoot,  # ✅ Pydantic model, not response_format
)
```

**Status**: Already fixed in previous commits.

---

## 📊 Complete Inventory

### 1. Responses API Usage

| File | Line | Function | API Call | Status |
|------|------|----------|----------|--------|
| `app/gpt_extractor.py` | 757 | `_responses_with_pdf` | `responses.create` + `response_format` | ❌ CRITICAL BUG |
| `app/casco/extractor.py` | 121 | `extract_casco_offers_from_text` | `responses.parse` | ✅ FIXED |
| `scripts/probe_vector_store.py` | 10 | script | `responses.create` (no response_format) | ✅ OK |

### 2. Chat Completions API Usage (Different API - OK)

| File | Line | Function | Usage | Status |
|------|------|----------|-------|--------|
| `app/gpt_extractor.py` | 783 | `_chat_with_text` | Fallback with `response_format` | ✅ OK (valid for chat API) |
| `app/gpt_extractor.py` | 794 | `_chat_with_text` | Fallback without `response_format` | ✅ OK |
| `backend/api/routes/qa.py` | 704 | `qa_chat_endpoint` | Q&A chat | ✅ OK |
| `app/routes/translate.py` | 31 | `translate_endpoint` | Translation | ✅ OK |

---

## 🔍 Detailed Analysis

### Critical Finding: HEALTH Extractor Bug

**Flow**:
```
HEALTH Upload → extract_offer_from_pdf_bytes() 
              → call_gpt_extractor()
              → _responses_with_pdf()
              → responses.create(response_format={...})  ❌ FAILS
```

**Evidence**:
```python
# app/gpt_extractor.py:740
def _responses_with_pdf(model: str, document_id: str, pdf_bytes: bytes, allow_schema: bool):
    # ...
    kwargs = {"model": model, "input": [...]}
    
    if allow_schema:  # ← This is True by default
        kwargs["response_format"] = {  # ← FORBIDDEN in 2025 API
            "type": "json_schema",
            "json_schema": {...}
        }
    
    resp = openai_client.responses.create(**kwargs)  # ← WILL CRASH
```

**Why It's a Problem**:
1. OpenAI removed `response_format` parameter from `responses.create()` in 2025
2. The correct API is now `responses.parse()` with `schema` parameter
3. This is the EXACT same bug CASCO had before we fixed it

---

## 🆚 API Comparison

### Old (Broken) vs New (Fixed)

| Aspect | Old HEALTH/CASCO | New CASCO | Old HEALTH |
|--------|------------------|-----------|------------|
| **API Method** | `responses.create()` | `responses.parse()` | Still using `responses.create()` ❌ |
| **Schema Param** | `response_format={}` | `schema=PydanticModel` | Still using `response_format={}` ❌ |
| **Validation** | Manual JSON parsing | Automatic Pydantic | Manual JSON parsing |
| **Status** | ❌ Broken (was) | ✅ Fixed | ❌ Still broken |

---

## 📋 Summary by Component

### CASCO Module: ✅ HEALTHY
- ✅ Extractor uses `responses.parse()`
- ✅ Pydantic schema enforcement
- ✅ No deprecated parameters
- ✅ Production-ready

### HEALTH Module: ❌ AT RISK
- ❌ Extractor uses `responses.create()` with `response_format`
- ❌ Will fail with OpenAI SDK update
- ⚠️ Currently works only if SDK is outdated
- ❌ Needs same fix as CASCO

### Chat Features: ✅ HEALTHY
- ✅ Q&A uses `chat.completions.create()` (correct API)
- ✅ Translation uses `chat.completions.create()` (correct API)
- ✅ `response_format` is VALID for chat completions API
- ✅ No issues

### Fallback Logic: ✅ HEALTHY
- ✅ Intentional fallback to chat completions
- ✅ Proper error handling
- ✅ No issues

---

## 🎯 Recommendations

### Immediate Action Required

**Fix HEALTH extractor** before it breaks in production:

1. **Update `app/gpt_extractor.py`** function `_responses_with_pdf()`:
   - Replace `responses.create()` with `responses.parse()`
   - Replace `response_format` with `schema` + Pydantic model
   - Follow CASCO's pattern (already proven to work)

2. **Test HEALTH extraction**:
   - Upload HEALTH PDF
   - Verify extraction works
   - Check comparison tables

### Why CASCO Had the Same Bug

CASCO was created by copying HEALTH's extraction pattern, which already had this deprecated API usage. When we fixed CASCO, we didn't realize HEALTH also needed the fix.

**Timeline**:
1. HEALTH extractor created with `responses.create()` + `response_format`
2. CASCO extractor copied HEALTH's pattern → inherited bug
3. CASCO batch uploads failed → we investigated
4. We fixed CASCO extractor → now uses `responses.parse()`
5. ⚠️ **HEALTH still has the original bug**

---

## 🔧 Proposed Fix for HEALTH

### Current HEALTH Code (Broken)
```python
def _responses_with_pdf(model, document_id, pdf_bytes, allow_schema):
    kwargs = {"model": model, "input": [...]}
    if allow_schema:
        kwargs["response_format"] = {  # ❌ FORBIDDEN
            "type": "json_schema",
            "json_schema": {...}
        }
    resp = openai_client.responses.create(**kwargs)  # ❌ WILL FAIL
    # ... manual JSON parsing
```

### Fixed HEALTH Code (Recommended)
```python
def _responses_with_pdf(model, document_id, pdf_bytes, allow_schema):
    # Define response structure
    from pydantic import BaseModel
    
    class InsurerOffer(BaseModel):
        document_id: str
        programs: List[Program]
        # ... other fields from INSURER_OFFER_SCHEMA
    
    if allow_schema:
        # ✅ NEW API
        parsed = openai_client.responses.parse(
            model=model,
            input=[{"role": "user", "content": content}],
            schema=InsurerOffer,  # ✅ Pydantic model
        )
        return parsed.output.model_dump()  # Convert to dict
    else:
        # Fallback without schema
        resp = openai_client.responses.create(
            model=model,
            input=[{"role": "user", "content": content}],
        )
        # ... parse manually
```

---

## 🎉 Conclusion

### Current Status

| Component | Status | Action Needed |
|-----------|--------|---------------|
| **CASCO** | ✅ Fixed | None - already using modern API |
| **HEALTH** | ❌ Broken | Fix required - same as CASCO fix |
| **Q&A** | ✅ OK | None - different API |
| **Translation** | ✅ OK | None - different API |
| **Scripts** | ✅ OK | None - no deprecated params |

### Key Insight

**CASCO inherited the bug from HEALTH** but we fixed CASCO first. Now HEALTH needs the same fix.

The good news: We already know how to fix it - just apply the CASCO fix pattern to HEALTH! ✅

