from flask import Flask, request, jsonify, render_template_string, redirect, session, url_for
import os
import json
import re
import hashlib
import time
import urllib.parse
from datetime import datetime
import pylast
from ytmusicapi import YTMusic
import secrets
import requests
import threading

# Import database layer (multi-user support)
try:
    from api.database import (
        UserDataStore, is_multi_user_enabled, get_or_create_user,
        get_all_active_users, get_file_storage
    )
except ImportError:
    from database import (
        UserDataStore, is_multi_user_enabled, get_or_create_user,
        get_all_active_users, get_file_storage
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

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))


# =============================================================================
# CORE SCROBBLE LOGIC (Shared between single and multi-user)
# =============================================================================


def normalize_string(s: str) -> str:
    """Normalize a string for consistent comparison"""
    if not s:
        return ""
    # Lowercase, strip whitespace, remove common variations
    s = s.lower().strip()
    # Remove featuring variations
    for feat in [' feat.', ' feat ', ' ft.', ' ft ', ' featuring ']:
        if feat in s:
            s = s.split(feat)[0].strip()
    # Remove special characters but keep alphanumeric and spaces
    s = ''.join(c for c in s if c.isalnum() or c == ' ')
    # Collapse multiple spaces
    s = ' '.join(s.split())
    return s


def generate_track_uids(title: str, artist: str, video_id: str = None) -> list:
    """
    Generate multiple UIDs for a track for comprehensive deduplication.
    Returns list of UIDs to check - if ANY match, track is considered already scrobbled.
    """
    uids = []
    
    # Primary: Video ID (most unique)
    if video_id:
        uids.append(f"vid:{video_id}")
    
    # Secondary: Title + Artist (exact)
    if title and artist:
        uids.append(f"{title}_{artist}")
    
    # Tertiary: Normalized title + artist (catches slight variations)
    norm_title = normalize_string(title)
    norm_artist = normalize_string(artist)
    if norm_title and norm_artist:
        uids.append(f"norm:{norm_title}_{norm_artist}")
    
    return uids


def is_track_scrobbled(track_uids: list, track_meta_map: dict, data_store=None) -> tuple:
    """
    Check if a track has been scrobbled using ANY of its UIDs.
    Returns: (is_scrobbled: bool, matching_uid: str or None)
    """
    for uid in track_uids:
        # Check persistent storage
        if uid in track_meta_map:
            return True, uid
        # Check session storage
        if data_store and data_store.is_session_scrobbled(uid):
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
        
        return None, None

