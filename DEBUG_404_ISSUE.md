# 🚨 CRITICAL DEBUG - ask-share Route Returns 404 Despite Being Registered

## ⚠️ SYMPTOMS

```
OPTIONS /api/qa/ask-share HTTP/1.1" 200 OK  ✅
POST /api/qa/ask-share HTTP/1.1" 404 Not Found  ❌

[route] /api/qa/ask-share/ping                             GET  ✅
[route] /api/qa/ask-share                                  POST  ✅
```

**Route IS registered, OPTIONS works, but POST returns 404!**

---

## 🔍 ROOT CAUSE THEORIES

### Theory 1: Reverse Proxy / CDN Caching
- Render.com might have multiple instances
- Old code still running on some instances
- Load balancer routing to mixed versions

### Theory 2: FastAPI Response Model Issue
- `response_model=QAAskResponse` might be causing problems
- Pydantic validation could be failing silently

### Theory 3: Dependency Injection Failure
- `Depends(get_db)` might be failing at route matching time
- Database connection issue preventing route from being accessible

### Theory 4: Request Body Validation
- FastAPI might be rejecting the request before it reaches the handler
- Though this would usually return 422, not 404

---

## 🛠️ NEW DEBUGGING ADDED

### 1. Request Logging Middleware (app/main.py)
Logs all `/api/qa/` requests BEFORE and AFTER route handler:

```python
@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    method = request.method
    if "/api/qa/" in path:
        print(f"[middleware] {method} {path} - BEFORE route handler")
    response = await call_next(request)
    if "/api/qa/" in path:
        print(f"[middleware] {method} {path} - AFTER route handler - status: {response.status_code}")
    return response
```

**What to look for:**
- If you see `BEFORE` but not `AFTER` → route handler crashed
- If you see neither → request not reaching FastAPI at all (proxy issue)
- If you see both with 404 → route not matching for some reason

### 2. Handler Entry Logging (backend/api/routes/qa.py)
Logs when the POST handler is actually invoked:

```python
@router.post("/ask-share", response_model=QAAskResponse)
def ask_share_qa(req: QAAskRequest, conn = Depends(get_db)):
    print(f"[qa] ask-share POST received: question={req.question[:50]}, share_token={req.share_token[:10]}...")
    ...
```

**What to look for:**
- If this prints → handler is reached, issue is downstream
- If this doesn't print → request not reaching handler (validation/dependency issue)

### 3. Simple Test Endpoint (backend/api/routes/qa.py)
Added minimal POST endpoint with NO dependencies or validation:

```python
@router.post("/ask-share/test")
def ask_share_test():
    print("[qa] ask-share/test POST received!")
    return {"ok": True, "message": "ask-share test endpoint works"}
```

**Test it:**
```bash
curl -X POST https://gpt-vis.onrender.com/api/qa/ask-share/test
```

**Expected:** `{"ok": true, "message": "ask-share test endpoint works"}`

---

## 🧪 DEBUGGING STEPS

### Step 1: Test Simple Endpoint
```bash
curl -X POST https://gpt-vis.onrender.com/api/qa/ask-share/test
```

**Result Analysis:**
- ✅ Works → Routing is fine, issue is with main endpoint
- ❌ 404 → Deployment/proxy issue, routes not actually deployed

### Step 2: Check Logs for Middleware Output
After sending a POST to `/api/qa/ask-share`, check logs for:

```
[middleware] POST /api/qa/ask-share - BEFORE route handler
[middleware] POST /api/qa/ask-share - AFTER route handler - status: XXX
```

**Result Analysis:**
- See both lines → Request reached FastAPI, check status code
- See BEFORE only → Handler crashed or hung
- See neither → Request not reaching FastAPI (proxy/routing issue)

### Step 3: Check for Handler Entry Log
After POST, look for:
```
[qa] ask-share POST received: question=...
```

