"""
Database layer for YT Music Scrobbler - Supabase REST API Integration
Uses HTTP/HTTPS for connectivity (works regardless of IPv6/DNS issues)
"""

import os
import json
import time
import requests
from typing import Optional, Dict, Any, Tuple, Set
from datetime import datetime

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

# Detect if REST API is available
REST_API_AVAILABLE = bool(SUPABASE_URL and SUPABASE_KEY)


def get_headers() -> Dict[str, str]:
    """Get headers for Supabase REST API requests"""
    return {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }


def is_multi_user_enabled() -> bool:
    """Check if multi-user mode is available via REST API"""
    if not REST_API_AVAILABLE:
        return False
    try:
        # Test connection with a simple query
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/users?select=id&limit=1",
            headers=get_headers(),
            timeout=10
        )
        # 200 = table exists, 404 or other = need to create tables
        if response.status_code == 200:
            print("[INFO] Supabase REST API connected successfully")
            return True
        elif response.status_code == 404:
            print("[INFO] Supabase tables not found - will use file storage")
            return False
        else:
            print(f"[WARN] Supabase REST API returned {response.status_code}: {response.text[:200]}")
            return False
    except requests.RequestException as e:
        print(f"[ERROR] Supabase REST API connection failed: {e}")
        return False


def init_database():
    """
    Note: Table creation via REST API requires the SQL Editor in Supabase Dashboard.
    Run this SQL in Supabase SQL Editor to create tables:
    
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        lastfm_username VARCHAR(255) UNIQUE NOT NULL,
        lastfm_api_key TEXT,
        lastfm_api_secret TEXT,
        lastfm_session_key TEXT,
        ytmusic_headers TEXT,
        settings JSONB DEFAULT '{"auto_scrobble": false, "interval": 300}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    
    CREATE TABLE IF NOT EXISTS scrobbles (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
        track_uid VARCHAR(512) NOT NULL,
        track_title TEXT,
        artist TEXT,
        last_scrobble_time BIGINT,
        scrobble_count INT DEFAULT 1,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, track_uid)
    );
    
    CREATE INDEX IF NOT EXISTS idx_scrobbles_user_id ON scrobbles(user_id);
    CREATE INDEX IF NOT EXISTS idx_users_lastfm_username ON users(lastfm_username);
    
    -- Enable Row Level Security (optional but recommended)
    ALTER TABLE users ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scrobbles ENABLE ROW LEVEL SECURITY;
    
    -- Allow all operations for now (you can add proper policies later)
    CREATE POLICY "Allow all for users" ON users FOR ALL USING (true);
    CREATE POLICY "Allow all for scrobbles" ON scrobbles FOR ALL USING (true);
    """
    print("[INFO] Tables must be created via Supabase Dashboard SQL Editor")
    print("[INFO] See database_rest.py init_database() docstring for SQL")
    return True


def get_or_create_user(lastfm_username: str) -> Optional[Dict[str, Any]]:
    """Get existing user or create a new one"""
    if not REST_API_AVAILABLE:
        return {'id': 'local', 'username': lastfm_username}
    
    try:
        # Try to find existing user
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'lastfm_username': f'eq.{lastfm_username}', 'select': '*'},
            headers=get_headers(),
            timeout=10
        )
        
        if response.status_code == 200:
            users = response.json()
            if users:
                return users[0]
        
        # Create new user
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers=get_headers(),
            json={
                'lastfm_username': lastfm_username,
                'settings': {'auto_scrobble': False, 'interval': 300}
            },
            timeout=10
        )
        
        if response.status_code in (200, 201):
            users = response.json()
            return users[0] if users else None
        else:
            print(f"[ERROR] Failed to create user: {response.status_code} {response.text[:200]}")
            return None
            
    except requests.RequestException as e:
        print(f"[ERROR] get_or_create_user failed: {e}")
        return None


def get_all_active_users() -> list:
    """Get all users with auto_scrobble enabled"""
    if not REST_API_AVAILABLE:
        return []
    
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'settings->>auto_scrobble': 'eq.true', 'select': '*'},
            headers=get_headers(),
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json()
        return []
        
    except requests.RequestException as e:
        print(f"[ERROR] get_all_active_users failed: {e}")
        return []