# YTMusic Scrobbler - Powered by Browser Headers

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YT Music Scrobbler</title>
    <meta name="description" content="Automatically scrobble your YouTube Music listening history to Last.fm. Free and open-source.">
    <meta name="keywords" content="YouTube Music, Last.fm, scrobbler, music tracker, listening history">
    <meta name="author" content="Kuber Bassi">
    <meta name="robots" content="index, follow">
    <meta property="og:title" content="YT Music Scrobbler">
    <meta property="og:description" content="Sync YouTube Music to Last.fm automatically">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://ytscrobbler.kuberbassi.com">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="YT Music Scrobbler">
    <meta name="twitter:description" content="Automatically scrobble your YouTube Music listening history to Last.fm.">
    <link rel="canonical" href="https://ytscrobbler.kuberbassi.com">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect fill='%23000' rx='20' width='100' height='100'/><polygon fill='%23fff' points='35,25 35,75 75,50'/></svg>">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #000;
            --bg-secondary: #0a0a0a;
            --bg-tertiary: #111;
            --bg-elevated: #171717;
            --border: #262626;
            --border-hover: #404040;
            --text-primary: #fafafa;
            --text-secondary: #a1a1a1;
            --text-tertiary: #737373;
            --accent: #fff;
            --success: #22c55e;
            --error: #ef4444;
            --blue: #3b82f6;
        }
        [data-theme="light"] {
            --bg-primary: #fff;
            --bg-secondary: #fafafa;
            --bg-tertiary: #f5f5f5;
            --bg-elevated: #fff;
            --border: #e5e5e5;
            --border-hover: #d4d4d4;
            --text-primary: #171717;
            --text-secondary: #525252;
            --text-tertiary: #a3a3a3;
            --accent: #000;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            -webkit-font-smoothing: antialiased;
        }
        
        /* Toast */
        .toast-container { position: fixed; top: 16px; right: 16px; z-index: 1000; display: flex; flex-direction: column; gap: 8px; }
        .toast {
            background: var(--bg-elevated);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px 16px;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 8px;
            transform: translateX(calc(100% + 20px));
            transition: transform 0.2s ease;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
        }
        .toast.show { transform: translateX(0); }
        .toast-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
        .toast.success .toast-dot { background: var(--success); }
        .toast.error .toast-dot { background: var(--error); }
        .toast.info .toast-dot { background: var(--blue); }

        /* Header */
        .header {
            border-bottom: 1px solid var(--border);
            padding: 0 24px;
            height: 48px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-secondary);
        }
        .logo { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; }
        .logo svg { width: 20px; height: 20px; }
        .header-actions { display: flex; align-items: center; gap: 8px; }
        .theme-btn {
            width: 32px; height: 32px;
            background: transparent;
            border: 1px solid var(--border);
            border-radius: 6px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--text-secondary);
            transition: all 0.15s ease;
        }
        .theme-btn:hover { border-color: var(--border-hover); color: var(--text-primary); }
        .theme-btn svg { width: 16px; height: 16px; }

        /* Main */
        .container { max-width: 600px; margin: 0 auto; padding: 48px 24px; flex: 1; }
        h1 { font-size: 24px; font-weight: 600; margin-bottom: 4px; letter-spacing: -0.3px; }
        .subtitle { color: var(--text-secondary); font-size: 14px; margin-bottom: 32px; }

        /* Tabs */
        .tabs { display: flex; gap: 24px; margin-bottom: 32px; border-bottom: 1px solid var(--border); }
        .tab {
            padding: 12px 0;
            cursor: pointer;
            color: var(--text-tertiary);
            font-size: 14px;
            border-bottom: 1px solid transparent;
            margin-bottom: -1px;
            transition: all 0.15s ease;
        }
        .tab:hover { color: var(--text-secondary); }
        .tab.active { color: var(--text-primary); border-color: var(--text-primary); }
        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeIn 0.2s ease; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

        /* Card */
        .card {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 8px;
            margin-bottom: 16px;
        }
        .card-header {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .card-title { font-size: 12px; font-weight: 500; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.5px; }
        .card-body { padding: 16px; }

        /* Status */
        .status-item { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; }
        .status-item:not(:last-child) { border-bottom: 1px solid var(--border); }
        .status-left { display: flex; align-items: center; gap: 12px; }
        .status-icon {
            width: 32px; height: 32px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 12px;
            color: #fff;
        }
        .status-icon.lastfm { background: #d51007; }
        .status-icon.ytmusic { background: #ff0000; }
        .status-name { font-size: 14px; font-weight: 500; }
        .status-badge {
            font-size: 12px;
            padding: 4px 8px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .status-badge .dot { width: 6px; height: 6px; border-radius: 50%; }
        .status-badge.online { background: rgba(34,197,94,0.15); color: var(--success); }
        .status-badge.online .dot { background: var(--success); }
        .status-badge.offline { background: rgba(239,68,68,0.15); color: var(--error); }
        .status-badge.offline .dot { background: var(--error); }

        /* Buttons */
        .btn {
            height: 36px;
            padding: 0 14px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            border: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            transition: all 0.15s ease;
        }
        .btn-primary { background: var(--accent); color: var(--bg-primary); }
        .btn-primary:hover { opacity: 0.9; }
        .btn-secondary { background: transparent; border: 1px solid var(--border); color: var(--text-primary); }
        .btn-secondary:hover { border-color: var(--border-hover); background: var(--bg-tertiary); }
        .btn-sm { height: 28px; padding: 0 10px; font-size: 12px; }
        .btn-google {
            background: #fff;
            color: #1f1f1f;
            border: 1px solid #dadce0;
            height: 40px;
            padding: 0 16px;
            font-weight: 500;
        }
        .btn-google:hover { background: #f8f9fa; border-color: #c6c6c6; }
        .btn-google svg { width: 18px; height: 18px; }
        .btn-group { display: flex; gap: 8px; }

        /* Form */
        .form-group { margin-bottom: 16px; }
        .form-label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; color: var(--text-primary); }
        .form-input {
            width: 100%;
            height: 36px;
            padding: 0 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-primary);
            color: var(--text-primary);
            font-size: 13px;
            transition: border-color 0.15s ease;
        }
        .form-input:focus { outline: none; border-color: var(--text-tertiary); }
        .form-input::placeholder { color: var(--text-tertiary); }
        .form-hint { font-size: 12px; color: var(--text-tertiary); margin-top: 6px; }
        .form-hint a { color: var(--blue); text-decoration: none; }
        .form-hint a:hover { text-decoration: underline; }

        /* Toggle Row */
        .toggle-row { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; }
        .toggle-row:not(:last-child) { border-bottom: 1px solid var(--border); }
        .toggle-info h3 { font-size: 14px; font-weight: 500; margin-bottom: 2px; }
        .toggle-info p { font-size: 13px; color: var(--text-secondary); }
        
        /* Toggle Switch */
        .toggle {
            width: 40px; height: 22px;
            background: var(--border);
            border-radius: 11px;
            cursor: pointer;
            position: relative;
            transition: background 0.2s ease;
            flex-shrink: 0;
        }
        .toggle::after {
            content: '';
            position: absolute;
            width: 16px; height: 16px;
            background: #fff;
            border-radius: 50%;
            top: 3px; left: 3px;
            transition: transform 0.2s ease;
        }
        .toggle.active { background: var(--success); }
        .toggle.active::after { transform: translateX(18px); }

        /* Select */
        .form-select {
            height: 32px;
            padding: 0 28px 0 10px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-primary);
            color: var(--text-primary);
            font-size: 13px;
            cursor: pointer;
            appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23737373' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 8px center;
        }
        .form-select:focus { outline: none; border-color: var(--text-tertiary); }

        /* Divider */
        .divider { display: flex; align-items: center; gap: 16px; margin: 20px 0; color: var(--text-tertiary); font-size: 12px; }
        .divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: var(--border); }

        /* Track */
        .track { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; }
        .track:not(:last-child) { border-bottom: 1px solid var(--border); }
        .track-info h4 { font-size: 13px; font-weight: 500; margin-bottom: 2px; }
        .track-info p { font-size: 12px; color: var(--text-secondary); }
        .track-badge { font-size: 11px; padding: 2px 6px; border-radius: 4px; }
        .track-badge.done { background: rgba(34,197,94,0.15); color: var(--success); }

        /* Log */
        .log { font-family: 'SF Mono', Monaco, 'Courier New', monospace; font-size: 12px; max-height: 120px; overflow-y: auto; }
        .log-entry { padding: 4px 0; color: var(--text-secondary); line-height: 1.4; border-bottom: 1px solid rgba(255,255,255,0.03); }
        .log-entry .time { color: var(--text-tertiary); margin-right: 4px; }
        .log-entry.error { color: var(--error); }
        .log-entry b { color: var(--text-primary); }

        /* Empty */
        .empty { text-align: center; padding: 32px; color: var(--text-tertiary); font-size: 13px; }

        /* Footer */
        .footer {
            border-top: 1px solid var(--border);
            padding: 16px 24px;
            text-align: center;
            font-size: 12px;
            color: var(--text-tertiary);
        }
        .footer a { color: var(--text-secondary); text-decoration: none; }
        .footer a:hover { color: var(--text-primary); }

        .sync-info { font-size: 11px; margin-top: 12px; color: var(--text-tertiary); text-align: center; }
        .sync-info.active { color: var(--success); }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

        /* Modal */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.6);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            opacity: 0;
            visibility: hidden;
            transition: all 0.2s ease;
        }
        .modal-overlay.show { opacity: 1; visibility: visible; }
        .modal {
            background: var(--bg-secondary);
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 32px;
            max-width: 400px;
            width: 90%;
            text-align: center;
            transform: scale(0.95);
            transition: transform 0.2s ease;
        }
        .modal-overlay.show .modal { transform: scale(1); }
        .modal-title { font-size: 18px; font-weight: 600; margin-bottom: 8px; }
        .modal-text { font-size: 14px; color: var(--text-secondary); margin-bottom: 24px; }
        .device-code {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 32px;
            font-weight: 600;
            letter-spacing: 4px;
            padding: 16px 24px;
            background: var(--bg-tertiary);
            border-radius: 8px;
            margin-bottom: 16px;
            user-select: all;
        }
        .device-link {
            font-size: 14px;
            color: var(--blue);
            margin-bottom: 24px;
            display: block;
        }
        .spinner {
            width: 20px;
            height: 20px;
            border: 2px solid var(--border);
            border-top-color: var(--blue);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 0 auto;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .modal-status { font-size: 13px; color: var(--text-tertiary); margin-top: 16px; }
        
        /* Login Screen */
        .login-screen {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 80vh;
            text-align: center;
            padding: 24px;
        }
        .login-logo { font-size: 48px; margin-bottom: 24px; }
        .login-title { font-size: 28px; font-weight: 600; margin-bottom: 8px; }
        .login-subtitle { color: var(--text-secondary); margin-bottom: 32px; max-width: 400px; }
        .login-btn {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            padding: 14px 28px;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 500;
            cursor: pointer;
            border: 1px solid var(--border);
            background: var(--bg-elevated);
            color: var(--text-primary);
            transition: all 0.2s;
        }
        .login-btn:hover { border-color: var(--border-hover); background: var(--bg-tertiary); }
        .login-btn svg { width: 20px; height: 20px; }
        .user-menu { display: flex; align-items: center; gap: 12px; }
        .user-avatar { width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--border); }
        .user-name { font-size: 13px; color: var(--text-secondary); }
        .logout-btn { font-size: 12px; color: var(--text-tertiary); cursor: pointer; text-decoration: none; }
        .logout-btn:hover { color: var(--text-primary); }
    </style>
