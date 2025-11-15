-- Create the offers_casco table with comprehensive schema
-- Run this in your Supabase SQL editor to set up CASCO persistence

CREATE TABLE IF NOT EXISTS public.offers_casco (
    id SERIAL PRIMARY KEY,
    
    -- Core identifiers
    insurer_name TEXT NOT NULL,
    reg_number TEXT NOT NULL,
    insured_entity TEXT,
    inquiry_id INTEGER,
    
    -- Financial data
    insured_amount NUMERIC(12, 2),
    currency TEXT DEFAULT 'EUR',
    premium_total NUMERIC(12, 2),
    premium_breakdown JSONB,
    
    -- Coverage period & territory
    territory TEXT,
    period_from DATE,
    period_to DATE,
    
    -- Structured coverage data (60+ fields)
    coverage JSONB NOT NULL,
    
    -- Audit/debug raw text from GPT extraction
    raw_text TEXT,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_offers_casco_inquiry_id 
    ON public.offers_casco(inquiry_id);
    
CREATE INDEX IF NOT EXISTS idx_offers_casco_reg_number 
    ON public.offers_casco(reg_number);
    
CREATE INDEX IF NOT EXISTS idx_offers_casco_insurer 
    ON public.offers_casco(insurer_name);
    
CREATE INDEX IF NOT EXISTS idx_offers_casco_created 
    ON public.offers_casco(created_at DESC);

-- GIN index for JSONB coverage queries (optional but recommended)
CREATE INDEX IF NOT EXISTS idx_offers_casco_coverage_gin 
    ON public.offers_casco USING gin(coverage);

-- Auto-update updated_at trigger
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

-- Optional: Comment for documentation
COMMENT ON TABLE public.offers_casco IS 
'CASCO (car insurance) offers with hybrid extraction (structured + raw_text). Replaces N8N data.';

-- Grant permissions (adjust as needed for your setup)
-- ALTER TABLE public.offers_casco ENABLE ROW LEVEL SECURITY;

