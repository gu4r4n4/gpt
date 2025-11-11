# 🐛 BUG FIX: Filename Mismatch Between Upload and Share Inference

## 🎯 ROOT CAUSE

Two different `safe_filename` functions produce different outputs:

1. **Upload** uses `backend/api/routes/util.safe_filename()`  
   → `"File (v2).pdf"` → `"File__v2_.pdf"` (double underscores)

2. **Share inference** uses `app/main._safe_filename()`  
   → `"File (v2).pdf"` → `"File_v2_.pdf"` (single underscore)

**Result:** Filename doesn't match → batch_token inference fails → "No offer chunks available"

---

## ✅ SOLUTION: Unify to ONE Function

### Option A: Use util.safe_filename() Everywhere (Recommended)

**Simpler, faster, already used by upload path.**

```python
# app/main.py - Replace _safe_filename with import
from backend.api.routes.util import safe_filename as _safe_filename

# Remove the old _safe_filename function (lines 211-219)
# Remove _SAFE_DOC_CHARS (line 209)

# All calls to _safe_filename() now use util.safe_filename()
```

### Option B: Use main._safe_filename() Everywhere

**More sophisticated normalization, but requires changes to upload.**

```python
# backend/api/routes/offers_upload.py
# Replace:
from backend.api.routes.util import get_db_connection, safe_filename

# With:
from backend.api.routes.util import get_db_connection
from app.main import _safe_filename as safe_filename

# OR move _safe_filename to util.py and import from there
```

---

## 🔧 IMPLEMENTATION (Option A)

### 1. Update app/main.py

```python
# At top of file, add import (around line 30)
from backend.api.routes.util import safe_filename as _safe_filename

# Remove these lines (209-219):
# _SAFE_DOC_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
# 
# def _safe_filename(name: str) -> str:
#     name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
#     base = os.path.basename(name or "uploaded.pdf")
#     root, ext = os.path.splitext(base)
#     root = _SAFE_DOC_CHARS.sub("_", root).strip("._")
#     root = re.sub(r"_+", "_", root)
#     root = root[:100] or "uploaded"
#     ext = ext if ext else ".pdf"
#     return f"{root}{ext}"
```

### 2. Test

```bash
python3 -c "
from backend.api.routes.util import safe_filename
from app.main import _safe_filename

test_files = [
    'File (v2).pdf',
    'Offer - Company (2024).pdf',
    'Test  Multiple   Spaces.pdf'
]

for f in test_files:
    util_result = safe_filename(f)
    main_result = _safe_filename(f)
    match = '✅' if util_result == main_result else '❌'
    print(f'{match} {f}')
    print(f'   util: {util_result}')
    print(f'   main: {main_result}')
"
```

After fix, all should show ✅.

---

## 🧪 VERIFICATION

### Test Flow

1. **Upload a file**
   ```bash
   curl -X POST "https://gpt-vis.onrender.com/api/offers/upload" \
     -F "pdf=@test.pdf" \
     -F "org_id=1"
   ```

2. **Check filename in DB**
   ```sql
   SELECT filename FROM offer_files ORDER BY id DESC LIMIT 1;
   ```

3. **Create share with document_ids**
   ```bash
   curl -X POST "https://gpt-vis.onrender.com/shares" \
     -H "Content-Type: application/json" \
     -H "X-Org-Id: 1" \
     -d '{"document_ids": ["uuid::1::test.pdf"]}'
   ```

4. **Check batch_token in share**
   ```bash
   curl "https://gpt-vis.onrender.com/shares/SHARE_TOKEN" | jq .payload.batch_token
   ```
   
   **Should return:** `"bt_xxx"` (not null)

5. **Test ask-share**
   ```bash
   curl -X POST "https://gpt-vis.onrender.com/api/qa/ask-share" \
     -H "Content-Type: application/json" \
     -d '{"share_token":"SHARE_TOKEN","question":"test"}'
   ```
   
   **Should NOT return:** "No offer chunks available"

---

## 📋 FILES TO MODIFY

- ✅ `app/main.py` - Replace _safe_filename with import
- ✅ (Optional) `backend/api/routes/util.py` - Enhance if needed

---

## ⏱️ IMPACT

**Before Fix:**
- Filenames with special chars → mismatch
- batch_token inference fails
- Shares show no chunks
- ask-share returns 404

**After Fix:**
- All filenames normalized consistently
- batch_token inference works
- Shares find chunks
- ask-share returns answers

---

## 🚀 DEPLOYMENT

```bash
cd /path/to/repo
git add app/main.py
git commit -m "Fix: Unify safe_filename to prevent batch_token inference failure"
git push

# On Render.com: auto-deploy or trigger manual deploy
# Wait 5-10 min for deployment
```

---

## 🔍 ADDITIONAL DEBUG (If Still Failing After Fix)

If batch_token is still NULL after applying fix:

1. **Check for existing shares** created before fix
   → Recreate them or manually update batch_token

2. **Check upload happened AFTER fix deployed**
   → Re-upload files if they were uploaded with old code

3. **Run diagnostic SQL**
   ```sql
   -- Compare filenames
   SELECT 
       sl.token as share_token,
       jsonb_array_elements_text(sl.payload->'document_ids') as doc_id,
       split_part(jsonb_array_elements_text(sl.payload->'document_ids'), '::', 3) as extracted_name,
       of.filename as db_filename,
       CASE 
           WHEN split_part(jsonb_array_elements_text(sl.payload->'document_ids'), '::', 3) = of.filename 
           THEN '✅ Match'
           ELSE '❌ Mismatch'
       END as status
   FROM share_links sl
   CROSS JOIN offer_files of
   WHERE sl.token = 'SHARE_TOKEN'
   ORDER BY of.created_at DESC;
   ```

---

## 💡 BONUS: Future-Proof Solution

**Store original filename in offer_files:**

```sql
ALTER TABLE offer_files 
ADD COLUMN original_filename TEXT;

-- Then during upload:
INSERT INTO offer_files (..., filename, original_filename)
VALUES (..., safe_filename(original), original);

-- Share inference can match both
WHERE of.filename = safe(extracted) 
   OR of.original_filename = extracted
```

This makes the system resilient to future safe_filename changes.

