from flask import Flask, request, jsonify, render_template, redirect, session, url_for, send_from_directory
import os
import json
import re
import hashlib
import time
import urllib.parse
from datetime import datetime
from functools import wraps
from collections import defaultdict
import pylast
from ytmusicapi import YTMusic
import secrets
import requests
import threading

# Import database layer (multi-user support)
try:
    from api.database import (
        UserDataStore, is_multi_user_enabled, get_or_create_user,
        get_all_active_users, get_file_storage, get_or_create_user_by_google,
        get_user_by_id, iterate_active_users, get_active_users_count,
        update_user_last_sync
    )
except ImportError:
    from database import (
        UserDataStore, is_multi_user_enabled, get_or_create_user,
        get_all_active_users, get_file_storage, get_or_create_user_by_google,
        get_user_by_id, iterate_active_users, get_active_users_count,
        update_user_last_sync
    )

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
# Production URI by default - override with GOOGLE_REDIRECT_URI env var for local dev
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'https://ytscrobbler.kuberbassi.com/auth/google/callback')

# Global Sync State
scrobble_lock = threading.Lock()  # Lock for individual scrobble calls
sync_operation_lock = threading.Lock()  # Lock for entire sync operations (prevents overlapping syncs)
last_sync_time = 0
sync_logs = []  # List of [timestamp, artist, title, status]

def add_sync_log(artist, title, status="Synced", user=None):
    global sync_logs
    entry = {
        'time': int(time.time()),
        'artist': artist,
        'title': title,
        'status': status,
        'user': user  # Track which user scrobbled (for multi-user)
    }
    sync_logs.insert(0, entry)
    sync_logs = sync_logs[:50]  # Keep last 50 for multi-user

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
template_dir = os.path.join(BASE_DIR, 'templates')
static_dir = os.path.join(BASE_DIR, 'static')

app = Flask(
    __name__,
    template_folder=template_dir,
    static_folder=static_dir,
    static_url_path='/static'
)

# Build a STABLE secret key so sessions survive serverless cold starts.
# If SECRET_KEY env var isn't set, derive a deterministic key from other
# stable secrets so users don't get logged out every deployment/cold start.
_secret_key = os.environ.get('SECRET_KEY', '')
if not _secret_key:
    _key_source = os.environ.get('GOOGLE_CLIENT_SECRET', '') + os.environ.get('SUPABASE_KEY', '')
    if _key_source.strip():
        _secret_key = hashlib.sha256(f'ytscrobbler-session:{_key_source}'.encode()).hexdigest()
    else:
        # Local dev only fallback — set SECRET_KEY in production!
        _secret_key = 'dev-only-insecure-key-set-SECRET_KEY-in-production'
        print('[WARN] SECRET_KEY not set and no stable env vars found. Sessions will NOT persist across restarts.')
app.secret_key = _secret_key

# Session cookie hardening — keeps users logged in across visits
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('SECRET_KEY'))  # Secure in prod, not in local http
app.config['PERMANENT_SESSION_LIFETIME'] = 30 * 24 * 60 * 60  # 30 days

# =============================================================================
# SECURITY & RATE LIMITING
# =============================================================================

# Rate limiting storage (in-memory, resets on cold start - acceptable for serverless)
rate_limit_store = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = {
    'default': 60,      # 60 requests per minute for most endpoints
    'scrobble': 10,     # 10 scrobbles per minute
    'auth': 5,          # 5 auth attempts per minute
    'cron': 2,          # 2 cron calls per minute
}

