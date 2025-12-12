-- Create admin chat tables for storing chat sessions and messages
-- Run this in your Supabase SQL editor or via psql

-- Table: admin_chat_sessions
CREATE TABLE IF NOT EXISTS public.admin_chat_sessions (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL,
    created_by_user_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'admin_ui',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_activity_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for admin_chat_sessions
CREATE INDEX IF NOT EXISTS idx_admin_chat_sessions_org_id 
    ON public.admin_chat_sessions(org_id);
    
CREATE INDEX IF NOT EXISTS idx_admin_chat_sessions_user_id 
    ON public.admin_chat_sessions(created_by_user_id);
    
CREATE INDEX IF NOT EXISTS idx_admin_chat_sessions_last_activity 
    ON public.admin_chat_sessions(last_activity_at DESC);

-- Table: admin_chat_messages
CREATE TABLE IF NOT EXISTS public.admin_chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES public.admin_chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for admin_chat_messages
CREATE INDEX IF NOT EXISTS idx_admin_chat_messages_session_id 
    ON public.admin_chat_messages(session_id);
    
CREATE INDEX IF NOT EXISTS idx_admin_chat_messages_created_at 
    ON public.admin_chat_messages(created_at DESC);

-- Optional: Comments for documentation
COMMENT ON TABLE public.admin_chat_sessions IS 
'Admin chat sessions linking users to their conversation history';

COMMENT ON TABLE public.admin_chat_messages IS 
'Individual messages within admin chat sessions';

