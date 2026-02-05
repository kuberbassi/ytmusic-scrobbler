"""
Database layer for YT Music Scrobbler - Supabase REST API Integration
Uses HTTP/HTTPS for connectivity (works regardless of IPv6/DNS issues)
"""

import os
import json
import time
import requests
from typing import Optional, Dict, Any, Tuple, Set
from datetime import datetime, timedelta

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
    Run schema.sql in Supabase SQL Editor to create tables.
    See schema.sql for the full production-ready schema with Google OAuth support.
    """
    print("[INFO] Tables must be created via Supabase Dashboard SQL Editor")
    print("[INFO] Run schema.sql for the complete setup")
    return True


def get_or_create_user_by_google(google_user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Get existing user by Google ID or create a new one.
    This is the PRIMARY method for user identification.
    
    Args:
        google_user: Dict with 'id', 'email', 'name', 'picture' from Google OAuth
    
    Returns:
        User dict with all fields or None on error
    """
    if not REST_API_AVAILABLE:
        return {
            'id': 'local',
            'google_id': google_user.get('id'),
            'google_email': google_user.get('email'),
            'google_name': google_user.get('name')
        }
    
    google_id = google_user.get('id')
    if not google_id:
        print("[ERROR] Google user ID is required")
        return None
    
    try:
        # Try to find existing user by Google ID
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'google_id': f'eq.{google_id}', 'select': '*'},
            headers=get_headers(),
            timeout=10
        )
        
        if response.status_code == 200:
            users = response.json()
            if users:
                # Update user info (name/picture might change)
                user = users[0]
                _update_google_user_info(user['id'], google_user)
                return user
        
        # Create new user
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers=get_headers(),
            json={
                'google_id': google_id,
                'google_email': google_user.get('email', ''),
                'google_name': google_user.get('name', ''),
                'google_picture': google_user.get('picture', ''),
                'settings': {'auto_scrobble': False, 'interval': 300},
                'is_active': True,
                'last_login_at': datetime.utcnow().isoformat()
            },
            timeout=10
        )
        
        if response.status_code in (200, 201):
            users = response.json()
            print(f"[INFO] Created new user: {google_user.get('email')}")
            return users[0] if users else None
        else:
            print(f"[ERROR] Failed to create user: {response.status_code} {response.text[:200]}")
            return None
            
    except requests.RequestException as e:
        print(f"[ERROR] get_or_create_user_by_google failed: {e}")
        return None


