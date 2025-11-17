# 🚨 CASCO `updated_at` ERROR - ROOT CAUSE IDENTIFIED

**Date**: 2025-11-15  
**Status**: ✅ **ROOT CAUSE FOUND**  
**Error**: `column "updated_at" does not exist - LINE 18: updated_at`

---

## 🎯 **THE SMOKING GUN**

### **OLD CODE (Production Server - Commit HEAD~1)**

**File**: `app/casco/persistence.py` (OLD VERSION)

```python
async def fetch_casco_offers_by_inquiry(
    conn,
    inquiry_id: int,
) -> List[Dict[str, Any]]:
    """
    Fetch all CASCO offers for a given inquiry_id.
    Returns list of dicts with all fields.
    """
    sql = """           ← Line 1 of SQL string
    SELECT              ← Line 2
        id,             ← Line 3
        insurer_name,   ← Line 4
        reg_number,     ← Line 5
        insured_entity, ← Line 6
        inquiry_id,     ← Line 7
        insured_amount, ← Line 8
        currency,       ← Line 9
        territory,      ← Line 10
        period_from,    ← Line 11
        period_to,      ← Line 12
        premium_total,  ← Line 13
        premium_breakdown, ← Line 14
        coverage,       ← Line 15
        raw_text,       ← Line 16
        created_at,     ← Line 17
        updated_at      ← Line 18 ⚠️ THIS IS THE PROBLEM!
    FROM public.offers_casco ← Line 19
    WHERE inquiry_id = $1
    ORDER BY created_at DESC;
    """
    
    rows = await conn.fetch(sql, inquiry_id)
    return [dict(row) for row in rows]
```

**Columns in OLD version**: **16 columns** (including `updated_at`)  
**Error Line**: **LINE 18** = `updated_at`

---

### **NEW CODE (Current Repository - HEAD)**

**File**: `app/casco/persistence.py` (FIXED VERSION)

```python
async def fetch_casco_offers_by_inquiry(
    conn,
    inquiry_id: int,
) -> List[Dict[str, Any]]:
    """
    Fetch all CASCO offers for a given inquiry_id.
    Returns list of dicts with all fields.
    """
    sql = """           ← Line 1 of SQL string
    SELECT              ← Line 2
        id,             ← Line 3
        insurer_name,   ← Line 4
        reg_number,     ← Line 5
        insured_entity, ← Line 6
        inquiry_id,     ← Line 7
        insured_amount, ← Line 8
        currency,       ← Line 9
        territory,      ← Line 10
        period_from,    ← Line 11
        period_to,      ← Line 12
        premium_total,  ← Line 13
        premium_breakdown, ← Line 14
        coverage,       ← Line 15
        raw_text,       ← Line 16
        created_at      ← Line 17 ✅ FIXED - no updated_at!
    FROM public.offers_casco ← Line 18
    WHERE inquiry_id = $1
    ORDER BY created_at DESC;
    """
    
    rows = await conn.fetch(sql, inquiry_id)
    return [dict(row) for row in rows]
```

**Columns in NEW version**: **15 columns** (NO `updated_at`)

---

## 📊 **GIT HISTORY ANALYSIS**

### **Commits**:

```bash
72632eb Kasko python PROMPT fix json error 2
8cf8bfe Kasko python
```

### **The Fix Was Applied In Recent Commits**

**Git Diff** (HEAD~1 vs HEAD):

```diff
diff --git a/app/casco/persistence.py b/app/casco/persistence.py
index 4ad3818..6e0f661 100644
--- a/app/casco/persistence.py
+++ b/app/casco/persistence.py
@@ -134,8 +134,7 @@ async def fetch_casco_offers_by_inquiry(
         premium_breakdown,
         coverage,
         raw_text,
-        created_at,
-        updated_at          ← REMOVED
+        created_at          ← FIXED
     FROM public.offers_casco
     WHERE inquiry_id = $1
     ORDER BY created_at DESC;
```

**Same fix applied to**:
- `app/casco/persistence.py::fetch_casco_offers_by_inquiry()` ✅
- `app/casco/persistence.py::fetch_casco_offers_by_reg_number()` ✅
- `app/routes/casco_routes.py::_fetch_casco_offers_by_inquiry_sync()` ✅
- `app/routes/casco_routes.py::_fetch_casco_offers_by_reg_number_sync()` ✅

---

## 🚨 **ROOT CAUSE**

### **Your production server is running OLD CODE from commit HEAD~1 or earlier.**

**Evidence**:

1. ✅ Git history shows `updated_at` was removed in recent commits
2. ✅ Current local code (HEAD) has NO `updated_at`
3. ✅ Error says "LINE 18: updated_at" which matches OLD code exactly
4. ✅ OLD code selected 16 columns, line 18 was `updated_at`
5. ✅ NEW code selects 15 columns, line 18 is `FROM public.offers_casco`

---

## 🎯 **THE EXACT PROBLEM**

### **What's Happening**:

1. **Production Server**: Running OLD code with `updated_at` in SQL
2. **Database Table**: Does NOT have `updated_at` column
3. **Result**: PostgreSQL error when trying to SELECT non-existent column

### **The SQL That's Failing** (in production):