def save_user_credentials(user_id: str, lastfm_config: Dict, ytmusic_headers: str) -> bool:
    """Save user's Last.fm and YT Music credentials"""
    if not REST_API_AVAILABLE:
        return False
    
    try:
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'id': f'eq.{user_id}'},
            headers=get_headers(),
            json={
                'lastfm_api_key': lastfm_config.get('api_key', ''),
                'lastfm_api_secret': lastfm_config.get('api_secret', ''),
                'lastfm_session_key': lastfm_config.get('session_key', ''),
                'ytmusic_headers': ytmusic_headers,
                'updated_at': datetime.utcnow().isoformat()
            },
            timeout=10
        )
        
        return response.status_code in (200, 204)
        
    except requests.RequestException as e:
        print(f"[ERROR] save_user_credentials failed: {e}")
        return False


def get_user_credentials(user_id: str) -> Optional[Dict[str, Any]]:
    """Get user's credentials and settings"""
    if not REST_API_AVAILABLE:
        return None
    
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/users",
            params={
                'id': f'eq.{user_id}',
                'select': 'lastfm_api_key,lastfm_api_secret,lastfm_session_key,ytmusic_headers,settings'
            },
            headers=get_headers(),
            timeout=10
        )
        
        if response.status_code == 200:
            users = response.json()
            return users[0] if users else None
        return None
        
    except requests.RequestException as e:
        print(f"[ERROR] get_user_credentials failed: {e}")
        return None


def update_user_settings(user_id: str, settings: Dict) -> bool:
    """Update user's settings"""
    if not REST_API_AVAILABLE:
        return False
    
    try:
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'id': f'eq.{user_id}'},
            headers=get_headers(),
            json={
                'settings': settings,
                'updated_at': datetime.utcnow().isoformat()
            },
            timeout=10
        )
        
        return response.status_code in (200, 204)
        
    except requests.RequestException as e:
        print(f"[ERROR] update_user_settings failed: {e}")
        return False


def get_user_scrobble_history(user_id: str) -> Tuple[Set[str], Dict[str, Any]]:
    """Get user's scrobble history"""
    if not REST_API_AVAILABLE:
        return set(), {}
    
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/scrobbles",
            params={
                'user_id': f'eq.{user_id}',
                'select': 'track_uid,track_title,artist,last_scrobble_time,scrobble_count'
            },
            headers=get_headers(),
            timeout=15
        )
        
        if response.status_code == 200:
            history_set = set()
            meta_map = {}
            
            for row in response.json():
                track_uid = row['track_uid']
                history_set.add(track_uid)
                meta_map[track_uid] = {
                    'timestamp': row.get('last_scrobble_time') or 0,
                    'track_title': row.get('track_title') or '',
                    'artist': row.get('artist') or '',
                    'scrobble_count': row.get('scrobble_count') or 1
                }
            
            return history_set, meta_map
        
        return set(), {}
        
    except requests.RequestException as e:
        print(f"[ERROR] get_user_scrobble_history failed: {e}")
        return set(), {}


def save_user_scrobble(user_id: str, track_uid: str, meta: Dict) -> Tuple[Set[str], Dict[str, Any]]:
    """Save a scrobble to the database, handling upsert"""
    if not REST_API_AVAILABLE:
        return set(), {}
    
    current_time = int(time.time())
    
    try:
        # First, try to get existing scrobble
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/scrobbles",
            params={
                'user_id': f'eq.{user_id}',
                'track_uid': f'eq.{track_uid}',
                'select': 'id,scrobble_count'
            },
            headers=get_headers(),
            timeout=10
        )
        
        if response.status_code == 200 and response.json():
            # Update existing scrobble
            existing = response.json()[0]
            new_count = (existing.get('scrobble_count') or 0) + 1
            
            response = requests.patch(
                f"{SUPABASE_URL}/rest/v1/scrobbles",
                params={'id': f"eq.{existing['id']}"},
                headers=get_headers(),
                json={
                    'last_scrobble_time': meta.get('timestamp', current_time),
                    'scrobble_count': new_count,
                    'updated_at': datetime.utcnow().isoformat()
                },
                timeout=10
            )
        else:
            # Insert new scrobble
            response = requests.post(
                f"{SUPABASE_URL}/rest/v1/scrobbles",
                headers=get_headers(),
                json={
                    'user_id': user_id,
                    'track_uid': track_uid,
                    'track_title': meta.get('track_title', ''),
                    'artist': meta.get('artist', ''),
                    'last_scrobble_time': meta.get('timestamp', current_time),
                    'scrobble_count': 1
                },
                timeout=10
            )
        
        # Return updated history
        return get_user_scrobble_history(user_id)
        
    except requests.RequestException as e:
        print(f"[ERROR] save_user_scrobble failed: {e}")
        return set(), {}


