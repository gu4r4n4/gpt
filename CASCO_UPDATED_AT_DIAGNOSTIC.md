# 🚨 CASCO `updated_at` ERROR - COMPREHENSIVE DIAGNOSTIC

**Error**: `column "updated_at" does not exist - LINE 18: updated_at`

---

## ✅ PYTHON CODE STATUS

### **ALL SQL Queries Are CORRECT** (No `updated_at` in any SELECT)

I've verified EVERY SQL query in the codebase:

| File | Function | Columns | Status |
|------|----------|---------|--------|
| `app/casco/persistence.py` | `fetch_casco_offers_by_inquiry()` | 15 | ✅ NO `updated_at` |
| `app/casco/persistence.py` | `fetch_casco_offers_by_reg_number()` | 15 | ✅ NO `updated_at` |
| `app/routes/casco_routes.py` | `_fetch_casco_offers_by_inquiry_sync()` | 15 | ✅ NO `updated_at` |
| `app/routes/casco_routes.py` | `_fetch_casco_offers_by_reg_number_sync()` | 15 | ✅ NO `updated_at` |

**All 4 queries select exactly these 15 columns:**
1. `id`
2. `insurer_name`
3. `reg_number`
4. `insured_entity`
5. `inquiry_id`
6. `insured_amount`
7. `currency`
8. `territory`
9. `period_from`
10. `period_to`
11. `premium_total`
12. `premium_breakdown`
13. `coverage`
14. `raw_text`
15. `created_at` ✅ (NOT `updated_at`)

---

## 🔍 SCHEMA MISMATCH DETECTED

### **Problem**: SQL Creation Script vs Production Table

**File**: `backend/scripts/create_offers_casco_table.sql:32`

```sql
CREATE TABLE IF NOT EXISTS public.offers_casco (
    ...
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()  -- ⚠️ DEFINED
);
```

**But**: Your error says the column doesn't exist in production!

This means ONE of these is true:

1. ✅ **Most Likely**: The production table was created WITHOUT `updated_at` (older version of script)
2. The `updated_at` column was manually dropped later
3. The SQL script was never applied to production
4. There are multiple databases and you're querying the wrong one

---

## 🔍 POSSIBLE CAUSES OF "LINE 18" ERROR

Since all Python code is correct, the error could be:

### Possibility 1: **Old Code Still Running in Production**

- ✅ **Solution**: Restart your FastAPI server to load the fixed code
- The old version of the code might still be running

### Possibility 2: **Cached Database Connection**

- ✅ **Solution**: Restart database connection pool
- Old query plans might be cached

### Possibility 3: **Different Code Path**

- ❓ **Check**: Is the error coming from a CASCO endpoint or a different part of the system?
- The error traceback will show the exact file and line number

### Possibility 4: **Database View or Function**

- ❓ **Check**: Are there any database views, materialized views, or stored procedures that reference `offers_casco`?
- These might have `SELECT *` which would fail if the table structure changed

---

## 🔧 DIAGNOSTIC STEPS

### Step 1: Check Actual Table Schema

Run this query in your database console:

```sql
SELECT 
    column_name,
    data_type,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'offers_casco'
ORDER BY ordinal_position;
```

**Expected Output** (if table matches production error):
- 15 columns total
- `created_at` exists
- `updated_at` does NOT exist

---

### Step 2: Search for Database Views or Functions

```sql
-- Check for views referencing offers_casco
SELECT 
    table_name,
    view_definition
FROM information_schema.views
WHERE table_schema = 'public'
  AND view_definition LIKE '%offers_casco%';

-- Check for functions/procedures
SELECT 
    routine_name,
    routine_definition
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND routine_definition LIKE '%offers_casco%';
```

---

### Step 3: Check for Triggers

```sql
SELECT 
    trigger_name,
    event_object_table,
    action_statement
FROM information_schema.triggers
WHERE event_object_schema = 'public'
  AND event_object_table = 'offers_casco';
```

---

### Step 4: Get Full Error Traceback

When you see the error, copy the FULL traceback showing:
- Which endpoint failed (e.g., `/casco/inquiry/123/compare`)
- Exact file path and line number
- Complete SQL query that failed

---

## 🎯 SOLUTION OPTIONS

### Option A: **Add `updated_at` Column to Production Table** (Recommended)

If you want the table to match the schema script:

```sql
-- Add the missing column
ALTER TABLE public.offers_casco 
ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Add the trigger
CREATE OR REPLACE FUNCTION update_offers_casco_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_offers_casco_updated_at
    BEFORE UPDATE ON public.offers_casco
    FOR EACH ROW
    EXECUTE FUNCTION update_offers_casco_updated_at();

-- Backfill existing rows
UPDATE public.offers_casco 
SET updated_at = created_at 
WHERE updated_at IS NULL;
```

**Then update all Python queries** to include `updated_at` in SELECT lists.

---

### Option B: **Remove `updated_at` from Schema Script** (Simpler)

If you don't need `updated_at`:

```sql
-- Keep table as-is (without updated_at)
-- No changes needed to production table
```

**Remove these lines** from `backend/scripts/create_offers_casco_table.sql`:

- Line 32: `updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()`
- Lines 52-64: The trigger function and trigger

**Python code already matches** this approach (no `updated_at` in queries).

---

## 🚀 IMMEDIATE FIX

**If error persists after code restart**, run this query to confirm the issue:

```sql
-- This will succeed if updated_at exists, fail if it doesn't
SELECT updated_at FROM public.offers_casco LIMIT 1;
```

**If it fails**: The column doesn't exist → Use **Option B** (remove from schema script)

**If it succeeds**: The column exists → The Python code needs to be updated to include it

---

## 📊 VERIFICATION CHECKLIST

After applying fixes:

- [ ] Restart FastAPI server
- [ ] Clear any database connection pools
- [ ] Test: `GET /casco/inquiry/{id}/compare`
- [ ] Test: `GET /casco/vehicle/{reg}/compare`
- [ ] Confirm no `updated_at` errors
- [ ] Check error logs for any other issues

---

## 📝 FILES ALREADY FIXED (No Further Changes Needed)

✅ `app/casco/persistence.py` - All queries correct  
✅ `app/routes/casco_routes.py` - All queries correct  
✅ `app/casco/comparator.py` - No database access  
✅ `app/casco/extractor.py` - No database access  
✅ `app/casco/service.py` - No database access  

---

## 🎯 NEXT STEPS

1. **Run the schema check query** (see `check_casco_schema.sql`)
2. **Get the full error traceback** from your production logs
3. **Choose Option A or B** based on your requirements
4. **Restart the application** to ensure latest code is loaded

---

**STATUS**: ✅ All Python code verified correct  
**ISSUE**: Schema mismatch between script and production table  
**ACTION**: Verify actual table schema and choose fix option

