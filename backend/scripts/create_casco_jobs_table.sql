-- Create casco_jobs table for CASCO job tracking
-- This replaces dependency on inquiry_id with an internal job system
-- Uses UUID as PRIMARY KEY to match HEALTH architecture

CREATE TABLE IF NOT EXISTS public.casco_jobs (
    casco_job_id TEXT PRIMARY KEY,
    reg_number TEXT NOT NULL,
    product_line TEXT DEFAULT 'casco' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS idx_casco_jobs_reg_number 
    ON public.casco_jobs(reg_number);

CREATE INDEX IF NOT EXISTS idx_casco_jobs_created_at 
    ON public.casco_jobs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_casco_jobs_product_line 
    ON public.casco_jobs(product_line);

-- Add casco_job_id to offers_casco
ALTER TABLE public.offers_casco 
    ADD COLUMN IF NOT EXISTS casco_job_id TEXT;

-- Create foreign key
ALTER TABLE public.offers_casco 
    ADD CONSTRAINT offers_casco_casco_job_id_fkey 
    FOREIGN KEY (casco_job_id) 
    REFERENCES public.casco_jobs(casco_job_id) 
    ON DELETE CASCADE;

-- Index for quick filtering by job
CREATE INDEX IF NOT EXISTS idx_offers_casco_casco_job_id 
    ON public.offers_casco(casco_job_id);

-- Comments
COMMENT ON TABLE public.casco_jobs IS 
'CASCO job tracking - each upload creates a new job with UUID. Replaces inquiry_id dependency.';

COMMENT ON COLUMN public.casco_jobs.casco_job_id IS 
'UUID string identifier for the job. Generated on upload.';

COMMENT ON COLUMN public.offers_casco.casco_job_id IS 
'Links to casco_jobs.casco_job_id - all offers in one upload share the same job ID.';

