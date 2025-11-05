-- Migration: Add view and edit counting columns to share_links table
-- Run this migration to add support for view/edit statistics tracking
--
-- Usage:
--   psql $DATABASE_URL -f backend/scripts/add_share_links_stats_columns.sql
--   Or run in your database admin tool (Supabase SQL editor, etc.)

-- Add columns for view counting
ALTER TABLE public.share_links
  ADD COLUMN IF NOT EXISTS views_count     bigint DEFAULT 0 NOT NULL,
  ADD COLUMN IF NOT EXISTS edit_count      bigint DEFAULT 0 NOT NULL,
  ADD COLUMN IF NOT EXISTS last_viewed_at  timestamptz,
  ADD COLUMN IF NOT EXISTS last_edited_at  timestamptz,
  ADD COLUMN IF NOT EXISTS payload_updated_at timestamptz;

-- Ensure view_prefs column exists (used for FE preferences)
ALTER TABLE public.share_links
  ADD COLUMN IF NOT EXISTS view_prefs jsonb DEFAULT '{}'::jsonb;

-- Ensure org_id column exists (for organization isolation)
ALTER TABLE public.share_links
  ADD COLUMN IF NOT EXISTS org_id integer;

-- Create index on org_id for faster queries
CREATE INDEX IF NOT EXISTS idx_share_links_org_id ON public.share_links(org_id);

-- Create index on token for faster lookups (if not already exists)
CREATE INDEX IF NOT EXISTS idx_share_links_token ON public.share_links(token);

-- Add comments for documentation
COMMENT ON COLUMN public.share_links.views_count IS 'Total number of times this share has been viewed (non-unique)';
COMMENT ON COLUMN public.share_links.edit_count IS 'Total number of times this share has been edited';
COMMENT ON COLUMN public.share_links.last_viewed_at IS 'Timestamp of the most recent view';
COMMENT ON COLUMN public.share_links.last_edited_at IS 'Timestamp of the most recent edit';
COMMENT ON COLUMN public.share_links.payload_updated_at IS 'Timestamp when payload was last updated';
COMMENT ON COLUMN public.share_links.view_prefs IS 'Frontend view preferences (column order, hidden rows, etc.) stored as JSONB';
COMMENT ON COLUMN public.share_links.org_id IS 'Organization ID for multi-tenant isolation';

-- Verify columns were added (optional - uncomment to run)
-- SELECT column_name, data_type, is_nullable, column_default
-- FROM information_schema.columns
-- WHERE table_schema = 'public' AND table_name = 'share_links'
-- ORDER BY ordinal_position;
