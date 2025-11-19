-- Migration: Add product_line to CASCO tables
-- This enables filtering and routing between CASCO and HEALTH products

-- 1. Add product_line to offers_casco (if not exists)
ALTER TABLE public.offers_casco 
ADD COLUMN IF NOT EXISTS product_line TEXT DEFAULT 'casco';

-- 2. Add product_line to offer_files (if not exists)
ALTER TABLE public.offer_files 
ADD COLUMN IF NOT EXISTS product_line TEXT DEFAULT 'health';

-- 3. Add product_line to share_links (if not exists)
ALTER TABLE public.share_links 
ADD COLUMN IF NOT EXISTS product_line TEXT DEFAULT 'health';

-- 4. Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_offers_casco_product_line 
    ON public.offers_casco(product_line);

CREATE INDEX IF NOT EXISTS idx_offer_files_product_line 
    ON public.offer_files(product_line);

CREATE INDEX IF NOT EXISTS idx_share_links_product_line 
    ON public.share_links(product_line);

-- 5. Update existing CASCO records to have product_line='casco'
UPDATE public.offers_casco 
SET product_line = 'casco' 
WHERE product_line IS NULL;

-- 6. Comments for documentation
COMMENT ON COLUMN public.offers_casco.product_line IS 
'Product type: casco, health, travel, etc. Defaults to casco for this table.';

COMMENT ON COLUMN public.offer_files.product_line IS 
'Product type: casco, health, travel, etc. Used for filtering files by product.';

COMMENT ON COLUMN public.share_links.product_line IS 
'Product type for share link: casco or health. Determines which comparison logic to use.';

