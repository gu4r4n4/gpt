# Quick Fix Reference - ask-share 404

## 🎯 TL;DR

**Route is CORRECT:** `/api/qa/ask-share` (no double prefix)  
**Likely cause:** Server needs restart OR data issue

---

## ⚡ Quick Test (30 seconds)

```bash
# Test 1: Ping (should work immediately)
curl https://gpt-vis.onrender.com/api/qa/ask-share/ping

# Test 2: Real request
curl https://gpt-vis.onrender.com/api/qa/ask-share \
  -H "Content-Type: application/json" \
  -d '{"lang":"en","question":"test","share_token":"Q4ZIHIb9OYtPuEbm1mP2mQ"}'

# Test 3: Check data
curl "https://gpt-vis.onrender.com/api/qa/chunks-report?share_token=Q4ZIHIb9OYtPuEbm1mP2mQ" | jq .total_chunks
```

---

## 🔧 Found Lines

**qa.py:88**
```python
router = APIRouter(prefix="/qa", tags=["qa"])
```

**main.py:127**
```python
app.include_router(qa_router, prefix="/api")
```

**Effective path:** `/api` + `/qa` + `/ask-share` = `/api/qa/ask-share` ✅

---

## 📝 Changes Made

1. **Added route debugging** (main.py) - shows all routes at startup
2. **Added ping endpoint** (qa.py) - GET `/api/qa/ask-share/ping`

---

## 🚨 Action Required

### Step 1: Restart Server
```bash
# Restart your FastAPI server to load the new debugging code
```

### Step 2: Check Startup Logs
Look for this section:
```
=== REGISTERED FASTAPI ROUTES ===
[route] /api/qa/ask-share          POST
[route] /api/qa/ask-share/ping     GET
...
```

If `/api/qa/ask-share` appears → routing works!  
If it doesn't appear → import issue, check for errors in logs.

### Step 3: Test Endpoints
Run the Quick Test commands above.

---

## 🔍 If Total Chunks = 0

```bash
# 1. Get batch_token from share
curl "https://gpt-vis.onrender.com/shares/Q4ZIHIb9OYtPuEbm1mP2mQ" | jq .payload.batch_token

# 2. Find file IDs (in DB):
# SELECT id FROM public.offer_files WHERE batch_id = (SELECT id FROM public.offer_batches WHERE token = '<batch_token>');

# 3. Re-embed each file:
curl -X POST "https://gpt-vis.onrender.com/api/qa/reembed-file?file_id=<FILE_ID>" -H "X-User-Role: admin"
```

---

## 📂 Files to Review

- ✅ `ROUTING_ANALYSIS.md` - Full detailed analysis
- ✅ `test_commands.sh` - All test commands + SQL queries
- ✅ `app/main.py` - Added route debugging (line 1515)
- ✅ `backend/api/routes/qa.py` - Added ping endpoint (line 322)

---

## ✅ Checklist

- [ ] Server restarted
- [ ] Startup logs show `/api/qa/ask-share` route
- [ ] Ping endpoint returns `{"ok": true}`
- [ ] POST request works or shows data error
- [ ] If data error: chunks-report shows `total_chunks > 0`
- [ ] If chunks=0: re-embed files

---

**Expected outcome:** After server restart, the route should appear in `/docs` and respond to requests. If it returns a data error, re-embed the files.

