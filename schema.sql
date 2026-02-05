-- ============================================================================
-- YT Music Scrobbler - Supabase Schema (Production Multi-User)
-- Run this in Supabase SQL Editor to set up the tables
-- Optimized for 5000+ users with Google OAuth
-- ============================================================================

-- Drop existing policies if they exist (for clean re-runs)
DROP POLICY IF EXISTS "Service role full access to users" ON users;
DROP POLICY IF EXISTS "Service role full access to scrobbles" ON scrobbles;

-- Users table - Google OAuth as primary identity
CREATE TABLE IF NOT EXISTS users (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    
    -- Google OAuth (primary identity)
    google_id VARCHAR(255) UNIQUE NOT NULL,
    google_email VARCHAR(255) NOT NULL,
    google_name VARCHAR(255),
    google_picture TEXT,
    
    -- Last.fm connection (optional, linked after Google login)
    lastfm_username VARCHAR(255),
    lastfm_api_key VARCHAR(255),
    lastfm_api_secret VARCHAR(255),
    lastfm_session_key VARCHAR(255),
    
    -- YT Music credentials (browser headers - encrypted recommended)
    ytmusic_headers TEXT,
    
    -- Settings (JSON: auto_scrobble, interval, etc.)
    settings JSONB DEFAULT '{"auto_scrobble": false, "interval": 300}'::jsonb,
    
    -- Account status
    is_active BOOLEAN DEFAULT true,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_sync_at TIMESTAMP WITH TIME ZONE,
    last_login_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
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

-- ============================================================================
-- INDEXES (Optimized for 5000+ users)
-- ============================================================================

-- Users table indexes
CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id);
CREATE INDEX IF NOT EXISTS idx_users_google_email ON users(google_email);
CREATE INDEX IF NOT EXISTS idx_users_lastfm_username ON users(lastfm_username) WHERE lastfm_username IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_active_auto_scrobble ON users(is_active, (settings->>'auto_scrobble')) 
    WHERE is_active = true AND (settings->>'auto_scrobble') = 'true';
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);
CREATE INDEX IF NOT EXISTS idx_users_last_sync ON users(last_sync_at) WHERE last_sync_at IS NOT NULL;

-- Scrobbles table indexes
CREATE INDEX IF NOT EXISTS idx_scrobbles_user_id ON scrobbles(user_id);
CREATE INDEX IF NOT EXISTS idx_scrobbles_track_uid ON scrobbles(track_uid);
CREATE INDEX IF NOT EXISTS idx_scrobbles_user_track ON scrobbles(user_id, track_uid);
CREATE INDEX IF NOT EXISTS idx_scrobbles_last_time ON scrobbles(user_id, last_scrobble_time DESC);
CREATE INDEX IF NOT EXISTS idx_scrobbles_created_at ON scrobbles(created_at);

-- ============================================================================
-- FUNCTIONS & TRIGGERS
-- ============================================================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to users table
DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to scrobbles table
DROP TRIGGER IF EXISTS update_scrobbles_updated_at ON scrobbles;
CREATE TRIGGER update_scrobbles_updated_at
    BEFORE UPDATE ON scrobbles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrobbles ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS, so these policies are for extra safety
CREATE POLICY "Service role full access to users" ON users
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Service role full access to scrobbles" ON scrobbles
    FOR ALL USING (true) WITH CHECK (true);

-- ============================================================================
-- USEFUL VIEWS (Optional - for admin dashboard)
-- ============================================================================

-- Active users stats view
CREATE OR REPLACE VIEW active_users_stats AS
SELECT 
    COUNT(*) FILTER (WHERE is_active = true) as total_active,
    COUNT(*) FILTER (WHERE is_active = true AND (settings->>'auto_scrobble') = 'true') as auto_scrobble_enabled,
    COUNT(*) FILTER (WHERE lastfm_username IS NOT NULL) as lastfm_connected,
    COUNT(*) FILTER (WHERE ytmusic_headers IS NOT NULL AND ytmusic_headers != '') as ytmusic_connected,
    COUNT(*) FILTER (WHERE last_sync_at > NOW() - INTERVAL '1 hour') as synced_last_hour,
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as new_today
FROM users;

-- ============================================================================
-- MIGRATION: If upgrading from old schema (lastfm_username based)
-- Run this ONLY if you have existing data to migrate
-- ============================================================================
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255);
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS google_email VARCHAR(255);
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS google_name VARCHAR(255);
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS google_picture TEXT;
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true;
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP WITH TIME ZONE;
-- ALTER TABLE users ALTER COLUMN lastfm_username DROP NOT NULL;
-- UPDATE users SET google_id = 'migrate_' || id::text WHERE google_id IS NULL;
-- UPDATE users SET google_email = lastfm_username || '@migrated.local' WHERE google_email IS NULL;
-- UPDATE users SET is_active = true WHERE is_active IS NULL;
-- ALTER TABLE users ALTER COLUMN google_id SET NOT NULL;
-- ALTER TABLE users ALTER COLUMN google_email SET NOT NULL;
-- CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id_unique ON users(google_id);