def get_client_ip():
    """Get real client IP, accounting for proxies"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'

def check_rate_limit(endpoint_type='default'):
    """Check if request should be rate limited. Returns (allowed, retry_after)"""
    ip = get_client_ip()
    key = f"{ip}:{endpoint_type}"
    now = time.time()
    
    # Clean old entries
    rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < RATE_LIMIT_WINDOW]
    
    max_requests = RATE_LIMIT_MAX_REQUESTS.get(endpoint_type, RATE_LIMIT_MAX_REQUESTS['default'])
    
    if len(rate_limit_store[key]) >= max_requests:
        oldest = rate_limit_store[key][0] if rate_limit_store[key] else now
        retry_after = int(RATE_LIMIT_WINDOW - (now - oldest)) + 1
        return False, retry_after
    
    rate_limit_store[key].append(now)
    return True, 0

def rate_limit(endpoint_type='default'):
    """Decorator for rate limiting endpoints"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            allowed, retry_after = check_rate_limit(endpoint_type)
            if not allowed:
                response = jsonify({'error': 'Rate limit exceeded', 'retry_after': retry_after})
                response.status_code = 429
                response.headers['Retry-After'] = str(retry_after)
                return response
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def require_login(f):
    """Decorator to require Google login for endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP for HTML responses only
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://accounts.google.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https: blob:; "
            "connect-src 'self' https://accounts.google.com https://oauth2.googleapis.com https://vitals.vercel-insights.com; "
            "frame-ancestors 'none';"
        )
    return response


# =============================================================================
# CORE SCROBBLE LOGIC (Shared between single and multi-user)
# =============================================================================

# Common YT Music title suffixes that Last.fm (or other scrobblers) typically strip.
_TITLE_VARIANT_RE = re.compile(
    r'\s*[\(\[]\s*(?:'
    r'official\s+(?:video|audio|music\s*video|mv|lyric\s*video)|'
    r'lyric\s*(?:video|s)?|'
    r'\d{4}\s*remaster(?:ed)?(?:\s*version)?|'
    r'remaster(?:ed)?(?:\s*\d{4})?(?:\s*version)?|'
    r'instrumental(?:\s*version)?|'
    r'live(?:\s+(?:version|at\s+.{0,30}))?|'
    r'(?:[\w\s&]+\s+)?remix|'
    r'(?:[\w\s&]+\s+)?edit|'
    r'extended(?:\s+(?:version|mix))?|'
    r'radio\s+edit|'
    r'acoustic(?:\s*version)?|'
    r'explicit|clean(?:\s+version)?|'
    r'visualizer|'
    r'vevo|hd|4k|hq'
    r')\s*[\)\]]',
    re.IGNORECASE
)

# Separators used in multi-artist strings from YT Music
_ARTIST_SPLIT_RE = re.compile(r'\s*(?:,|&|\bx\b|\band\b)\s*', re.IGNORECASE)


def strip_title_variants(title: str) -> str:
    """
    Strip common YT Music parenthetical/bracketed suffixes to get the base title.
    e.g. "Song (2024 Remastered Version)" → "Song"
         "Song [Official Video]" → "Song"
    Iterates until stable so nested variants are also handled.
    """
    if not title:
        return ""
    t = title.strip()
    # Strip common unparenthesized trailing noise like "- Official Audio", " - Topic"
    t = re.sub(r'\s*-\s*(?:official\s+(?:video|audio|music\s*video|lyric\s*video)|topic|lyric\s*video|visualizer)$', '', t, flags=re.IGNORECASE).strip()
    for _ in range(5):
        stripped = _TITLE_VARIANT_RE.sub('', t).strip()
        if stripped == t:
            break
        t = stripped
    return t


def extract_primary_artist(artist: str) -> str:
    """
    Extract the primary (first) artist from a multi-artist string.
    YT Music: "40K, Sharn, The Paul, and Bohemia" → "40K"
    Last.fm stores only the primary artist, so we need the same reduction
    on both sides to produce matching UIDs.
    """
    if not artist:
        return ""
    # Strip " - Topic" from artist name if present
    clean_a = re.sub(r'\s*-\s*topic$', '', artist.strip(), flags=re.IGNORECASE).strip()
    parts = _ARTIST_SPLIT_RE.split(clean_a)
    return parts[0].strip() if parts else clean_a


def normalize_string(s: str) -> str:
    """Normalize a string for consistent comparison across platforms"""
    if not s:
        return ""
    # Lowercase & strip spaces
    s = s.lower().strip()
    # Normalize unicode apostrophes, dashes, and quotes
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    # Remove featured artist tags
    for feat in [' feat.', ' feat ', ' ft.', ' ft ', ' featuring ', ' (feat.', ' [feat.']:
        if feat in s:
            s = s.split(feat)[0].strip()
    # Keep alphanumeric characters and spaces only
    s = ''.join(c for c in s if c.isalnum() or c == ' ')
    s = ' '.join(s.split())
    return s


def generate_track_uids(title: str, artist: str, video_id: str = None) -> list:
    """
    Generate multiple UIDs for a track for comprehensive deduplication.
    Produces UIDs with both the full artist string AND the primary artist
    alone, so YT Music multi-artist strings match Last.fm single-artist entries.
    ANY match → already scrobbled.
    """
    uids = []

    # 1. Video ID — most precise
    if video_id and str(video_id).strip() and str(video_id) != 'no-id':
        uids.append(f"vid:{str(video_id).strip()}")

    primary_artist = extract_primary_artist(artist)
    clean_title = strip_title_variants(title)

    def _add_variants(t, a):
        """Add exact + normalized UIDs for a (title, artist) pair."""
        if t and a:
            uids.append(f"{t}_{a}")
        norm_t = normalize_string(t)
        norm_a = normalize_string(a)
        if norm_t and norm_a:
            n_uid = f"norm:{norm_t}_{norm_a}"
            if n_uid not in uids:
                uids.append(n_uid)

    # 2. Full artist string (as returned by API)
    _add_variants(title, artist)

    # 3. Primary artist only — critical for YT multi-artist vs Last.fm single-artist
    if primary_artist and primary_artist.lower() != artist.lower():
        _add_variants(title, primary_artist)

    # 4. Clean title (YT suffix stripped) + full artist
    if clean_title and clean_title.lower() != title.lower():
        _add_variants(clean_title, artist)
        # 5. Clean title + primary artist (widest net)
        if primary_artist and primary_artist.lower() != artist.lower():
            _add_variants(clean_title, primary_artist)

    return uids


_NON_MUSIC_KEYWORDS = re.compile(
    r'\b(?:'
    r'react(?:ion|ing|s)?|'
    r'review(?:ing|s)?|'
    r'unboxing|'
    r'vlog(?:s)?|'
    r'tier\s*list|'
    r'interview|'
    r'podcast|'
    r'commentary|'
    r'gameplay|'
    r'walkthrough|'
    r'playthrough|'
    r'lets\s*play|'
    r'try\s*not\s*to|'
    r'challenge|'
    r'q&a|'
    r'behind\s*the\s*scenes'
    r')\b',
    re.IGNORECASE
)

_MUSIC_EXEMPT_KEYWORDS = re.compile(
    r'\b(?:'
    r'cover|instrumental|remix|acoustic|rework|bootleg|flip|mashup|'
    r'live|performance|session|unplugged|rendition|reprise|medley|'
    r'jam|tribute|orchestra|symphony|version|official\s*video|'
    r'official\s*audio|playalong|bass\s*cover|guitar\s*cover|drum\s*cover|'
    r'studio\s*version|live\s*version|acoustic\s*version'
    r')\b',
    re.IGNORECASE
)

def is_music_content(item: dict) -> bool:
    """
    Filter out non-music YouTube content (reaction videos, vlogs, podcasts, reviews)
    while preserving actual music (official songs, music videos, covers, live performances, instrumental covers).
    """
    if not isinstance(item, dict):
        return True
        
    title = item.get('title', '')
    artists = item.get('artists', [])
    artist = artists[0].get('name', '') if artists else item.get('author', '')
    album = item.get('album', {}).get('name', '') if item.get('album') else ''
    result_type = item.get('resultType') or item.get('category')
    
    # 1. Has explicit album metadata or SONG resultType -> Genuine Music
    if album and str(album).strip():
        return True
    if result_type in ('SONG', 'MUSIC_VIDEO_TYPE_ATV', 'MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC'):
        return True

    title_lower = str(title).lower()

    # 2. Music-related keywords (e.g. "Davie504 - RHCP Bass Cover", "Guitar Cover", "Live") -> Always Music!
    if _MUSIC_EXEMPT_KEYWORDS.search(title_lower):
        return True

    # 3. Non-music video keywords (e.g. "Davie504 Reaction to 100 Basslines", "Album Review", "Unboxing") -> Skip!
    if _NON_MUSIC_KEYWORDS.search(title_lower):
        return False

    return True


global_scrobble_session_cache = {}  # In-process cache of scrobbled UIDs -> timestamp
SCROBBLE_COOLDOWN_SECONDS = 20 * 60  # 20 minutes cooldown window

def is_track_scrobbled(track_uids: list, track_meta_map: dict, data_store=None, cooldown_seconds=SCROBBLE_COOLDOWN_SECONDS) -> tuple:
    """
    Check if a track has been scrobbled in the current session or within the recent cooldown window (20 min).
    Returns: (is_scrobbled: bool, matching_uid: str or None)
    """
    now = int(time.time())
    for uid in track_uids:
        # 1. Check in-process global session cache (catches instant single-scrobbles)
        if uid in global_scrobble_session_cache:
            ts = global_scrobble_session_cache[uid]
            if now - ts < cooldown_seconds:
                return True, uid

        # 2. Check session storage (scrobbled during active sync session)
        if data_store and data_store.is_session_scrobbled(uid):
            return True, uid
            
        # 3. Check persistent storage (only if scrobbled within cooldown window)
        meta = track_meta_map.get(uid)
        if meta:
            timestamp = meta.get('timestamp', 0)
            try:
                ts = int(timestamp)
            except (TypeError, ValueError):
                ts = 0
            if ts > 0 and (now - ts < cooldown_seconds):
                return True, uid
                
    return False, None


def should_scrobble(track_uid, track_meta_map, current_time, duration, position=0, data_store=None):
    """
    Determine if a track should be scrobbled.
    
    IMPORTANT: YT Music API does NOT provide real-time playback status.
    We cannot detect:
    - If music is currently playing or paused
    - When playback stopped
    - If a song is actually being replayed vs just sitting in history
    
    Therefore, we ONLY scrobble first plays. No repeat detection.
    This prevents false scrobbles when user stops listening.
    
    Args:
        track_uid: Unique identifier for the track
        track_meta_map: Metadata dict with timestamps
        current_time: Current unix timestamp
        duration: Track duration in seconds (unused, kept for compatibility)
        position: Position in history (0 = most recent)
        data_store: UserDataStore instance for session tracking
    
    Returns: (should_scrobble: bool, reason: str)
    """
    # Guard 1: Already scrobbled in this sync session (prevents multi-scrobble bug)
    if data_store and data_store.is_session_scrobbled(track_uid):
        return False, "already_in_session"
    
    # Check if track exists in our history
    meta = track_meta_map.get(track_uid)
    
    # Case 1: Never scrobbled before - scrobble it
    if meta is None:
        return True, "first_play"
    
    last_scrobble_time = meta.get('timestamp', 0)
    
    # Case 2: No timestamp recorded - allow (legacy data migration)
    if last_scrobble_time == 0:
        return True, "no_timestamp"
    
    # Case 3: Already scrobbled - do NOT scrobble again
    # We cannot reliably detect repeats without real-time playback data
    return False, "already_scrobbled"


def get_track_duration(yt_track):
    """Safely extract duration in seconds from YTMusic track object"""
    try:
        # Check integer field first
        if 'duration_seconds' in yt_track:
            return int(yt_track['duration_seconds'])
            
        duration_str = yt_track.get('duration')
        if not duration_str: return 180  # Default 3 mins
        if ':' in duration_str:
            parts = list(map(int, duration_str.split(':')))
            if len(parts) == 2: return parts[0] * 60 + parts[1]
            if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return int(duration_str)
    except:
        return 180


# =============================================================================
# LEGACY COMPATIBILITY WRAPPERS (For backward compatibility with single-user)
# =============================================================================

def load_scrobbles():
    """Legacy wrapper - loads from file storage"""
    return get_file_storage().load_scrobbles()


def save_scrobble(track_uid, meta=None):
    """Legacy wrapper - saves to file storage"""
    return get_file_storage().save_scrobble(track_uid, meta)


# Configuration Persistence (now uses database for multi-user)
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")


class ConfigManager:
    """
    Configuration manager that supports both single-user (file) and multi-user (database).
    """
    
    @staticmethod
    def load(user_id=None):
        """Load config - from DB if multi-user enabled, else from file"""
        if is_multi_user_enabled() and user_id:
            store = UserDataStore(user_id=user_id)
            return store.get_config()
        
        # Fallback to file
        return get_file_storage().load_config()
    
    @staticmethod
    def save(config, user_id=None):
        """Save config - to DB if multi-user enabled, else to file"""
        if is_multi_user_enabled() and user_id:
            store = UserDataStore(user_id=user_id)
            store.save_config(config)
            return
        
        # Fallback to file
        get_file_storage().save_config(config)
    
    @staticmethod
    def get_user_from_session(session_key, api_key, api_secret):
        """
        Get or create user based on Last.fm session. Returns (user_id, username).
        """
        if not is_multi_user_enabled():
            return None, None
        
        try:
            # Get Last.fm username from session
            network = pylast.LastFMNetwork(
                api_key=api_key,
                api_secret=api_secret,
                session_key=session_key
            )
            username = str(network.get_authenticated_user())
            
            # Get or create user in database
            user = get_or_create_user(username)
            if user:
                return user.get('id'), username
        except Exception as e:
            print(f"[WARN] Failed to get user from session: {e}")
# Scrobble tracking handled by persistent scrobbled.json


def _enrich_config_from_db(config: dict, user_id) -> dict:
    """
    If the client-supplied config is missing Last.fm or YT Music credentials,
    fall back to the credentials stored in the user's DB profile.
    This handles the case where localStorage is stale/cleared but the user
    is still logged-in via Google OAuth and has saved credentials in the DB.
    """
    if not user_id:
        return config
    needs_lastfm = not config.get('lastfm', {}).get('api_key')
    needs_ytmusic = not config.get('ytmusic', {}).get('headers')
    if not needs_lastfm and not needs_ytmusic:
        return config  # Nothing to fill in
    try:
        store = UserDataStore(user_id=user_id)
        db_cfg = store.get_config()
        merged = dict(config)
        if needs_lastfm and db_cfg.get('lastfm', {}).get('api_key'):
            merged['lastfm'] = db_cfg['lastfm']
        if needs_ytmusic and db_cfg.get('ytmusic', {}).get('headers'):
            merged['ytmusic'] = db_cfg['ytmusic']
        return merged
    except Exception as e:
        print(f"[WARN] _enrich_config_from_db failed: {e}")
        return config


def get_lastfm_network(config):
    """Initialize Last.fm network connection"""
    lastfm_config = config.get('lastfm', {})
    
    api_key = lastfm_config.get('api_key') or os.environ.get('LASTFM_API_KEY')
    api_secret = lastfm_config.get('api_secret') or os.environ.get('LASTFM_API_SECRET')
    session_key = lastfm_config.get('session_key') or os.environ.get('LASTFM_SESSION_KEY')
    
    if not all([api_key, api_secret]):
        return None, "Missing API credentials"
    
    if not session_key:
        return None, "Not authorized"
    
    try:
        network = pylast.LastFMNetwork(
            api_key=api_key,
            api_secret=api_secret,
            session_key=session_key
        )
        return network, None
    except Exception as e:
        return None, str(e)


def parse_browser_headers(header_str):
    """The 'Final BOSS' Parser: Scans the entire text for auth nuggets regardless of formatting"""
    if not header_str:
        return None
        
    headers = {}
    targets = ['cookie', 'authorization', 'user-agent', 'origin', 'referer', 'accept']
    for t in targets:
        # Match "key: value" or "key\nvalue"
        pattern = rf'(?i)(?:^|[\n\r]){t}[:\s]+([^\n\r]+)'
        match = re.search(pattern, header_str)
        if match:
            headers[t] = match.group(1).strip()
            
    # 2. X-Goog, X-Youtube, and other identity headers
    # Broad capture for anything starting with x- and following some valid pattern
    identity_headers = re.findall(r'(?i)(x-[a-z0-9-]+)[:\s]+([^\n\r]+)', header_str)
    for k, v in identity_headers:
        headers[k.lower()] = v.strip()

    # 3. Emergency Cookie Rescue: Strictly hunt for __Secure-3PAPISID
    if 'cookie' not in headers or '__Secure-3PAPISID' not in headers['cookie']:
        sid_match = re.search(r'__Secure-3PAPISID=([^;]+)', header_str)
        if sid_match:
            val = sid_match.group(1).strip()
            if 'cookie' not in headers:
                headers['cookie'] = f'__Secure-3PAPISID={val};'
            elif '__Secure-3PAPISID' not in headers['cookie']:
                headers['cookie'] += f' __Secure-3PAPISID={val};'

    # Fallback Origin
    if 'origin' not in headers:
        headers['origin'] = 'https://music.youtube.com'

    return headers if headers else None

def get_ytmusic_client(config):
    """Initialize YT Music client using browser headers"""
    ytmusic_config = config.get('ytmusic', {})
    
    # Prioritize Browser Headers
    if 'headers' in ytmusic_config:
        try:
            headers = parse_browser_headers(ytmusic_config['headers'])
            if headers:
                yt_headers = requests.structures.CaseInsensitiveDict(headers)
                return YTMusic(auth=yt_headers), None
            else:
                return None, "Invalid header format"
        except Exception as e:
            print(f"[DEBUG] Header auth failed: {e}")
            return None, f"Header error: {str(e)}"

    if not ytmusic_config:
        return None, "Not configured"
    
    try:
        if 'cookie' in ytmusic_config or 'Cookie' in ytmusic_config:
            return YTMusic(auth=ytmusic_config), None
        else:
            return None, "Please connect with browser headers"
    except Exception as e:
        return None, str(e)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/robots.txt')
def robots():
    return "User-agent: *\nAllow: /\nSitemap: https://ytscrobbler.kuberbassi.com/sitemap.xml", 200, {'Content-Type': 'text/plain'}

@app.route('/sitemap.xml')
def sitemap():
    return '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ytscrobbler.kuberbassi.com/</loc><priority>1.0</priority></url>
  <url><loc>https://ytscrobbler.kuberbassi.com/privacy</loc><priority>0.5</priority></url>
  <url><loc>https://ytscrobbler.kuberbassi.com/terms</loc><priority>0.5</priority></url>
</urlset>''', 200, {'Content-Type': 'application/xml'}


# Terms and Privacy routes use templates/legal.html

@app.route('/terms')
def terms():
    content = '''
        <p><strong>1. Service Description:</strong> YT Music Scrobbler automatically syncs your YouTube Music listening history to Last.fm across all your devices.</p>
        <p><strong>2. Account & Data:</strong> Your credentials and listening history are stored securely per-user in Supabase and are never shared or sold.</p>
        <p><strong>3. Acceptable Use:</strong> You agree not to abuse the service, bypass rate limits, or attempt unauthorized data access.</p>
        <p><strong>4. Availability:</strong> The service is provided "as is" with no uptime guarantees.</p>
        <p><strong>5. Contact:</strong> For inquiries or data deletion, contact <a href="https://kuberbassi.com" target="_blank">kuberbassi.com</a>.</p>
    '''
    return render_template('legal.html', title="Terms of Service", path="/terms", content=content)


@app.route('/privacy')
def privacy():
    content = '''
        <p><strong>1. Information We Collect:</strong> Google profile info via OAuth, Last.fm credentials, YT Music browser headers, and scrobble logs.</p>
        <p><strong>2. How We Use Data:</strong> Exclusively to authenticate you and sync your music history to Last.fm.</p>
        <p><strong>3. Data Security:</strong> Secured with PostgreSQL Row-Level Security, HTTPS, and session hardening.</p>
        <p><strong>4. Deletion:</strong> You can request full data deletion at any time.</p>
    '''
    return render_template('legal.html', title="Privacy Policy", path="/privacy", content=content)


# =============================================================================
# GOOGLE OAUTH ROUTES (For Multi-User Authentication)
# =============================================================================

@app.route('/auth/google')
@rate_limit('auth')
def google_login():
    """Initiate Google OAuth flow"""
    if not GOOGLE_CLIENT_ID:
        return jsonify({'error': 'Google OAuth not configured'}), 500
    
    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    
    # Build authorization URL
    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'offline',
        'prompt': 'consent'
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)


@app.route('/auth/google/callback')
def google_callback():
    """Handle Google OAuth callback - creates/retrieves user in database"""
    error = request.args.get('error')
    if error:
        return redirect(f'/?error={error}')
    
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state token
    if state != session.get('oauth_state'):
        return redirect('/?error=invalid_state')
    
    try:
        # Exchange code for tokens
        token_response = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': GOOGLE_CLIENT_ID,
                'client_secret': GOOGLE_CLIENT_SECRET,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': GOOGLE_REDIRECT_URI
            },
            timeout=10
        )
        
        if token_response.status_code != 200:
            return redirect('/?error=token_exchange_failed')
        
        tokens = token_response.json()
        access_token = tokens.get('access_token')
        
        # Get user info from Google
        user_response = requests.get(
            'https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        
        if user_response.status_code != 200:
            return redirect('/?error=user_info_failed')
        
        user_info = user_response.json()
        
        # Get or create user in database (Google ID is primary identifier)
        google_user = {
            'id': user_info.get('id'),
            'email': user_info.get('email'),
            'name': user_info.get('name'),
            'picture': user_info.get('picture')
        }
        
        db_user = get_or_create_user_by_google(google_user)
        if not db_user:
            print(f"[ERROR] Failed to create/get user for {user_info.get('email')}")
            return redirect('/?error=user_creation_failed')
        
        # Store both Google info and database user_id in session
        # Mark permanent so cookie survives browser close (up to PERMANENT_SESSION_LIFETIME)
        session.permanent = True
        session['google_user'] = google_user
        session['user_id'] = db_user.get('id')  # Database UUID
        session['logged_in'] = True
        
        print(f"[INFO] User logged in: {user_info.get('email')} (DB ID: {db_user.get('id')})")
        return redirect('/')
        
    except Exception as e:
        print(f"[ERROR] Google OAuth error: {e}")
        return redirect(f'/?error=oauth_error')


@app.route('/auth/logout')
def logout():
    """Log out user"""
    session.clear()
    return redirect('/')


@app.route('/api/user')
def get_current_user():
    """Get current logged in user with their database ID"""
    if session.get('logged_in'):
        return jsonify({
            'logged_in': True,
            'user': session.get('google_user'),
            'user_id': session.get('user_id')  # Database UUID for config operations
        })
    return jsonify({'logged_in': False})


@app.route('/api/status', methods=['POST'])
def status():
    config = request.json or {}

    # Fill in missing credentials from DB (handles stale localStorage)
    user_id = session.get('user_id')
    config = _enrich_config_from_db(config, user_id)

    # Check Last.fm
    network, _ = get_lastfm_network(config)
    username = None
    if network:
        try:
            username = str(network.get_authenticated_user())
            lastfm_status = {'connected': True, 'username': username}
        except:
            lastfm_status = {'connected': False}
    else:
        lastfm_status = {'connected': False}
    
    # Check YT Music
    ytmusic, _ = get_ytmusic_client(config)
    if ytmusic:
        try:
            ytmusic.get_history()
            ytmusic_status = {'connected': True}
        except Exception as e:
            print(f"Status check error (History): {e}")
            try:
                ytmusic.search("test", limit=1)
                ytmusic_status = {'connected': True, 'warning': "History unavailable"}
            except Exception as e2:
                print(f"Status check error (Search): {e2}")
                ytmusic_status = {'connected': False}
    else:
        ytmusic_status = {'connected': False}
    
    global last_sync_time, sync_logs
    
    # Filter logs for current user in multi-user mode
    user_logs = sync_logs
    google_user = session.get('google_user', {})
    current_user_email = google_user.get('email')
    if is_multi_user_enabled() and current_user_email:
        # Filter by Google email or Last.fm username
        user_logs = [log for log in sync_logs 
                     if log.get('user') == current_user_email 
                     or log.get('user') == username 
                     or log.get('user') is None][:20]
    
    last_track_title = user_logs[0]['title'] if user_logs else None
    
    return jsonify({
        'lastfm': lastfm_status, 
        'ytmusic': ytmusic_status,
        'last_sync': last_sync_time,
        'now': int(time.time()),
        'last_track': last_track_title,
        'logs': user_logs[:20],
        'mode': 'multi-user' if is_multi_user_enabled() else 'single-user'
    })


@app.route('/api/history', methods=['POST'])
def history():
    config = request.json or {}

    if is_multi_user_enabled() and not session.get('logged_in'):
        return jsonify({'error': 'Not logged in'}), 401

    # Fill missing credentials from DB (handles stale localStorage)
    user_id = session.get('user_id')
    config = _enrich_config_from_db(config, user_id)

    ytmusic, error = get_ytmusic_client(config)
    if not ytmusic:
        return jsonify({'error': error or 'Not configured'})

    try:
        google_user = session.get('google_user', {})
        username = google_user.get('email', 'unknown')

        data_store = UserDataStore(user_id=user_id, lastfm_username=username)
        db_scrobbled, track_meta_map = data_store.get_scrobble_history()

        try:
            yt_history = ytmusic.get_history()
        except Exception as e:
            return jsonify({'error': 'YouTube Music session expired. Please re-paste headers in Accounts.'})

        tracks = []
        for item in yt_history[:30]:
            if not is_music_content(item):
                continue
            title = item.get('title', 'Unknown')
            artists = item.get('artists', [])
            artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
            if not artist or artist == 'Unknown':
                if item.get('author'):
                    artist = item.get('author')
                elif item.get('artist'):
                    artist = item.get('artist')
            
            clean_artist = re.sub(r'\s*-\s*Topic$', '', str(artist), flags=re.IGNORECASE).strip()
            video_id = item.get('videoId')
            track_uids = generate_track_uids(title, clean_artist, video_id)
            is_scrobbled, _ = is_track_scrobbled(track_uids, track_meta_map, data_store)
            tracks.append({
                'title': title,
                'artist': clean_artist,
                'album': item.get('album', {}).get('name', '') if item.get('album') else '',
                'videoId': video_id or 'no-id',
                'scrobbled': is_scrobbled
            })

        return jsonify({'tracks': tracks})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/scrobble-single', methods=['POST'])
@rate_limit('scrobble')
def scrobble_single():
    data = request.json or {}
    artist = data.get('artist')
    title = data.get('title')
    album = data.get('album', '')
    video_id = data.get('videoId')

    if not artist or not title:
        return jsonify({'success': False, 'error': 'Artist and title required'}), 400

    user_id = session.get('user_id')
    google_user = session.get('google_user', {})
    username = google_user.get('email', 'unknown')
    config = _enrich_config_from_db(data, user_id)

    network, lastfm_error = get_lastfm_network(config)
    if not network:
        return jsonify({'success': False, 'error': lastfm_error or 'Last.fm not configured'})

    clean_artist = re.sub(r'\s*-\s*Topic$', '', str(artist), flags=re.IGNORECASE).strip()
    clean_title = strip_title_variants(title)
    scrobble_title = clean_title if clean_title else title
    current_time = int(time.time())

    try:
        with scrobble_lock:
            network.scrobble(
                artist=clean_artist,
                title=scrobble_title,
                timestamp=current_time,
                album=album if album else None
            )
            data_store = UserDataStore(user_id=user_id, lastfm_username=username)
            track_uids = generate_track_uids(title, clean_artist, video_id)
            scrobble_meta = {
                'timestamp': current_time,
                'track_title': scrobble_title,
                'artist': clean_artist
            }
            for uid in track_uids:
                data_store.save_scrobble(uid, scrobble_meta)
                data_store.mark_session_scrobbled(uid)
                global_scrobble_session_cache[uid] = current_time
            
            add_sync_log(clean_artist, scrobble_title, status="Single Scrobble", user=username)
            return jsonify({'success': True, 'artist': clean_artist, 'title': scrobble_title, 'video_id': video_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/reset-history', methods=['POST'])
def reset_history():
    user_id = session.get('user_id')
    google_user = session.get('google_user', {})
    username = google_user.get('email', 'unknown')
    
    try:
        data_store = UserDataStore(user_id=user_id, lastfm_username=username)
        data_store.clear_session()
        global_scrobble_session_cache.clear()
        
        if data_store.is_multi_user and user_id:
            try:
                requests.delete(
                    f"{SUPABASE_URL}/rest/v1/scrobbles",
                    params={'user_id': f'eq.{user_id}'},
                    headers=get_headers(),
                    timeout=10
                )
            except Exception as e:
                print(f"[WARN] Reset scrobbles DB delete failed: {e}")
        else:
            store = get_file_storage()
            try:
                if os.path.exists(store.scrobbled_file):
                    os.remove(store.scrobbled_file)
            except Exception as e:
                print(f"[WARN] Reset scrobbles file delete failed: {e}")

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/scrobble', methods=['POST'])
@rate_limit('scrobble')
def scrobble():
    config = request.json or {}
    
    # Require login in production multi-user mode
    if is_multi_user_enabled() and not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    user_id = session.get('user_id')
    google_user = session.get('google_user', {})
    username = google_user.get('email', 'unknown')
    config = _enrich_config_from_db(config, user_id)

    # Prevent overlapping sync operations
    if not sync_operation_lock.acquire(blocking=False):
        return jsonify({'success': False, 'error': 'Sync already in progress'})
    
    try:
        network, lastfm_error = get_lastfm_network(config)
        if not network:
            return jsonify({'success': False, 'error': lastfm_error or 'Last.fm not configured'})
        
        ytmusic, ytmusic_error = get_ytmusic_client(config)
        if not ytmusic:
            return jsonify({'success': False, 'error': ytmusic_error or 'YT Music not configured'})

        if user_id:
            update_user_last_sync(user_id)

        data_store = UserDataStore(user_id=user_id, lastfm_username=username)
        data_store.clear_session()
        
        scrobbled_tracks, track_meta_map = data_store.get_scrobble_history()
        
        try:
            authenticated_user = network.get_authenticated_user()
            recent = network.get_user(authenticated_user).get_recent_tracks(limit=15)
            lastfm_synced_count = 0
            for r in recent:
                track_uids = generate_track_uids(r.track.title, r.track.artist.name)
                already_scrobbled, _ = is_track_scrobbled(track_uids, track_meta_map, data_store)
                if already_scrobbled:
                    continue
                
                try:
                    lfm_ts = int(r.timestamp) if r.timestamp else int(time.time())
                except (TypeError, ValueError):
                    lfm_ts = int(time.time())

                meta = {
                    'timestamp': lfm_ts,
                    'track_title': r.track.title,
                    'artist': r.track.artist.name
                }
                for uid in track_uids:
                    data_store.save_scrobble(uid, meta)
                    track_meta_map[uid] = meta
                lastfm_synced_count += 1
            if lastfm_synced_count > 0:
                print(f"[INFO] Synced {lastfm_synced_count} tracks from Last.fm history")
        except Exception as e:
            print(f"[WARN] Last.fm sync check failed: {e} — relying on DB-only deduplication")

        try:
            history = ytmusic.get_history()
        except Exception as e:
            err_str = str(e)
            return jsonify({'success': False, 'error': 'YouTube Music session expired. Please re-paste headers in Accounts.'})

        if not history:
            return jsonify({'success': True, 'count': 0, 'message': 'No history found'})
        
        scrobbled_count = 0
        scrobbled_video_ids = []
        current_time = int(time.time())

        for i, item in enumerate(history[:30]):
            if not is_music_content(item):
                continue
            title = item.get('title', 'Unknown')
            artists = item.get('artists', [])
            artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
            if not artist or artist == 'Unknown':
                if item.get('author'):
                    artist = item.get('author')
                elif item.get('artist'):
                    artist = item.get('artist')
            
            clean_artist = re.sub(r'\s*-\s*Topic$', '', str(artist), flags=re.IGNORECASE).strip()
            album = item.get('album', {}).get('name', '') if item.get('album') else ''
            video_id = item.get('videoId')
            
            if not video_id and title == 'Unknown':
                continue
            
            clean_title = strip_title_variants(title)
            scrobble_title = clean_title if clean_title else title
            
            track_uids = generate_track_uids(title, clean_artist, video_id)
            already_scrobbled, matching_uid = is_track_scrobbled(track_uids, track_meta_map, data_store)
            
            if already_scrobbled:
                print(f"[DEBUG] Skipping '{title}' - already_scrobbled (matched: {matching_uid})")
                continue
            
            print(f"[DEBUG] Scrobbling '{scrobble_title}' by '{clean_artist}' - first_play")

            try:
                with scrobble_lock:
                    # Realistic 3-minute spacing per track backwards from current_time
                    timestamp = current_time - (i * 180)
                    network.scrobble(
                        artist=clean_artist,
                        title=scrobble_title,
                        timestamp=timestamp,
                        album=album if album else None
                    )
                    scrobble_meta = {
                        'timestamp': timestamp,
                        'track_title': scrobble_title,
                        'artist': clean_artist
                    }
                    for uid in track_uids:
                        data_store.save_scrobble(uid, scrobble_meta)
                        data_store.mark_session_scrobbled(uid)
                        track_meta_map[uid] = scrobble_meta
                        global_scrobble_session_cache[uid] = current_time
                    if video_id:
                        scrobbled_video_ids.append(video_id)
                    add_sync_log(clean_artist, scrobble_title, user=username)
                    scrobbled_count += 1
            except pylast.WSError as e:
                print(f"[ERROR] Last.fm API error for '{title}': {e}")
                add_sync_log(clean_artist, scrobble_title, status=f"API: {str(e)[:20]}", user=username)
            except Exception as e:
                print(f"[ERROR] Scrobble failed for '{title}': {e}")
                add_sync_log(clean_artist, scrobble_title, status=f"Err: {str(e)[:20]}", user=username)

        status_msg = f"Scrobbled {scrobbled_count}" if scrobbled_count > 0 else "No new tracks"
        add_sync_log("System", status_msg, status="Done", user=username)
        global last_sync_time
        last_sync_time = int(time.time())

        if user_id:
            update_user_last_sync(user_id)

        return jsonify({'success': True, 'count': scrobbled_count, 'scrobbled_video_ids': scrobbled_video_ids})
    except Exception as e:
        import traceback
        traceback.print_exc()
        add_sync_log("System", "Sync failed", status="Error")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        sync_operation_lock.release()

# Background Worker (for local/single-user mode)
class BackgroundScrobbler:
    """
    Background scrobbler for local/single-user mode.
    For multi-user production, use the /api/cron endpoint with Vercel Cron.
    """
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        
    def run(self):
        # Don't run background worker in multi-user mode (use cron instead)
        if is_multi_user_enabled():
            print("[INFO] Multi-user mode detected. Use /api/cron for background sync.")
            return
        
        print("[INFO] Background Scrobbler Started (Single-User Mode)")
        while not self.stop_event.is_set():
            config = ConfigManager.load()
            auto_enabled = config.get('auto_scrobble') == True
            interval = int(config.get('interval', 300))
            
            if auto_enabled:
                now = time.time()
                if now - last_sync_time >= interval:
                    # Skip if manual sync is running
                    if not sync_operation_lock.acquire(blocking=False):
                        print(f"[INFO] Background Sync: Skipped (manual sync in progress)")
                        continue
                    try:
                        print(f"[INFO] Background Sync: Starting... (Interval: {interval}s)")
                        self._perform_sync(config)
                    except Exception as e:
                        print(f"[ERROR] Background Sync failed: {e}")
                    finally:
                        sync_operation_lock.release()
            
            self.stop_event.wait(5)

    def _perform_sync(self, config, user_id=None, username=None):
        """Perform sync for a single user. Used by both local and cron."""
        global last_sync_time
        last_sync_time = int(time.time())

        # DISTRIBUTED SYNC LOCK: claim this user's sync slot immediately.
        # Cron workers on separate instances check last_sync_at before picking a user;
        # updating it now prevents two instances from syncing the same user at once.
        if user_id:
            update_user_last_sync(user_id)
        
        network, net_err = get_lastfm_network(config)
        ytmusic, yt_err = get_ytmusic_client(config)
        
        if not network:
            print(f"[WARN] Background sync: Last.fm not available - {net_err}")
            return 0
        if not ytmusic:
            print(f"[WARN] Background sync: YT Music not available - {yt_err}")
            return 0
        
        # Use UserDataStore for proper per-user isolation
        data_store = UserDataStore(user_id=user_id, lastfm_username=username)
        data_store.clear_session()
        history_set, meta_map = data_store.get_scrobble_history()
        
        # Seed local history from Last.fm recent tracks (up to 200 tracks)
        try:
            authenticated_user = network.get_authenticated_user()
            recent = network.get_user(authenticated_user).get_recent_tracks(limit=200)
            for r in recent:
                track_uids = generate_track_uids(r.track.title, r.track.artist.name)
                already_scrobbled, _ = is_track_scrobbled(track_uids, meta_map, data_store)
                if already_scrobbled:
                    continue
                try:
                    lfm_ts = int(r.timestamp) if r.timestamp else int(time.time())
                except (TypeError, ValueError):
                    lfm_ts = int(time.time())
                meta = {
                    'timestamp': lfm_ts,
                    'track_title': r.track.title,
                    'artist': r.track.artist.name
                }
                for uid in track_uids:
                    data_store.save_scrobble(uid, meta)
                    meta_map[uid] = meta
        except Exception as e:
            print(f"[BG] Last.fm sync check failed: {e} — relying on DB-only deduplication")

        try:
            history = ytmusic.get_history()
        except Exception as e:
            print(f"[ERROR] Background sync: Failed to get history - {e}")
            return 0
        
        if not history:
            return 0
        
        current_time = int(time.time())
        scrobbled_count = 0
        
        # Process the 15 most recent items (was 3, then 10)
        for i, item in enumerate(history[:15]):
            title = item.get('title', 'Unknown')
            artists = item.get('artists', [])
            artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
            album = item.get('album', {}).get('name', '') if item.get('album') else ''
            video_id = item.get('videoId')
            
            if not video_id and title == 'Unknown':
                continue
            
            # Generate ALL possible UIDs for bulletproof deduplication
            track_uids = generate_track_uids(title, artist, video_id)
            
            # Check if ANY UID was already scrobbled
            already_scrobbled, matching_uid = is_track_scrobbled(track_uids, meta_map, data_store)
            
            if already_scrobbled:
                print(f"[BG] Skip '{title}' - already_scrobbled (matched: {matching_uid})")
                continue
            
            print(f"[BG] New: '{title}' - first_play")

            try:
                with scrobble_lock:
                    # Space timestamps 1 minute apart so Last.fm doesn't deduplicate them
                    timestamp = current_time - (i * 60)
                    network.scrobble(
                        artist=artist,
                        title=title,
                        timestamp=timestamp,
                        album=album if album else None
                    )
                    scrobble_meta = {
                        'timestamp': timestamp,
                        'track_title': title,
                        'artist': artist
                    }
                    # Save to DB AND immediately update local meta_map.
                    # Direct map update is critical: save_scrobble() returns set(),{}
                    # on DB failure, which would zero out meta_map and cause a
                    # scrobble loop on every subsequent cron tick.
                    for uid in track_uids:
                        data_store.save_scrobble(uid, scrobble_meta)
                        meta_map[uid] = scrobble_meta  # Always stays accurate
                    add_sync_log(artist, title, status="Auto", user=username)
                    scrobbled_count += 1
            except pylast.WSError as e:
                print(f"[BG] Last.fm API error: {e}")
                add_sync_log(artist, title, status="API Err", user=username)
            except Exception as e:
                print(f"[BG] Scrobble error: {e}")
                add_sync_log(artist, title, status="Error", user=username)
        
        if scrobbled_count > 0:
            print(f"[INFO] Background Sync: {scrobbled_count} tracks scrobbled for {username or 'local'}")
        
        return scrobbled_count

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()


# Initialize background worker (only runs in single-user mode)
bg_scrobbler = BackgroundScrobbler()
bg_scrobbler.start()


# =============================================================================
# CRON ENDPOINT (For External Cron Service / Multi-User Background Sync)
# Optimized for 5000+ users with batch processing
# =============================================================================

@app.route('/api/cron', methods=['GET', 'POST'])
@rate_limit('cron')
def cron_sync():
    """
    Cron endpoint for background sync. Optimized for 5000+ users.
    
    For external cron service (cron-job.org), configure:
    - URL: https://yourapp.com/api/cron
    - Schedule: Every 5 minutes
    - Header: Authorization: Bearer YOUR_CRON_SECRET
    
    Query params:
    - batch_size: Number of users per batch (default 50)
    - offset: Starting offset for pagination (default 0)
    - max_users: Maximum users to process in this run (default 200)
    
    Security: Requires CRON_SECRET env var for authentication.
    """
    # REQUIRED: Verify cron secret for security
    cron_secret = os.environ.get('CRON_SECRET')
    if cron_secret:
        auth_header = request.headers.get('Authorization', '')
        if f'Bearer {cron_secret}' != auth_header:
            return jsonify({'error': 'Unauthorized'}), 401
    
    # Parse pagination params for large-scale processing
    batch_size = min(int(request.args.get('batch_size', 50)), 100)  # Max 100 per batch
    offset = int(request.args.get('offset', 0))
    max_users = min(int(request.args.get('max_users', 200)), 500)  # Max 500 per cron run
    
    start_time = time.time()
    max_runtime = 55  # Vercel function timeout is 60s, leave buffer
    
    results = {
        'users_processed': 0,
        'total_scrobbled': 0,
        'errors': [],
        'offset': offset,
        'batch_size': batch_size
    }
    
    if is_multi_user_enabled():
        # Multi-user mode: Process users in batches
        total_active = get_active_users_count()
        results['total_active_users'] = total_active
        
        print(f"[CRON] Starting sync: {total_active} active users, batch_size={batch_size}, offset={offset}")
        
        processed = 0
        for user in iterate_active_users(batch_size=batch_size):
            # Skip users before offset (allows distributed processing)
            if processed < offset:
                processed += 1
                continue
            
            # Check runtime limit
            if time.time() - start_time > max_runtime:
                results['timeout'] = True
                results['next_offset'] = processed
                print(f"[CRON] Timeout reached after {processed} users")
                break
            
            # Check max users limit
            if results['users_processed'] >= max_users:
                results['max_reached'] = True
                results['next_offset'] = processed
                print(f"[CRON] Max users limit reached: {max_users}")
                break
            
            try:
                user_id = user.get('id')
                username = user.get('lastfm_username') or user.get('google_email', 'unknown')
                
                # Build config from user's stored credentials
                store = UserDataStore(user_id=user_id, lastfm_username=username)
                config = store.get_config()
                
                # Double-check auto_scrobble is enabled
                if not config.get('auto_scrobble', False):
                    processed += 1
                    continue
                
                count = bg_scrobbler._perform_sync(config, user_id=user_id, username=username)
                results['users_processed'] += 1
                results['total_scrobbled'] += count
                
                # Update last sync time
                update_user_last_sync(user_id)
                
            except Exception as e:
                error_msg = f"{user.get('google_email', 'unknown')}: {str(e)[:100]}"
                print(f"[CRON] Error: {error_msg}")
                results['errors'].append(error_msg)
                if len(results['errors']) > 10:
                    results['errors'] = results['errors'][:10] + ['... truncated']
            
            processed += 1
        
        results['runtime_seconds'] = round(time.time() - start_time, 2)
        
    else:
        # Single-user mode: Process local config
        config = ConfigManager.load()
        if config.get('auto_scrobble', False):
            count = bg_scrobbler._perform_sync(config)
            results['users_processed'] = 1
            results['total_scrobbled'] = count
    
    return jsonify({
        'success': True,
        'mode': 'multi-user' if is_multi_user_enabled() else 'single-user',
        **results
    })

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    """
    Get or save user configuration.
    In multi-user mode, uses session user_id to identify the user.
    """
    # Get user_id from session (set during Google login)
    user_id = session.get('user_id')
    
    if not session.get('logged_in'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if request.method == 'POST':
        new_config = request.json or {}
        # Merge with existing config so that a save from one device/browser
        # (which may not have all credentials in localStorage) does NOT erase
        # credentials saved by another device.
        existing = ConfigManager.load(user_id=user_id) or {}
        merged_config = {**existing, **new_config}
        # Deep-merge nested dicts (lastfm, ytmusic):
        # - If key is NOT in new_config → keep existing (client has no opinion)
        # - If key IS in new_config → use new values (even nulls clear the field)
        for key in ('lastfm', 'ytmusic'):
            if key in new_config and key in existing and isinstance(new_config[key], dict) and isinstance(existing[key], dict):
                merged_sub = dict(existing[key])
                merged_sub.update(new_config[key])  # explicit nulls overwrite (allows clearing)
                merged_config[key] = merged_sub
        ConfigManager.save(merged_config, user_id=user_id)
        return jsonify({'success': True})
    
    return jsonify(ConfigManager.load(user_id=user_id))


@app.route('/api/lastfm-callback')
def lastfm_callback():
    token = request.args.get('token')
    if token:
        return render_template('callback.html', token=token)
    return 'No token', 400


@app.route('/api/lastfm-session', methods=['POST'])
def lastfm_session():
    data = request.json or {}
    api_key = data.get('api_key')
    api_secret = data.get('api_secret')
    token = data.get('token')
    
    if not all([api_key, api_secret, token]):
        return jsonify({'error': 'Missing parameters'})
    
    try:
        params = {
            'api_key': api_key,
            'method': 'auth.getSession',
            'token': token
        }
        
        sig_string = ''.join(f'{k}{params[k]}' for k in sorted(params.keys()))
        sig_string += api_secret
        api_sig = hashlib.md5(sig_string.encode('utf-8')).hexdigest()
        
        response = requests.get('https://ws.audioscrobbler.com/2.0/', params={
            **params,
            'api_sig': api_sig,
            'format': 'json'
        })
        
        result = response.json()
        if 'session' in result:
            return jsonify({
                'session_key': result['session']['key'],
                'username': result['session']['name']
            })
        else:
            return jsonify({'error': result.get('message', 'Failed')})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/icon.png')
@app.route('/Icon.png')
@app.route('/favicon.ico')
def serve_logo():
    from flask import send_from_directory
    api_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(api_dir)
    public_dir = os.path.join(root_dir, 'public')
    
    if os.path.exists(os.path.join(root_dir, 'Icon.png')):
        return send_from_directory(root_dir, 'Icon.png')
    elif os.path.exists(os.path.join(public_dir, 'Icon.png')):
        return send_from_directory(public_dir, 'Icon.png')
    elif os.path.exists(os.path.join(public_dir, 'icon.png')):
        return send_from_directory(public_dir, 'icon.png')
    return 'Not found', 404



app = app