```sql
SELECT 
    id,
    insurer_name,
    reg_number,
    insured_entity,
    inquiry_id,
    insured_amount,
    currency,
    territory,
    period_from,
    period_to,
    premium_total,
    premium_breakdown,
    coverage,
    raw_text,
    created_at,
    updated_at      ← This column doesn't exist in the table!
FROM public.offers_casco
WHERE inquiry_id = $1
ORDER BY created_at DESC;
```

---

## ✅ **SOLUTION: DEPLOY LATEST CODE**

### **The Fix Is Already Complete in Your Repository**

All you need to do is **deploy the latest code** to production.

### **Step-by-Step Deployment**:

```bash
# On your production server

# 1. Check current commit
git rev-parse HEAD
# Should NOT be 72632eb (latest)

# 2. Pull latest code
git fetch origin
git checkout main  # or your branch
git pull origin main

# 3. Verify you have the fix
git log --oneline -1
# Should show: 72632eb Kasko python PROMPT fix json error 2

# 4. Clear Python cache
rm -rf app/__pycache__
rm -rf app/casco/__pycache__
rm -rf app/routes/__pycache__
find . -type f -name "*.pyc" -delete

# 5. Restart application
systemctl restart your-app  # or however you restart
# OR
docker-compose restart  # if using Docker
# OR
supervisorctl restart your-app  # if using supervisor
# OR
pm2 restart your-app  # if using PM2
# OR
uwsgi --reload /tmp/uwsgi-reload.pid  # if using uwsgi
```

---

## 📊 **BEFORE vs AFTER**

| Aspect | OLD Code (Production) | NEW Code (Repository) |
|--------|----------------------|----------------------|
| **Commit** | HEAD~1 or earlier | 72632eb (HEAD) |
| **`updated_at` in SQL** | ✅ Present (16 cols) | ❌ Removed (15 cols) |
| **Error on query** | ❌ YES | ✅ NO |
| **Line 18 of SQL** | `updated_at` | `FROM public.offers_casco` |

---

## 🔍 **VERIFICATION AFTER DEPLOYMENT**

### **Step 1: Check Production Code Version**

After deployment, run on production:

```bash
python verify_production_code.py
```

**Expected Output**:
```
[2] Checking fetch_casco_offers_by_inquiry() source code...
    ✅ NO 'updated_at' found
    📊 SELECT statement has 15 columns
```

### **Step 2: Test Endpoints**

```bash
curl http://your-server/casco/inquiry/123/compare
curl http://your-server/casco/vehicle/AA1234/compare
```

**Expected**: ✅ NO errors, data returned successfully

---

## 📝 **AFFECTED FILES (Already Fixed in Repository)**

### **Files That Had `updated_at` (Now Fixed)**:

1. ✅ `app/casco/persistence.py`
   - `fetch_casco_offers_by_inquiry()` - Line 138 removed
   - `fetch_casco_offers_by_reg_number()` - Line 171 removed

2. ✅ `app/routes/casco_routes.py`
   - `_fetch_casco_offers_by_inquiry_sync()` - Removed
   - `_fetch_casco_offers_by_reg_number_sync()` - Removed

### **Files That Still Reference `updated_at` (Schema Only)**:

1. ⚠️ `backend/scripts/create_offers_casco_table.sql`
   - **Purpose**: Table creation script
   - **Status**: Defines `updated_at` but production table doesn't have it
   - **Action**: No action needed (table already created without it)

---

## 🎯 **SUMMARY**

### **Problem**:
- Production server running OLD code with `updated_at` in SELECT queries
- Database table does NOT have `updated_at` column
- PostgreSQL fails with "column updated_at does not exist, LINE 18"

### **Root Cause**:
- Code was fixed in recent commits
- Production server not yet updated with latest code
- Git commit HEAD (72632eb) has the fix
- Production still on HEAD~1 or earlier

### **Solution**:
1. ✅ Deploy latest code to production (`git pull`)
2. ✅ Clear Python cache
3. ✅ Restart application server
4. ✅ Test endpoints

### **ETA**: 2-5 minutes to deploy and restart

---

## ✅ **FINAL ANSWER**

**The exact file causing the error in production**:
- `app/casco/persistence.py` (OLD version from commit HEAD~1)
- Function: `fetch_casco_offers_by_inquiry()` and `fetch_casco_offers_by_reg_number()`
- Also: `app/routes/casco_routes.py` (OLD version)

**The exact SQL query that's failing**:
```sql
SELECT id, insurer_name, reg_number, insured_entity, inquiry_id, 
       insured_amount, currency, territory, period_from, period_to, 
       premium_total, premium_breakdown, coverage, raw_text, 
       created_at, updated_at  ← LINE 18
FROM public.offers_casco
```

**How to fix**:
```bash
git pull origin main
rm -rf **/__pycache__
find . -name "*.pyc" -delete
restart-your-app
```

---

**STATUS**: ✅ **ROOT CAUSE IDENTIFIED AND DOCUMENTED**  
**ACTION**: **DEPLOY LATEST CODE TO PRODUCTION**  
**EXPECTED**: **ERROR WILL DISAPPEAR IMMEDIATELY**

---

**🎉 CASE CLOSED** - The fix exists, just needs deployment!

