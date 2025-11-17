-- Check for Database Views or Functions referencing offers_casco with updated_at
-- Run this in your PostgreSQL database console

-- ============================================================================
-- CHECK 1: Views referencing offers_casco
-- ============================================================================
SELECT 
    table_name,
    view_definition
FROM information_schema.views
WHERE table_schema = 'public'
  AND view_definition LIKE '%offers_casco%'
ORDER BY table_name;

-- ============================================================================
-- CHECK 2: Materialized Views
-- ============================================================================
SELECT 
    schemaname,
    matviewname,
    definition
FROM pg_matviews
WHERE schemaname = 'public'
  AND definition LIKE '%offers_casco%';

-- ============================================================================
-- CHECK 3: Stored Procedures and Functions
-- ============================================================================
SELECT 
    routine_name,
    routine_type,
    routine_definition
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND (
    routine_definition LIKE '%offers_casco%'
    OR routine_name LIKE '%casco%'
  )
ORDER BY routine_name;

-- ============================================================================
-- CHECK 4: Triggers on offers_casco
-- ============================================================================
SELECT 
    trigger_name,
    event_manipulation,
    action_statement,
    action_timing
FROM information_schema.triggers
WHERE event_object_schema = 'public'
  AND event_object_table = 'offers_casco'
ORDER BY trigger_name;

-- ============================================================================
-- CHECK 5: Actual table columns (verify updated_at doesn't exist)
-- ============================================================================
SELECT 
    column_name,
    data_type,
    ordinal_position,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'offers_casco'
ORDER BY ordinal_position;

-- ============================================================================
-- CHECK 6: Any rules on the table
-- ============================================================================
SELECT 
    tablename,
    rulename,
    definition
FROM pg_rules
WHERE schemaname = 'public'
  AND tablename = 'offers_casco';

-- ============================================================================
-- EXPECTED RESULTS
-- ============================================================================
-- 
-- CHECK 1-3: Should return EMPTY (no views/functions referencing offers_casco)
-- CHECK 4: Should show update trigger for updated_at (if column exists)
--          OR empty (if column doesn't exist)
-- CHECK 5: Should show EXACTLY 15 columns:
--          1. id
--          2. insurer_name
--          3. reg_number
--          4. insured_entity
--          5. inquiry_id
--          6. insured_amount
--          7. currency
--          8. territory
--          9. period_from
--          10. period_to
--          11. premium_total
--          12. premium_breakdown
--          13. coverage
--          14. raw_text
--          15. created_at
--          
--          If you see 16 columns with "updated_at", that's the problem!
-- CHECK 6: Should be EMPTY (no rules)
-- ============================================================================