**Result Analysis:**
- See this log → Handler executed, issue is in business logic
- Don't see it → Request validation or dependency injection failed

### Step 4: Test Ping Endpoint (Already Added)
```bash
curl https://gpt-vis.onrender.com/api/qa/ask-share/ping
```

**Result Analysis:**
- ✅ Works → GET routing works, only POST has issues
- ❌ 404 → All ask-share routes broken

---

## 🎯 DIAGNOSTIC MATRIX

| Ping | Test | POST | Middleware Log | Handler Log | Diagnosis |
|------|------|------|----------------|-------------|-----------|
| ✅ | ✅ | ❌ | Both | No | Request validation issue |
| ✅ | ✅ | ❌ | Both | Yes | Business logic error |
| ✅ | ✅ | ❌ | None | No | Proxy routing issue |
| ✅ | ❌ | ❌ | None | No | Deployment issue |
| ❌ | ❌ | ❌ | None | No | Routes not deployed |
| ✅ | ✅ | ❌ | Before only | No | Handler crash/hang |

---

## 🔧 LIKELY FIXES

### If Proxy/Deployment Issue (most likely):
1. **Full redeploy on Render.com**
   ```bash
   git add .
   git commit -m "Add debugging for ask-share route"
   git push
   ```
   
2. **Clear any CDN/proxy caches**
   - Check Render.com dashboard for cache settings
   - May need to wait 5-10 minutes for changes to propagate

3. **Verify deployment logs**
   - Check that new code is actually deployed
   - Look for the route registration output

### If Request Validation Issue:
1. **Remove response_model temporarily**
   ```python
   @router.post("/ask-share")  # Remove response_model=QAAskResponse
   def ask_share_qa(req: QAAskRequest, conn = Depends(get_db)):
       ...
       return {"answer": answer, "sources": sources}  # Return plain dict
   ```

2. **Make request body optional**
   ```python
   def ask_share_qa(req: Optional[QAAskRequest] = None, conn = Depends(get_db)):
       if not req:
           return {"error": "No request body"}
       ...
   ```

### If Dependency Injection Issue:
1. **Test without database dependency**
   ```python
   @router.post("/ask-share/no-db")
   def ask_share_no_db(req: QAAskRequest):
       return {"answer": f"Received: {req.question}", "sources": []}
   ```

2. **Check database connection**
   ```python
   @router.get("/db-test")
   def db_test():
       try:
           conn = get_db_connection()
           conn.close()
           return {"ok": True}
       except Exception as e:
           return {"ok": False, "error": str(e)}
   ```

---

## 📋 TEST COMMANDS (Updated)

```bash
# 1. Test simple endpoint (no deps, no validation)
curl -X POST https://gpt-vis.onrender.com/api/qa/ask-share/test

# 2. Test ping (GET)
curl https://gpt-vis.onrender.com/api/qa/ask-share/ping

# 3. Test main endpoint
curl -X POST https://gpt-vis.onrender.com/api/qa/ask-share \
  -H "Content-Type: application/json" \
  -d '{"lang":"en","question":"test","share_token":"Q4ZIHIb9OYtPuEbm1mP2mQ"}'

# 4. Check OpenAPI docs
curl -sS https://gpt-vis.onrender.com/openapi.json | jq '.paths."/api/qa/ask-share"'
```

---

## 🚀 NEXT ACTIONS

1. **Deploy these changes** to Render.com
2. **Wait 5 minutes** for deployment to complete
3. **Run test commands** in order (test, ping, then POST)
4. **Check server logs** for middleware and handler output
5. **Report findings** using the diagnostic matrix above

---

## 💡 IMPORTANT NOTES

- The route registration is **CORRECT** (`/api/qa/ask-share`)
- No code changes needed for routing
- Issue is likely **deployment/proxy related**
- The fact that OPTIONS works but POST doesn't suggests **stale cache or mixed deployment**

**Recommendation:** Do a full redeploy and clear any caches on Render.com