# ============================================================================
# File-based storage fallback (single-user mode)
# ============================================================================

class FileStorage:
    """File-based storage for single-user/local mode"""
    
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.config_file = os.path.join(base_dir, "config.json")
        self.scrobbled_file = os.path.join(base_dir, "scrobbled.json")

    def load_config(self) -> Dict:
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_config(self, config: Dict) -> bool:
        try:
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=4)
            return True
        except:
            return False

    def load_scrobbles(self) -> Tuple[Set[str], Dict[str, Any]]:
        if os.path.exists(self.scrobbled_file):
            try:
                with open(self.scrobbled_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data), {}
                    return set(data.get('history', [])), data.get('track_meta', {})
            except:
                return set(), {}
        return set(), {}

    def save_scrobble(self, track_uid: str, meta: Dict) -> Tuple[Set[str], Dict[str, Any]]:
        history, track_meta = self.load_scrobbles()
        history.add(track_uid)
        if meta:
            existing = track_meta.get(track_uid, {})
            existing.update(meta)
            existing['scrobble_count'] = existing.get('scrobble_count', 0) + 1
            track_meta[track_uid] = existing
        try:
            with open(self.scrobbled_file, "w") as f:
                json.dump({'history': list(history), 'track_meta': track_meta}, f)
        except:
            pass
        return history, track_meta


_file_storage: Optional[FileStorage] = None


def get_file_storage() -> FileStorage:
    global _file_storage
    if _file_storage is None:
        _file_storage = FileStorage(os.path.dirname(__file__))
    return _file_storage


# ============================================================================
# UserDataStore - Unified interface for both modes
# ============================================================================

class UserDataStore:
    """Unified data store that works with both REST API and file storage"""
    
    def __init__(self, user_id: Optional[str] = None, lastfm_username: Optional[str] = None):
        self.user_id = user_id
        self.lastfm_username = lastfm_username
        self._use_db = is_multi_user_enabled()
        self._session_scrobbled: Set[str] = set()

    @property
    def is_multi_user(self) -> bool:
        return self._use_db

    def clear_session(self):
        """Clear session-level scrobble tracking"""
        self._session_scrobbled.clear()

    def mark_session_scrobbled(self, track_uid: str):
        """Mark a track as scrobbled in current session"""
        self._session_scrobbled.add(track_uid)

    def is_session_scrobbled(self, track_uid: str) -> bool:
        """Check if track was scrobbled in current session"""
        return track_uid in self._session_scrobbled

    def get_config(self) -> Dict:
        """Get user configuration"""
        if self._use_db and self.user_id:
            creds = get_user_credentials(self.user_id)
            if creds:
                settings = creds.get('settings', {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                return {
                    'lastfm': {
                        'api_key': creds.get('lastfm_api_key', ''),
                        'api_secret': creds.get('lastfm_api_secret', ''),
                        'session_key': creds.get('lastfm_session_key', '')
                    },
                    'ytmusic': {
                        'headers': creds.get('ytmusic_headers', '')
                    },
                    'auto_scrobble': settings.get('auto_scrobble', False),
                    'interval': settings.get('interval', 300)
                }
        return get_file_storage().load_config()

    def save_config(self, config: Dict) -> bool:
        """Save user configuration"""
        if self._use_db and self.user_id:
            lastfm = config.get('lastfm', {})
            ytmusic = config.get('ytmusic', {})
            settings = {
                'auto_scrobble': config.get('auto_scrobble', False),
                'interval': config.get('interval', 300)
            }
            save_user_credentials(self.user_id, lastfm, ytmusic.get('headers', ''))
            update_user_settings(self.user_id, settings)
            return True
        return get_file_storage().save_config(config)

    def get_scrobble_history(self) -> Tuple[Set[str], Dict[str, Any]]:
        """Get user's scrobble history"""
        if self._use_db and self.user_id:
            return get_user_scrobble_history(self.user_id)
        return get_file_storage().load_scrobbles()

    def save_scrobble(self, track_uid: str, meta: Dict) -> Tuple[Set[str], Dict[str, Any]]:
        """Save a scrobble"""
        self._session_scrobbled.add(track_uid)
        if self._use_db and self.user_id:
            return save_user_scrobble(self.user_id, track_uid, meta)
        return get_file_storage().save_scrobble(track_uid, meta)


# Test connection on module load
if REST_API_AVAILABLE:
    print(f"[INFO] Supabase REST API configured: {SUPABASE_URL}")
else:
    print("[INFO] Supabase REST API not configured - using file storage")