</head>
<body data-theme="dark">
    <div class="toast-container" id="toasts"></div>
    
    <header class="header">
        <div class="logo">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816-.029-6.185-.484-8.549-4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z"/></svg>
            YT Music Scrobbler
        </div>
        <div class="header-actions">
            <div id="user-area"></div>
            <button class="theme-btn" onclick="toggleTheme()" title="Toggle theme">
                <svg id="theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
                </svg>
            </button>
        </div>
    </header>

    <!-- Login Screen (shown when multi-user and not logged in) -->
    <div id="login-screen" class="login-screen" style="display: none;">
        <div class="login-logo">🎵</div>
        <h1 class="login-title">YT Music Scrobbler</h1>
        <p class="login-subtitle">Sign in to sync your YouTube Music listening history to Last.fm automatically</p>
        <a href="/auth/google" class="login-btn">
            <svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
            Sign in with Google
        </a>
        <p style="margin-top: 24px; font-size: 13px; color: var(--text-tertiary);">
            Or continue without login for single-user mode
        </p>
        <button onclick="skipLogin()" class="btn btn-secondary" style="margin-top: 12px;">
            Continue as Guest
        </button>
    </div>

    <main id="main-app" class="container">
        <h1>YT Music → Last.fm</h1>
        <p class="subtitle">Scrobble your YouTube Music history automatically</p>

        <div class="tabs">
            <div class="tab active" onclick="showTab('dashboard')">Dashboard</div>
            <div class="tab" onclick="showTab('connect')">Connect</div>
            <div class="tab" onclick="showTab('history')">History</div>
        </div>

        <div id="dashboard" class="tab-content active">
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Connections</span>
                    <button class="btn btn-secondary btn-sm" onclick="checkStatus()">Refresh</button>
                </div>
                <div class="card-body">
                <div class="status-item">
                    <div class="status-left">
                        <div class="status-icon lastfm">L</div>
                        <div class="status-name">Last.fm</div>
                    </div>
                    <div id="lastfm-status" class="status-badge offline">
                        <span class="dot"></span> Offline
                    </div>
                </div>
                <div class="status-item">
                    <div class="status-left">
                        <div class="status-icon ytmusic">Y</div>
                        <div class="status-name">YouTube Music</div>
                    </div>
                    <div id="ytmusic-status" class="status-badge offline">
                        <span class="dot"></span> Offline
                    </div>
                </div>
                <div id="sync-info" class="sync-info">Waiting for sync...</div>
            </div>
        </div>

            <div class="card">
                <div class="card-header"><span class="card-title">Auto Scrobble</span></div>
                <div class="card-body">
                    <div class="toggle-row">
                        <div class="toggle-info">
                            <h3>Enable Auto Scrobble</h3>
                            <p>Automatically check for new tracks</p>
                        </div>
                        <div class="toggle" id="auto-toggle" onclick="toggleAuto()"></div>
                    </div>
                    <div class="toggle-row">
                        <div class="toggle-info">
                            <h3>Check Interval</h3>
                            <p>How often to sync new tracks</p>
                        </div>
                        <select class="form-select" id="interval-select" onchange="updateInterval()">
                            <option value="60">1 min</option>
                            <option value="180">3 min</option>
                            <option value="300" selected>5 min</option>
                            <option value="600">10 min</option>
                            <option value="900">15 min</option>
                        </select>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header"><span class="card-title">Actions</span></div>
                <div class="card-body">
                    <div class="btn-group">
                        <button class="btn btn-primary" onclick="scrobbleNow()">Scrobble Now</button>
                        <button class="btn btn-secondary" onclick="showTab('history'); loadHistory();">View History</button>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header"><span class="card-title">Activity</span></div>
                <div class="card-body">
                    <div class="log" id="log">
                        <div class="log-entry"><span class="time">[--:--]</span> Ready</div>
                    </div>
                </div>
            </div>
        </div>

        <div id="connect" class="tab-content">
            <div class="card">
                <div class="card-header"><span class="card-title">Last.fm</span></div>
                <div class="card-body">
                    <div class="form-group">
                        <label class="form-label">API Key</label>
                        <input type="text" class="form-input" id="lastfm-key" placeholder="32-character API key">
                        <p class="form-hint">Get from <a href="https://www.last.fm/api/account/create" target="_blank">last.fm/api</a></p>
                    </div>
                    <div class="form-group">
                        <label class="form-label">API Secret</label>
                        <input type="password" class="form-input" id="lastfm-secret" placeholder="32-character secret">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Session Key</label>
                        <input type="text" class="form-input" id="lastfm-session" placeholder="Generated after authorization">
                        <p class="form-hint"><a href="#" onclick="authorizeLastfm(); return false;">Click to authorize with Last.fm</a></p>
                    </div>
                    <button class="btn btn-primary" onclick="saveLastfm()">Save</button>
                </div>
            </div>

            <div class="card">
                <div class="card-header"><span class="card-title">YouTube Music</span></div>
                <div class="card-body">
                    <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;">Connect your account by pasting your browser headers. This allows the scrobbler to see what you listen to on **any device** (Phone, PC, TV) via your Watch History.</p>
                    
                    <div id="method-headers">
                        <div class="form-group">
                            <label class="form-label">Browser Headers</label>
                            <textarea class="form-input" id="yt-headers" placeholder="Paste headers here..." rows="4" style="font-family:monospace;font-size:11px;"></textarea>
                            <p class="form-hint">Copy from Network tab. <a href="https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html" target="_blank">Instructions</a></p>
                        </div>
                        <button class="btn btn-primary" onclick="saveYTHeaders()">Connect with Headers</button>
                    </div>
                    
                    <button class="btn btn-secondary" onclick="disconnectYT()" id="disconnect-btn" style="display:none;margin-top:8px;">Disconnect</button>
                    
                    <p class="form-hint" style="margin-top:16px;padding:12px;background:var(--bg-tertiary);border-radius:6px;">
                        Make sure YouTube watch history is enabled. <a href="https://myactivity.google.com/activitycontrols" target="_blank">Check settings</a>
                    </p>
                </div>
            </div>
        </div>

        <div id="history" class="tab-content">
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Recent History</span>
                    <button class="btn btn-secondary btn-sm" onclick="loadHistory()">Refresh</button>
                </div>
                <div class="card-body">
                    <div id="history-list">
                        <div class="empty">Click Refresh to load history</div>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <footer class="footer">
        © <span id="year"></span> <a href="https://kuberbassi.com" target="_blank">Kuber Bassi</a>
        <span style="margin:0 8px;">·</span>
        <a href="/terms">Terms</a>
        <span style="margin:0 8px;">·</span>
        <a href="/privacy">Privacy</a>
    </footer>
    <script src="/_vercel/insights/script.js" defer></script>

    <script>
        document.getElementById('year').textContent = new Date().getFullYear();

        // Toast notification system
        function toast(msg, type = 'success') {
            const container = document.getElementById('toasts');
            const toastEl = document.createElement('div');
            toastEl.className = `toast ${type}`;
            toastEl.innerHTML = `<span class="toast-dot"></span>${msg}`;
            container.appendChild(toastEl);
            requestAnimationFrame(() => toastEl.classList.add('show'));
            setTimeout(() => { 
                toastEl.classList.remove('show'); 
                setTimeout(() => toastEl.remove(), 200); 
            }, 3000);
        }

        // Theme
        function toggleTheme() {
            const isLight = document.body.getAttribute('data-theme') === 'light';
            document.body.setAttribute('data-theme', isLight ? 'dark' : 'light');
            document.getElementById('theme-icon').innerHTML = isLight 
                ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
                : '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
            localStorage.setItem('theme', isLight ? 'dark' : 'light');
        }
        if (localStorage.getItem('theme') === 'light') {
            document.body.setAttribute('data-theme', 'light');
            document.getElementById('theme-icon').innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
        }

        // Tabs
        function showTab(id) {
            document.querySelectorAll('.tab').forEach((t, i) => {
                t.classList.toggle('active', (id === 'dashboard' && i === 0) || (id === 'connect' && i === 1) || (id === 'history' && i === 2));
            });
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(id).classList.add('active');
        }

        // Config
        function getConfig() {
            return {
                lastfm: JSON.parse(localStorage.getItem('lastfm') || '{}'),
                ytmusic: JSON.parse(localStorage.getItem('ytmusic') || '{}')
            };
        }

        // Log
        function log(msg) {
            const l = document.getElementById('log');
            const time = new Date().toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit'});
            l.innerHTML = `<div class="log-entry"><span class="time">[${time}]</span> ${msg}</div>` + l.innerHTML;
        }

        let lastSyncTimestamp = 0;
        async function checkStatus() {
            try {
                const res = await fetch('/api/status', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(getConfig())
                });
                const data = await res.json();
                
                // Update Search Logs from Server
                if (data.logs && data.logs.length > 0) {
                    const l = document.getElementById('log');
                    l.innerHTML = data.logs.map(entry => {
                        const time = new Date(entry.time * 1000).toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit'});
                        const statusClass = entry.status.includes('Error') ? 'error' : '';
                        return `<div class="log-entry ${statusClass}">
                            <span class="time">[${time}]</span> 
                            <b>${entry.artist}</b> - ${entry.title} 
                            &nbsp;<span style="font-size:10px;color:var(--text-tertiary);float:right;">${entry.status}</span>
                        </div>`;
                    }).join('');
                }

                if (data.last_sync > lastSyncTimestamp) {
                    lastSyncTimestamp = data.last_sync;
                    loadHistory();
                }

                // Update Status Badges
                const lfm = document.getElementById('lastfm-status');
                lfm.innerHTML = `<span class="dot"></span>${data.lastfm.connected ? 'Online' : 'Offline'}`;
                lfm.className = `status-badge ${data.lastfm.connected ? 'online' : 'offline'}`;
                
                const ytm = document.getElementById('ytmusic-status');
                ytm.innerHTML = `<span class="dot"></span>${data.ytmusic.connected ? 'Online' : 'Offline'}`;
                ytm.className = `status-badge ${data.ytmusic.connected ? 'online' : 'offline'}`;

                // Update Sync Info text
                const syncInfo = document.getElementById('sync-info');
                if (data.last_sync > 0) {
                    const diff = Math.floor((data.now - data.last_sync) / 60);
                    syncInfo.innerText = diff === 0 ? 'Synced just now' : `Synced ${diff}m ago`;
                    syncInfo.className = 'sync-info active';
                } else {
                    syncInfo.innerText = 'Waiting for first sync...';
                    syncInfo.className = 'sync-info';
                }
            } catch (e) { console.error('Status check failed', e); }
        }

        // Save Last.fm
        async function saveLastfm() {
            const config = {
                api_key: document.getElementById('lastfm-key').value.trim(),
                api_secret: document.getElementById('lastfm-secret').value.trim(),
                session_key: document.getElementById('lastfm-session').value.trim()
            };
            if (!config.api_key || !config.api_secret) return toast('Enter API key and secret', 'error');
            localStorage.setItem('lastfm', JSON.stringify(config));
            
            // Sync with Server
            await saveConfigToServer();
            
            toast('Last.fm saved');
            log('Last.fm saved');
            checkStatus();
        }

        
        // Disconnect YouTube Music
        async function disconnectYT() {
            localStorage.removeItem('yt_headers');
            localStorage.removeItem('ytmusic');
            
            // Sync with Server
            await saveConfigToServer();
            
            toast('Disconnected from YouTube', 'info');
            log('Disconnected');
            checkStatus();
            updateYTState();
        }

        // Save Browser Headers
        async function saveYTHeaders() {
            const headers = document.getElementById('yt-headers').value.trim();
            if (!headers) return toast('Paste headers first', 'error');
            
            localStorage.setItem('yt_headers', headers);
            localStorage.setItem('ytmusic', JSON.stringify({headers: headers}));
            
            // Sync with Server
            await saveConfigToServer();
            
            toast('Headers connected!');
            log('YouTube Music connected');
            
            // Instant State Update
            updateYTState();
            checkStatus();
            setTimeout(loadHistory, 300); // Quick refresh after status
        }

        // Update YouTube UI State
        function updateYTState() {
            const headers = localStorage.getItem('yt_headers');
            const disconnectBtn = document.getElementById('disconnect-btn');
            const headerSection = document.getElementById('method-headers');
            
            if (headers) {
                disconnectBtn.style.display = 'inline-flex';
                headerSection.style.display = 'none';
            } else {
                disconnectBtn.style.display = 'none';
                headerSection.style.display = 'block';
            }
        }

        // Last.fm Auth
        function authorizeLastfm() {
            const key = document.getElementById('lastfm-key').value.trim();
            if (!key) return toast('Enter API key first', 'error');
            const cb = encodeURIComponent(window.location.origin + '/api/lastfm-callback');
            window.open(`https://www.last.fm/api/auth/?api_key=${key}&cb=${cb}`, 'lastfm', 'width=500,height=600');
        }

        window.addEventListener('message', async (e) => {
            if (e.data.type === 'lastfm-token') {
                const key = document.getElementById('lastfm-key').value.trim();
                const secret = document.getElementById('lastfm-secret').value.trim();
                try {
                    const res = await fetch('/api/lastfm-session', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({api_key: key, api_secret: secret, token: e.data.token})
                    });
                    const data = await res.json();
                    if (data.session_key) {
                        document.getElementById('lastfm-session').value = data.session_key;
                        toast('Authorized as ' + data.username);
                        log('Last.fm: ' + data.username);
                    } else toast(data.error || 'Failed', 'error');
                } catch { toast('Auth failed', 'error'); }
            }
        });

        // Scrobble
        async function scrobbleNow() {
            log('Scrobbling...');
            try {
                const res = await fetch('/api/scrobble', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(getConfig())
                });
                const data = await res.json();
                if (data.success) {
                    toast(`Scrobbled ${data.count} tracks`);
                    log(`Scrobbled ${data.count}`);
                    loadHistory(); // Auto-refresh history to show newly scrobbled tracks
                } else {
                    toast(data.error || 'Failed', 'error');
                    log('Error: ' + (data.error || 'Failed'));
                }
            } catch (e) { toast('Error', 'error'); log('Error'); }
        }

        // History
        async function loadHistory() {
            const list = document.getElementById('history-list');
            list.innerHTML = '<div class="empty">Loading...</div>';
            try {
                const res = await fetch('/api/history', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(getConfig())
                });
                const data = await res.json();
                if (data.error) return list.innerHTML = `<div class="empty">${data.error}</div>`;
                if (!data.tracks?.length) return list.innerHTML = '<div class="empty">No history</div>';
                list.innerHTML = data.tracks.map(t => `
                    <div class="track">
                        <div class="track-info">
                            <h4>${t.title}</h4>
                            <p>${t.artist}</p>
                        </div>
                        ${t.scrobbled ? '<span class="track-badge done">Scrobbled</span>' : ''}
                    </div>
                `).join('');
            } catch { list.innerHTML = '<div class="empty">Error</div>'; }
        }

        // Auto scrobble (Now purely UI toggle for server-side worker)
        async function toggleAuto() {
            const toggle = document.getElementById('auto-toggle');
            const isEnabled = !toggle.classList.contains('active');
            
            if (isEnabled) {
                toggle.classList.add('active');
                localStorage.setItem('autoScrobble', 'true');
                log('Auto Sync Server: ON');
                toast('Server Auto Scrobble ON');
            } else {
                toggle.classList.remove('active');
                localStorage.setItem('autoScrobble', 'false');
                log('Auto Sync Server: OFF');
                toast('Server Auto Scrobble OFF', 'info');
            }
            
            await saveConfigToServer();
        }

        async function updateInterval() {
            const sec = parseInt(document.getElementById('interval-select').value);
            localStorage.setItem('interval', sec);
            toast(`Interval: ${sec/60} min`, 'info');
            await saveConfigToServer();
        }

        // Server Config Sync
        async function saveConfigToServer() {
            const lastfm = JSON.parse(localStorage.getItem('lastfm') || '{}');
            const yt_headers = localStorage.getItem('yt_headers');
            const auto_scrobble = localStorage.getItem('autoScrobble') === 'true';
            const interval = parseInt(document.getElementById('interval-select').value);
            
            const config = {
                lastfm: lastfm,
                ytmusic: { headers: yt_headers },
                auto_scrobble: auto_scrobble,
                interval: interval
            };
            
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
            } catch (e) { console.error("Sync to server failed", e); }
        }

        // Load config
        async function loadConfig() {
            // Priority 1: Load from Server
            try {
                const res = await fetch('/api/config');
                const config = await res.json();
                
                if (config.lastfm) {
                    localStorage.setItem('lastfm', JSON.stringify(config.lastfm));
                    document.getElementById('lastfm-key').value = config.lastfm.api_key || '';
                    document.getElementById('lastfm-secret').value = config.lastfm.api_secret || '';
                    document.getElementById('lastfm-session').value = config.lastfm.session_key || '';
                }
                
                if (config.ytmusic?.headers) {
                    localStorage.setItem('yt_headers', config.ytmusic.headers);
                    document.getElementById('yt-headers').value = config.ytmusic.headers;
                }
                
                if (config.interval) {
                    localStorage.setItem('interval', config.interval);
                    document.getElementById('interval-select').value = config.interval;
                }
                
                if (config.auto_scrobble !== undefined) {
                    localStorage.setItem('autoScrobble', config.auto_scrobble ? 'true' : 'false');
                    document.getElementById('auto-toggle').classList.toggle('active', config.auto_scrobble);
                }
            } catch (e) {
                console.error("Failed to load server config, falling back to local", e);
                // Fallback to local if server fails
                const lastfm = JSON.parse(localStorage.getItem('lastfm') || '{}');
                const interval = localStorage.getItem('interval');
                const yt_headers = localStorage.getItem('yt_headers');
                const autoEnabled = localStorage.getItem('autoScrobble') === 'true';
                
                if (lastfm.api_key) document.getElementById('lastfm-key').value = lastfm.api_key;
                if (lastfm.api_secret) document.getElementById('lastfm-secret').value = lastfm.api_secret;
                if (lastfm.session_key) document.getElementById('lastfm-session').value = lastfm.session_key;
                if (interval) document.getElementById('interval-select').value = interval;
                if (yt_headers) document.getElementById('yt-headers').value = yt_headers;
                if (autoEnabled) document.getElementById('auto-toggle').classList.add('active');
            }
            
            updateYTState();
        }

        // Check login state and show appropriate screen
        let currentUser = null;
        async function checkLoginState() {
            try {
                const res = await fetch('/api/user');
                const data = await res.json();
                
                const loginScreen = document.getElementById('login-screen');
                const mainApp = document.getElementById('main-app');
                const userArea = document.getElementById('user-area');
                
                // Check URL for error
                const urlParams = new URLSearchParams(window.location.search);
                const errorMsg = urlParams.get('error');
                if (errorMsg) {
                    toast(errorMsg, 'error');
                    window.history.replaceState({}, '', '/');
                }
                
                // Check if guest mode or logged in
                const isGuest = localStorage.getItem('guestMode') === 'true';
                
                if (data.logged_in) {
                    currentUser = data.user;
                    loginScreen.style.display = 'none';
                    mainApp.style.display = 'block';
                    userArea.innerHTML = `
                        <div class="user-menu">
                            <img class="user-avatar" src="${data.user.picture || ''}" alt="">
                            <span class="user-name">${data.user.name || data.user.email}</span>
                            <a href="/auth/logout" class="logout-btn">Logout</a>
                        </div>
                    `;
                } else if (isGuest) {
                    // Guest mode - show main app with sign in option
                    loginScreen.style.display = 'none';
                    mainApp.style.display = 'block';
                    userArea.innerHTML = `<span class="user-name" style="font-size: 12px;">Guest</span><a href="#" onclick="exitGuestMode()" class="logout-btn" style="font-size: 11px;">Sign In</a>`;
                } else {
                    // Not logged in - show login screen
                    loginScreen.style.display = 'flex';
                    mainApp.style.display = 'none';
                }
            } catch (e) {
                // On error, default to guest mode
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('main-app').style.display = 'block';
            }
        }
        
        function exitGuestMode() {
            localStorage.removeItem('guestMode');
            document.getElementById('login-screen').style.display = 'flex';
            document.getElementById('main-app').style.display = 'none';
        }
        
        function skipLogin() {
            localStorage.setItem('guestMode', 'true');
            document.getElementById('login-screen').style.display = 'none';
            document.getElementById('main-app').style.display = 'block';
            document.getElementById('user-area').innerHTML = `<span class="user-name" style="font-size: 12px;">Guest</span><a href="#" onclick="exitGuestMode()" class="logout-btn" style="font-size: 11px;">Sign In</a>`;
            loadConfig();
            checkStatus();
        }

        // Clean up legacy URL params
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('google_auth')) {
            window.history.replaceState({}, '', '/');
        }

        // Initialize
        checkLoginState().then(() => {
            if (document.getElementById('main-app').style.display !== 'none') {
                loadConfig();
                checkStatus();
                loadHistory();
            }
        });
        
        // Poll status for real-time updates
        setInterval(() => {
            if (document.getElementById('main-app').style.display !== 'none') {
                checkStatus();
            }
        }, 5000);
        
        // Real-time history refresh every 10 seconds when on history tab
        setInterval(() => {
            if (document.getElementById('history').classList.contains('active')) {
                loadHistory();
            }
        }, 10000);
    </script>
