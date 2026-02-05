-- ============================================================================
-- YT Music Scrobbler - Supabase Schema
-- Run this in Supabase SQL Editor to set up the tables
-- ============================================================================

-- Users table - stores user info and credentials
CREATE TABLE IF NOT EXISTS users (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    lastfm_username VARCHAR(255) UNIQUE NOT NULL,
    
    -- Last.fm credentials
    lastfm_api_key VARCHAR(255),
    lastfm_api_secret VARCHAR(255),
    lastfm_session_key VARCHAR(255),
    
    -- YT Music credentials (browser headers)
    ytmusic_headers TEXT,
    
    -- Settings (JSON: auto_scrobble, interval, etc.)
    settings JSONB DEFAULT '{"auto_scrobble": false, "interval": 300}'::jsonb,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_sync_at TIMESTAMP WITH TIME ZONE
);

-- Scrobbles table - stores per-user scrobble history
CREATE TABLE IF NOT EXISTS scrobbles (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Track info
    track_uid VARCHAR(255) NOT NULL,  -- videoId or title_artist hash
    track_title VARCHAR(500),
    artist VARCHAR(500),
    album VARCHAR(500),
    
    -- Scrobble metadata
    last_scrobble_time BIGINT NOT NULL,  -- Unix timestamp
    scrobble_count INTEGER DEFAULT 1,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Unique constraint: one record per user+track
    UNIQUE(user_id, track_uid)
);

-- Indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_users_lastfm_username ON users(lastfm_username);
CREATE INDEX IF NOT EXISTS idx_scrobbles_user_id ON scrobbles(user_id);
CREATE INDEX IF NOT EXISTS idx_scrobbles_track_uid ON scrobbles(track_uid);
CREATE INDEX IF NOT EXISTS idx_scrobbles_user_track ON scrobbles(user_id, track_uid);

-- Enable Row Level Security
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrobbles ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see/modify their own data
-- For service role (backend), all access is allowed
-- For anon role, we use a simple policy based on user_id passed in

-- Allow insert for anon (new user registration)
CREATE POLICY "Allow insert for all" ON users
    FOR INSERT WITH CHECK (true);

-- Allow select/update for users matching their lastfm_username
CREATE POLICY "Users can view own data" ON users
    FOR SELECT USING (true);

CREATE POLICY "Users can update own data" ON users
    FOR UPDATE USING (true);

-- Scrobbles policies
CREATE POLICY "Allow all scrobbles operations" ON scrobbles
    FOR ALL USING (true);

-- Note: In production, you'd want stricter RLS policies
-- For now, we rely on the backend to manage user isolation