def _update_google_user_info(user_id: str, google_user: Dict[str, Any]) -> bool:
    """Update Google user info (name, picture) and last login on login"""
    try:
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'id': f'eq.{user_id}'},
            headers=get_headers(),
            json={
                'google_name': google_user.get('name', ''),
                'google_picture': google_user.get('picture', ''),
                'last_login_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat()
            },
            timeout=5
        )
        return response.status_code in (200, 204)
    except:
        return False


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Get user by their database UUID"""
    if not REST_API_AVAILABLE or not user_id:
        return None
    
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'id': f'eq.{user_id}', 'select': '*'},
            headers=get_headers(),
            timeout=10
        )
        
        if response.status_code == 200:
            users = response.json()
            return users[0] if users else None
        return None
        
    except requests.RequestException as e:
        print(f"[ERROR] get_user_by_id failed: {e}")
        return None


# Legacy function - kept for backwards compatibility
def get_or_create_user(lastfm_username: str) -> Optional[Dict[str, Any]]:
    """
    DEPRECATED: Use get_or_create_user_by_google instead.
    This is kept for backwards compatibility during migration.
    """
    if not REST_API_AVAILABLE:
        return {'id': 'local', 'lastfm_username': lastfm_username}
    
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
        
        # Legacy: Don't create new users via lastfm_username anymore
        # New users must use Google OAuth
        print(f"[WARN] Legacy get_or_create_user called for {lastfm_username} - use Google OAuth")
        return None
            
    except requests.RequestException as e:
        print(f"[ERROR] get_or_create_user failed: {e}")
        return None


def get_all_active_users(batch_size: int = 100, offset: int = 0) -> list:
    """
    Get users with auto_scrobble enabled and is_active=true, with pagination.
    Optimized for 5000+ users.
    
    Args:
        batch_size: Number of users to fetch per batch (default 100)
        offset: Starting offset for pagination
    
    Returns:
        List of active user dicts
    """
    if not REST_API_AVAILABLE:
        return []
    
    try:
        # Use Range header for proper Supabase pagination
        headers = get_headers()
        headers['Range'] = f'{offset}-{offset + batch_size - 1}'
        
        # Calculate cutoff time (4 min ago) to prevent double-sync
        cutoff_time = (datetime.utcnow() - timedelta(minutes=4)).isoformat()
        
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/users",
            params={
                'is_active': 'eq.true',
                'settings->>auto_scrobble': 'eq.true',
                'select': 'id,google_id,google_email,lastfm_username,lastfm_api_key,lastfm_api_secret,lastfm_session_key,ytmusic_headers,settings,last_sync_at',
                'order': 'last_sync_at.asc.nullsfirst',  # Prioritize users who haven't synced recently
                'or': f'(last_sync_at.is.null,last_sync_at.lt.{cutoff_time})'  # Skip users synced in last 4 min
            },
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200 or response.status_code == 206:
            return response.json()
        return []
        
    except requests.RequestException as e:
        print(f"[ERROR] get_all_active_users failed: {e}")
        return []


def get_active_users_count() -> int:
    """Get total count of active users with auto_scrobble enabled"""
    if not REST_API_AVAILABLE:
        return 0
    
    try:
        headers = get_headers()
        headers['Prefer'] = 'count=exact'
        
        response = requests.head(
            f"{SUPABASE_URL}/rest/v1/users",
            params={
                'is_active': 'eq.true',
                'settings->>auto_scrobble': 'eq.true'
            },
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            content_range = response.headers.get('content-range', '')
            # Format: "0-N/total" or "*/total"
            if '/' in content_range:
                return int(content_range.split('/')[-1])
        return 0
        
    except (requests.RequestException, ValueError) as e:
        print(f"[ERROR] get_active_users_count failed: {e}")
        return 0


def iterate_active_users(batch_size: int = 100):
    """
    Generator that yields all active users in batches.
    Memory-efficient for 5000+ users.
    
    Usage:
        for user in iterate_active_users():
            process_user(user)
    """
    offset = 0
    while True:
        batch = get_all_active_users(batch_size=batch_size, offset=offset)
        if not batch:
            break
        
        for user in batch:
            yield user
        
        if len(batch) < batch_size:
            break
        
        offset += batch_size


def update_user_last_sync(user_id: str) -> bool:
    """Update user's last_sync_at timestamp"""
    if not REST_API_AVAILABLE or not user_id:
        return False
    
    try:
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'id': f'eq.{user_id}'},
            headers=get_headers(),
            json={'last_sync_at': datetime.utcnow().isoformat()},
            timeout=5
        )
        return response.status_code in (200, 204)
    except:
        return False


def save_user_credentials(user_id: str, lastfm_config: Dict, ytmusic_headers: str, lastfm_username: str = None) -> bool:
    """Save user's Last.fm and YT Music credentials"""
    if not REST_API_AVAILABLE:
        return False
    
    try:
        update_data = {
            'lastfm_api_key': lastfm_config.get('api_key', ''),
            'lastfm_api_secret': lastfm_config.get('api_secret', ''),
            'lastfm_session_key': lastfm_config.get('session_key', ''),
            'ytmusic_headers': ytmusic_headers,
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # Also save Last.fm username if provided
        if lastfm_username:
            update_data['lastfm_username'] = lastfm_username
        
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/users",
            params={'id': f'eq.{user_id}'},
            headers=get_headers(),
            json=update_data,
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
