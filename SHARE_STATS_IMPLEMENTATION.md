# Share View/Edit Counting Implementation Summary

## ✅ What Was Implemented

### 1. View Counting (GET `/shares/{token}`)
- ✅ Increments `views_count` atomically on each GET request
- ✅ Updates `last_viewed_at` timestamp
- ✅ Returns stats in multiple formats for FE compatibility:
  - Top-level: `views`, `edits`, `last_viewed_at`, `last_edited_at`
  - Nested: `stats` object with all statistics

### 2. Edit Counting (PATCH `/shares/{token}`)
- ✅ Increments `edit_count` atomically when updating a share
- ✅ Updates `last_edited_at` and `payload_updated_at` timestamps
- ✅ Returns stats in response for FE consumption

### 3. POST `/shares/{token}` Support
- ✅ Automatically inherits edit counting (calls PATCH handler)

## ✅ Code Verification

### Placeholder Validation
All SQL queries use correct placeholders:
- ✅ **psycopg2 queries**: Use `%s` (correct for psycopg2)
- ✅ **SQLAlchemy queries**: Use `:named` (correct for SQLAlchemy)

No incorrect placeholders (`:param` or `$1`) found in psycopg2 code.

### Files Modified
1. **`app/main.py`**
   - Added `import json` and `import psycopg2.extras`
   - Modified `get_share_token_only()` to increment views
   - Modified `update_share_token_only()` to increment edits

## 📋 Next Steps

### 1. Run Database Migration

**Important**: The database needs the required columns before the code will work properly.

Run the migration:
```bash
psql $DATABASE_URL -f backend/scripts/add_share_links_stats_columns.sql
```

Or manually execute in your database admin tool (Supabase SQL editor, etc.):
```sql
-- See: backend/scripts/add_share_links_stats_columns.sql
ALTER TABLE public.share_links
  ADD COLUMN IF NOT EXISTS views_count     bigint DEFAULT 0 NOT NULL,
  ADD COLUMN IF NOT EXISTS edit_count      bigint DEFAULT 0 NOT NULL,
  ADD COLUMN IF NOT EXISTS last_viewed_at  timestamptz,
  ADD COLUMN IF NOT EXISTS last_edited_at  timestamptz,
  ADD COLUMN IF NOT EXISTS payload_updated_at timestamptz,
  ADD COLUMN IF NOT EXISTS view_prefs jsonb DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS org_id integer;
```

### 2. Test the Implementation

#### Test View Counting
```bash
# Get a share token first (or use an existing one)
TOKEN="your_share_token_here"

# Call GET endpoint twice - views should increment
curl -s http://localhost:8000/shares/$TOKEN | jq '{views:.views, stats:.stats}'
curl -s http://localhost:8000/shares/$TOKEN | jq '{views:.views, stats:.stats}'
```

#### Test Edit Counting
```bash
TOKEN="your_share_token_here"

# Update the share - edits should increment
curl -s -X PATCH http://localhost:8000/shares/$TOKEN \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"Test Co","employees_count":123}' \
  | jq '.stats'

# Call again - edits should increment again
curl -s -X PATCH http://localhost:8000/shares/$TOKEN \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"Updated Co","employees_count":456}' \
  | jq '.stats'
```

### 3. Verify RLS (Row Level Security)

If you have Row Level Security (RLS) enabled on `share_links` table, ensure the service role can UPDATE the statistics columns:

```sql
-- Check RLS policies
SELECT * FROM pg_policies WHERE tablename = 'share_links';

-- If needed, ensure UPDATE is allowed for service role
-- (Adjust policy names/roles as needed for your setup)
```

## 📊 Response Format

### GET `/shares/{token}` Response
```json
{
  "ok": true,
  "token": "abc123",
  "payload": {...},
  "offers": [...],
  "views": 5,
  "edits": 2,
  "stats": {
    "views": 5,
    "edits": 2,
    "last_viewed_at": "2025-01-15T10:30:00Z",
    "last_edited_at": "2025-01-15T09:15:00Z"
  },
  "last_viewed_at": "2025-01-15T10:30:00Z",
  "last_edited_at": "2025-01-15T09:15:00Z"
}
```

### PATCH `/shares/{token}` Response
```json
{
  "ok": true,
  "token": "abc123",
  "payload": {...},
  "stats": {
    "views": 5,
    "edits": 3,
    "last_viewed_at": "2025-01-15T10:30:00Z",
    "last_edited_at": "2025-01-15T10:35:00Z"
  }
}
```

## 🔍 Frontend Compatibility

The implementation provides stats in multiple places so your FE can pick any format:

- ✅ `views` / `edits` (top-level)
- ✅ `stats.views` / `stats.edits` (nested)
- ✅ `last_viewed_at` / `last_edited_at` (top-level)
- ✅ `stats.last_viewed_at` / `stats.last_edited_at` (nested)

Your FE code that looks for any of these patterns will work:
- `views`, `view_count`, `stats.views`, `analytics.views`, `metrics.views`
- `edits`, `edit_count`, `stats.edits`, `analytics.edits`, `metrics.edits`

## 🛡️ Error Handling

The implementation includes graceful degradation:
- If database update fails, the endpoint continues with cached/fallback values
- Errors are logged but don't crash the request
- Supabase client is used as fallback if direct DB connection fails

## 📝 Notes

1. **Atomic Updates**: All increments use PostgreSQL's atomic `UPDATE ... RETURNING` to prevent race conditions
2. **Total Views (Non-Unique)**: Every GET request increments the counter (as requested - not unique viewers)
3. **Backward Compatible**: Existing API responses are unchanged, stats are additive
4. **Migration File**: `backend/scripts/add_share_links_stats_columns.sql` contains the complete migration

## 🐛 Troubleshooting

### "Column 'views_count' does not exist"
→ Run the database migration (see step 1 above)

### Views/edits not incrementing
→ Check database connection and ensure columns exist
→ Check logs for `[warn] Failed to increment` messages
→ Verify RLS policies allow UPDATE operations

### Stats showing as null/0
→ Ensure migration was run successfully
→ Check that the share record exists in the database
→ Verify database connection is working
