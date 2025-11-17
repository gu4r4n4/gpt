-- OPTION A: Add updated_at to match schema script
-- Run this if you WANT updated_at tracking

BEGIN;

-- Add the column
ALTER TABLE public.offers_casco 
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Backfill existing rows
UPDATE public.offers_casco 
SET updated_at = created_at 
WHERE updated_at IS NULL;

-- Add trigger function
CREATE OR REPLACE FUNCTION update_offers_casco_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Add trigger
DROP TRIGGER IF EXISTS trigger_update_offers_casco_updated_at ON public.offers_casco;
CREATE TRIGGER trigger_update_offers_casco_updated_at
    BEFORE UPDATE ON public.offers_casco
    FOR EACH ROW
    EXECUTE FUNCTION update_offers_casco_updated_at();

COMMIT;

-- Verify
SELECT 
    id,
    insurer_name,
    created_at,
    updated_at  -- This should now work
FROM public.offers_casco 
LIMIT 5;

