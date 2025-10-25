-- Create offer_chunks table for storing document chunks
-- Run this if the table doesn't already exist in your database

-- Create the offer_chunks table
CREATE TABLE IF NOT EXISTS public.offer_chunks (
    id SERIAL PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES public.offer_files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding vector(1536),  -- OpenAI embedding dimension (optional, if using pgvector)
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Ensure unique chunks per file
    UNIQUE(file_id, chunk_index)
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_offer_chunks_file_id 
    ON public.offer_chunks(file_id);

CREATE INDEX IF NOT EXISTS idx_offer_chunks_file_chunk 
    ON public.offer_chunks(file_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_offer_chunks_created_at 
    ON public.offer_chunks(created_at DESC);

-- Optional: If using pgvector for semantic search
-- CREATE INDEX IF NOT EXISTS idx_offer_chunks_embedding 
--     ON public.offer_chunks USING ivfflat (embedding vector_cosine_ops)
--     WITH (lists = 100);

-- Add comment to table
COMMENT ON TABLE public.offer_chunks IS 'Stores text chunks from offer documents for vector search and retrieval';

COMMENT ON COLUMN public.offer_chunks.file_id IS 'Reference to the source file in offer_files table';
COMMENT ON COLUMN public.offer_chunks.chunk_index IS 'Sequential index of the chunk within the file (0-based)';
COMMENT ON COLUMN public.offer_chunks.text IS 'Full text content of the chunk';
COMMENT ON COLUMN public.offer_chunks.metadata IS 'JSON metadata like page number, position, etc.';
COMMENT ON COLUMN public.offer_chunks.embedding IS 'Vector embedding for semantic search (optional)';

-- Create trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_offer_chunks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_offer_chunks_updated_at
    BEFORE UPDATE ON public.offer_chunks
    FOR EACH ROW
    EXECUTE FUNCTION update_offer_chunks_updated_at();

-- Grant permissions (adjust as needed for your setup)
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.offer_chunks TO your_app_user;
-- GRANT USAGE, SELECT ON SEQUENCE offer_chunks_id_seq TO your_app_user;

-- Sample query to verify table creation
-- SELECT COUNT(*) FROM public.offer_chunks;