</body>
</html>
'''

# Scrobble tracking handled by persistent scrobbled.json


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
    return render_template_string(HTML_TEMPLATE)

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


# Simple page template
SIMPLE_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - YT Scrobbler</title>
    <link rel="canonical" href="https://ytscrobbler.kuberbassi.com{path}">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #000; color: #fafafa; line-height: 1.6; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 48px 24px; }}
        h1 {{ font-size: 24px; margin-bottom: 24px; }}
        p {{ color: #a1a1aa; margin-bottom: 16px; }}
        a {{ color: #fafafa; }}
        .back {{ margin-top: 32px; display: inline-block; color: #71717a; text-decoration: none; }}
        .back:hover {{ color: #fafafa; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        {content}
        <a class="back" href="/">← Back</a>
    </div>
    <script src="/_vercel/insights/script.js" defer></script>
</body>
</html>
'''


# =============================================================================
# GOOGLE OAUTH ROUTES (For Multi-User Authentication)
# =============================================================================

@app.route('/auth/google')
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
    """Handle Google OAuth callback"""
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
        
        # Get user info
        user_response = requests.get(
            'https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        
        if user_response.status_code != 200:
            return redirect('/?error=user_info_failed')
        
        user_info = user_response.json()
        
        # Store user info in session
        session['google_user'] = {
            'id': user_info.get('id'),
            'email': user_info.get('email'),
            'name': user_info.get('name'),
            'picture': user_info.get('picture')
        }
        session['logged_in'] = True
        
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
    """Get current logged in user"""
    if session.get('logged_in'):
        return jsonify({
            'logged_in': True,
            'user': session.get('google_user')
        })
    return jsonify({'logged_in': False})


@app.route('/api/status', methods=['POST'])
def status():
    config = request.json or {}
    
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
    if is_multi_user_enabled() and username:
        user_logs = [log for log in sync_logs if log.get('user') == username or log.get('user') is None][:20]
    
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
    
    ytmusic, error = get_ytmusic_client(config)
    if not ytmusic:
        return jsonify({'error': error or 'Not configured'})
    
    try:
        # Get data store for checking scrobbled status
        lastfm_config = config.get('lastfm', {})
        user_id, username = ConfigManager.get_user_from_session(
            lastfm_config.get('session_key'),
            lastfm_config.get('api_key'),
            lastfm_config.get('api_secret')
        )
        data_store = UserDataStore(user_id=user_id, lastfm_username=username)
        scrobbled_tracks, _ = data_store.get_scrobble_history()
        
        history = ytmusic.get_history()
        
        tracks = []
        for item in history[:20]:
            title = item.get('title', 'Unknown')
            artist = item.get('artists', [{}])[0].get('name', 'Unknown')
            video_id = item.get('videoId')
            # Check both videoId and title_artist formats
            title_artist_uid = f"{title}_{artist}"
            is_scrobbled = (video_id and video_id in scrobbled_tracks) or (title_artist_uid in scrobbled_tracks)
            
            tracks.append({
                'title': title,
                'artist': artist,
                'album': item.get('album', {}).get('name', '') if item.get('album') else '',
                'videoId': video_id or 'no-id',
                'scrobbled': is_scrobbled
            })
        
        return jsonify({'tracks': tracks})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/scrobble', methods=['POST'])
def scrobble():
    config = request.json or {}
    
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
        
        # Determine user context for multi-user support
        lastfm_config = config.get('lastfm', {})
        user_id, username = ConfigManager.get_user_from_session(
            lastfm_config.get('session_key'),
            lastfm_config.get('api_key'),
            lastfm_config.get('api_secret')
        )
        
        # Create data store (uses DB if multi-user, else file)
        data_store = UserDataStore(user_id=user_id, lastfm_username=username)
        data_store.clear_session()
        
        # Load scrobble history
        scrobbled_tracks, track_meta_map = data_store.get_scrobble_history()
        
        # Sync with Last.fm to avoid duplicates (marks recent Last.fm tracks as already scrobbled)
        # This runs every time to catch any tracks scrobbled from other sources
        try:
            authenticated_user = network.get_authenticated_user()
            recent = network.get_user(authenticated_user).get_recent_tracks(limit=50)
            lastfm_synced_count = 0
            for r in recent:
                # Generate ALL possible UIDs for this track
                track_uids = generate_track_uids(r.track.title, r.track.artist.name)
                
                # Check if ANY UID already exists
                already_scrobbled, _ = is_track_scrobbled(track_uids, track_meta_map, data_store)
                if already_scrobbled:
                    continue
                
                meta = {
                    'timestamp': int(time.time()) - 3600,  # Mark as 1 hour ago
                    'track_title': r.track.title,
                    'artist': r.track.artist.name
                }
                # Save ALL UIDs to storage for comprehensive deduplication
                for uid in track_uids:
                    scrobbled_tracks, track_meta_map = data_store.save_scrobble(uid, meta)
                lastfm_synced_count += 1
            if lastfm_synced_count > 0:
                print(f"[INFO] Synced {lastfm_synced_count} tracks from Last.fm history")
        except Exception as e:
            print(f"[WARN] Last.fm sync check failed: {e}")

        history = ytmusic.get_history()
        if not history:
            return jsonify({'success': True, 'count': 0, 'message': 'No history found'})
        
        scrobbled_count = 0
        current_time = int(time.time())
        
        # Process history - limit to 20 for manual sync
        for i, item in enumerate(history[:20]):
            title = item.get('title', 'Unknown')
            artists = item.get('artists', [])
            artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
            album = item.get('album', {}).get('name', '') if item.get('album') else ''
            video_id = item.get('videoId')
            
            if not video_id and title == 'Unknown':
                continue
            
            # Generate ALL possible UIDs for this track
            track_uids = generate_track_uids(title, artist, video_id)
            
            # Check if ANY UID was already scrobbled - bulletproof deduplication
            already_scrobbled, matching_uid = is_track_scrobbled(track_uids, track_meta_map, data_store)
            
            if already_scrobbled:
                print(f"[DEBUG] Skipping '{title}' - already_scrobbled (matched: {matching_uid})")
                continue
            
            print(f"[DEBUG] Scrobbling '{title}' - first_play")

            try:
                with scrobble_lock:
                    timestamp = current_time - (i * 3)
                    network.scrobble(
                        artist=artist,
                        title=title,
                        timestamp=timestamp,
                        album=album if album else None
                    )
                    # Save ALL UIDs to prevent any future duplicates
                    scrobble_meta = {
                        'timestamp': timestamp,
                        'track_title': title,
                        'artist': artist
                    }
                    for uid in track_uids:
                        scrobbled_tracks, track_meta_map = data_store.save_scrobble(uid, scrobble_meta)
                    add_sync_log(artist, title, user=username)
                    scrobbled_count += 1
            except pylast.WSError as e:
                print(f"[ERROR] Last.fm API error for '{title}': {e}")
                add_sync_log(artist, title, status=f"API: {str(e)[:12]}", user=username)
            except Exception as e:
                print(f"[ERROR] Scrobble failed for '{title}': {e}")
                add_sync_log(artist, title, status=f"Err: {str(e)[:12]}", user=username)
        
        status_msg = f"Scrobbled {scrobbled_count}" if scrobbled_count > 0 else "No new tracks"
        add_sync_log("System", status_msg, status="Done", user=username)
        global last_sync_time
        last_sync_time = int(time.time())
        return jsonify({'success': True, 'count': scrobbled_count})
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
        
        try:
            history = ytmusic.get_history()
        except Exception as e:
            print(f"[ERROR] Background sync: Failed to get history - {e}")
            return 0
        
        if not history:
            return 0
        
        current_time = int(time.time())
        scrobbled_count = 0
        
        # Background sync: ONLY check first 3 items
        for i, item in enumerate(history[:3]):
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
                    network.scrobble(
                        artist=artist,
                        title=title,
                        timestamp=current_time,
                        album=album if album else None
                    )
                    # Save ALL UIDs to prevent duplicates
                    scrobble_meta = {
                        'timestamp': current_time,
                        'track_title': title,
                        'artist': artist
                    }
                    for uid in track_uids:
                        history_set, meta_map = data_store.save_scrobble(uid, scrobble_meta)
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
# CRON ENDPOINT (For Vercel Cron / Multi-User Background Sync)
# =============================================================================

@app.route('/api/cron', methods=['GET', 'POST'])
def cron_sync():
    """
    Cron endpoint for background sync. Supports both single and multi-user modes.
    
    For Vercel, configure in vercel.json:
    {
      "crons": [{
        "path": "/api/cron",
        "schedule": "*/5 * * * *"  // Every 5 minutes
      }]
    }
    
    Security: Verify Vercel cron secret in production.
    """
    # Optional: Verify cron secret for security
    cron_secret = os.environ.get('CRON_SECRET')
    if cron_secret:
        auth_header = request.headers.get('Authorization', '')
        if f'Bearer {cron_secret}' != auth_header:
            return jsonify({'error': 'Unauthorized'}), 401
    
    results = {'users_processed': 0, 'total_scrobbled': 0, 'errors': []}
    
    if is_multi_user_enabled():
        # Multi-user mode: Process all active users
        active_users = get_all_active_users()
        print(f"[CRON] Processing {len(active_users)} active users")
        
        for user in active_users:
            try:
                user_id = user.get('id')
                username = user.get('lastfm_username')
                
                # Build config from user's stored credentials
                store = UserDataStore(user_id=user_id, lastfm_username=username)
                config = store.get_config()
                
                # Check if auto_scrobble is enabled
                if not config.get('auto_scrobble', False):
                    continue
                
                count = bg_scrobbler._perform_sync(config, user_id=user_id, username=username)
                results['users_processed'] += 1
                results['total_scrobbled'] += count
                
            except Exception as e:
                print(f"[CRON] Error processing user {user.get('lastfm_username')}: {e}")
                results['errors'].append(str(e))
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
    # Simple Security: If running locally, allow. If on Vercel, allow via session/secret.
    # Since this is primarily a local tool, we'll keep it simple.
    if request.method == 'POST':
        new_config = request.json
        ConfigManager.save(new_config)
        return jsonify({'success': True})
    return jsonify(ConfigManager.load())


@app.route('/api/lastfm-callback')
def lastfm_callback():
    token = request.args.get('token')
    if token:
        return f'''
        <!DOCTYPE html>
        <html>
        <head><title>Success</title></head>
        <body style="font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#000;color:#fff;">
            <div style="text-align:center;">
                <p style="font-size:24px;margin-bottom:8px;">✓</p>
                <p>Authorized</p>
            </div>
            <script>
                if (window.opener) window.opener.postMessage({{type:'lastfm-token',token:'{token}'}}, '*');
                setTimeout(() => window.close(), 1000);
            </script>
        </body>
        </html>
        '''
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


app = app
