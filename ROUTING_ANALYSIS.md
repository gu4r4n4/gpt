# QA Route Analysis: /api/qa/ask-share

**Date:** 2025-11-11  
**Issue:** POST to `https://gpt-vis.onrender.com/api/qa/ask-share` returns `{"detail":"Not Found"}`

---

## ✅ ROUTING ANALYSIS

### Route Definitions Found

**File: `backend/api/routes/qa.py`**
```python
# Line 88
router = APIRouter(prefix="/qa", tags=["qa"])

# Line 327 (after adding ping endpoint)
@router.post("/ask-share", response_model=QAAskResponse)
def ask_share_qa(req: QAAskRequest, conn = Depends(get_db)):
    ...
```

**File: `app/main.py`**
```python
# Line 127
app.include_router(qa_router, prefix="/api")
```

### Computed Effective Path

| Component | Value |
|-----------|-------|
| Mount prefix (main.py) | `/api` |
| Router prefix (qa.py) | `/qa` |
| Endpoint decorator | `/ask-share` |
| **FINAL PATH** | **`/api/qa/ask-share`** ✅ |

### Conclusion

**✅ NO DOUBLE PREFIX DETECTED**  
The routing configuration is **CORRECT**. The path should be `/api/qa/ask-share` with no duplication.

---

## 🔍 ROOT CAUSE ANALYSIS

Since the routing is correct, the 404 is likely caused by:

1. **Server not restarted** after recent code changes
2. **Import failure** - `qa_router` not loading properly
3. **Route registration failure** at startup
4. **Data dependency issue** - endpoint failing during initialization

---

## 🛠️ CHANGES MADE

### 1. Added Route Debugging (app/main.py)

Added startup event handler to print all registered routes:

```python
@app.on_event("startup")
async def print_routes():
    from fastapi.routing import APIRoute
    print("\n=== REGISTERED FASTAPI ROUTES ===")
    for route in app.routes:
        if isinstance(route, APIRoute):
            methods = ",".join(route.methods)
            print(f"[route] {route.path:50s} {methods}")
    print("=================================\n")
```

**Action:** Check server logs on startup for this output. Look for `/api/qa/ask-share` in the list.

### 2. Added Ping Endpoint (backend/api/routes/qa.py)

```python
@router.get("/ask-share/ping")
def ask_share_ping():
    """Health check endpoint for ask-share route"""
    return {"ok": True, "endpoint": "ask-share", "status": "available"}
```

**Test:**
```bash
curl -i https://gpt-vis.onrender.com/api/qa/ask-share/ping
```

- ✅ If returns `{"ok": true, ...}` → routing works, issue is with POST handler
- ❌ If returns 404 → routing completely broken

---

## 🧪 TESTING COMMANDS

All commands saved to `test_commands.sh`. Key tests:

### 1. Check OpenAPI Docs
```bash
curl -sS https://gpt-vis.onrender.com/docs | grep -E "api.?/qa/ask-share" -i
```

### 2. Ping Endpoint
```bash
curl -i https://gpt-vis.onrender.com/api/qa/ask-share/ping
```

### 3. Real POST Request
```bash
curl -i https://gpt-vis.onrender.com/api/qa/ask-share \
  -H "Content-Type: application/json" \
  -d '{
    "lang": "en",
    "question": "What is covered?",
    "share_token": "Q4ZIHIb9OYtPuEbm1mP2mQ"
  }'
```

### 4. Check Chunks Availability
```bash
curl -sS "https://gpt-vis.onrender.com/api/qa/chunks-report?share_token=Q4ZIHIb9OYtPuEbm1mP2mQ" | jq
```

**Expected:** `total_chunks > 0`

### 5. Inspect Share Payload
```bash
curl -sS "https://gpt-vis.onrender.com/shares/Q4ZIHIb9OYtPuEbm1mP2mQ" | jq ".payload.batch_token, .payload.document_ids"
```

---

## 📊 DATA INTEGRITY CHECKS

If routing is correct but returns 404 with domain-specific message like "No offer chunks available", run these SQL queries:

### Query 1: Verify Batch Exists
```sql
SELECT id, token, title, status, created_at 
FROM public.offer_batches 
WHERE token = '<batch_token_from_share>';
```

### Query 2: List Files in Batch
```sql
SELECT id, filename, storage_path, embeddings_ready, retrieval_file_id, insurer_code
FROM public.offer_files 
WHERE batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>')
ORDER BY id;
```

### Query 3: Count Chunks Per File
```sql
SELECT 
  of.id AS file_id, 
  of.filename, 
  COUNT(oc.id) AS chunk_count,
  of.embeddings_ready
FROM public.offer_files of
LEFT JOIN public.offer_chunks oc ON oc.file_id = of.id
WHERE of.batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>')
GROUP BY of.id, of.filename, of.embeddings_ready
ORDER BY of.id;
```

### Query 4: Total Chunks for Batch
```sql
SELECT COUNT(*) AS total_chunks
FROM public.offer_chunks 
WHERE file_id IN (
  SELECT id FROM public.offer_files 
  WHERE batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>')
);
```

---

## 🩹 IF CHUNKS = 0: RE-EMBEDDING

If `total_chunks = 0`, files need to be embedded:

### Step 1: Get File IDs
From Query 2 above, note the `id` values.

### Step 2: Re-embed Each File
```bash
curl -X POST "https://gpt-vis.onrender.com/api/qa/reembed-file?file_id=<FILE_ID>" \
  -H "X-User-Role: admin"
```

Repeat for each file in the batch.

### Step 3: Verify Chunks Created
Re-run Query 4 above to confirm `total_chunks > 0`.

---

## 📋 DELIVERABLES

### 1. Effective Route Path
**`/api/qa/ask-share`** ✅ (no double prefix)

### 2. No Patch Needed
Routing is already correct. No code changes required for routing itself.

### 3. Route Registration Verification
After server restart, check startup logs for:
```
=== REGISTERED FASTAPI ROUTES ===
[route] /api/qa/ask-share                             POST
[route] /api/qa/ask-share/ping                        GET
...
=================================
```

### 4. Next Actions (if routing confirmed working)

**Scenario A: Ping works, POST fails**
- Check request body validation
- Verify `share_token` is valid
- Check database connection in endpoint

**Scenario B: Both ping and POST fail (404)**
- Server needs restart
- Check import statement: `from backend.api.routes.qa import router as qa_router`
- Verify no syntax errors preventing module load

**Scenario C: POST works but returns "No offer chunks available"**
1. Run chunks report: confirm `total_chunks = 0`
2. Get `batch_token` from share payload
3. Run SQL Query 2 to get file IDs
4. Re-embed files using admin endpoint
5. Verify chunks created with Query 4

---

## 🚀 IMMEDIATE ACTION ITEMS

1. **Restart the server** to load the new route debugging code
2. **Check startup logs** for the registered routes list
3. **Test ping endpoint** to verify routing works
4. **Test POST endpoint** with valid payload
5. **If POST fails with data error**, run chunks-report and re-embed if needed

---

## 📞 SUMMARY

| Question | Answer |
|----------|--------|
| Is there a double `/api`? | ❌ No, routing is correct |
| What's the effective path? | `/api/qa/ask-share` |
| Why 404 then? | Likely server not restarted or data issue |
| Patch needed? | ✅ Added debugging code only |
| How to confirm route exists? | Check startup logs for route list |
| If data issue? | Re-embed files using admin endpoint |

**Test the ping endpoint first, then POST. If POST returns data errors, check chunks-report.**

