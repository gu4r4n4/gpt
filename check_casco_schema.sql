-- Run this in your database console to see the ACTUAL table schema

SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'offers_casco'
ORDER BY ordinal_position;

-- This will show you EXACTLY which columns exist in production

