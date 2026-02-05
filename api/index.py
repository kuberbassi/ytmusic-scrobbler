from flask import Flask, request, jsonify, render_template_string, redirect, session
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

# Global Sync State
scrobble_lock = threading.Lock()
file_lock = threading.Lock()  # For safe file access
last_sync_time = 0
sync_logs = []  # List of [timestamp, artist, title, status]
_session_scrobbled = set()  # Track scrobbles within current session to prevent duplicates

def add_sync_log(artist, title, status="Synced"):
    global sync_logs
    entry = {
        'time': int(time.time()),
        'artist': artist,
        'title': title,
        'status': status
    }
    sync_logs.insert(0, entry)
    sync_logs = sync_logs[:20]  # Keep last 20

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Persistent Scrobble Tracking
SCROBBLED_FILE = os.path.join(os.path.dirname(__file__), "scrobbled.json")

def load_scrobbles():
    """Thread-safe load of scrobble history"""
    with file_lock:
        if os.path.exists(SCROBBLED_FILE):
            try:
                with open(SCROBBLED_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data), {}
                    return set(data.get('history', [])), data.get('track_meta', {})
            except (json.JSONDecodeError, IOError) as e:
                print(f"[WARN] Failed to load scrobbles: {e}")
                return set(), {}
        return set(), {}


def should_scrobble(track_uid, track_meta_map, current_time, duration, position=0):
    """
    Determine if a track should be scrobbled. Handles both first plays and repeats.
    
    Key insight: YT Music doesn't add new history entries for repeats.
    A track staying at position 0 after its duration = likely a repeat.
    
    Args:
        track_uid: Unique identifier for the track
        track_meta_map: Metadata dict with timestamps
        current_time: Current unix timestamp
        duration: Track duration in seconds
        position: Position in history (0 = most recent)
    
    Returns: (should_scrobble: bool, reason: str)
    """
    global _session_scrobbled
    
    # Guard 1: Already scrobbled in this sync session (prevents multi-scrobble bug)
    if track_uid in _session_scrobbled:
        return False, "already_in_session"
    
    # Check if track exists in our history
    meta = track_meta_map.get(track_uid)
    
    # Case 1: Never scrobbled before - always allow
    if meta is None:
        return True, "first_play"
    
    last_scrobble_time = meta.get('timestamp', 0)
    
    # Case 2: No timestamp recorded - allow (legacy data)
    if last_scrobble_time == 0:
        return True, "no_timestamp"
    
    elapsed = current_time - last_scrobble_time
    
    # Guard 2: Minimum gap of 45 seconds (anti-spam)
    min_gap = 45
    if elapsed < min_gap:
        return False, f"too_recent ({elapsed}s < {min_gap}s)"
    
    # Guard 3: Only top 2 positions can trigger repeat scrobbles
    # Deeper history items should not be re-scrobbled
    if position > 1:
        return False, f"position_too_deep (pos={position})"
    
    # Case 3: Repeat detection - enough time for a full replay?
    # Require full duration + small buffer for sync delays
    required_time = max(duration, 60)  # At least 60 seconds
    
    if elapsed >= required_time:
        return True, f"repeat (elapsed={elapsed}s >= required={required_time}s)"
    
    return False, f"not_enough_time ({elapsed}s < {required_time}s)"


def save_scrobble(track_uid, meta=None):
    """
    Thread-safe save of scrobble. Returns updated (history_set, meta_map).
    Also updates session tracking.
    """
    global _session_scrobbled
    
    with file_lock:
        # Load current state
        if os.path.exists(SCROBBLED_FILE):
            try:
                with open(SCROBBLED_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        history = set(data)
                        track_meta = {}
                    else:
                        history = set(data.get('history', []))
                        track_meta = data.get('track_meta', {})
            except:
                history = set()
                track_meta = {}
        else:
            history = set()
            track_meta = {}
        
        # Update
        history.add(track_uid)
        _session_scrobbled.add(track_uid)  # Mark as scrobbled in this session
        
        if meta:
            existing = track_meta.get(track_uid, {})
            existing.update(meta)
            existing['last_check'] = meta.get('timestamp', int(time.time()))
            existing['scrobble_count'] = existing.get('scrobble_count', 0) + 1
            track_meta[track_uid] = existing
        
        # Save
        try:
            with open(SCROBBLED_FILE, "w") as f:
                json.dump({'history': list(history), 'track_meta': track_meta}, f)
        except IOError as e:
            print(f"[ERROR] Failed to save scrobble: {e}")
        
        return history, track_meta


def clear_session_state():
    """Clear session scrobble tracking. Call at start of each sync."""
    global _session_scrobbled
    _session_scrobbled = set()


# Initial Load
scrobbled_tracks, track_meta_map = load_scrobbles()

def get_track_duration(yt_track):
    """Safely extract duration in seconds from YTMusic track object"""
    try:
        # Check integer field first
        if 'duration_seconds' in yt_track:
            return int(yt_track['duration_seconds'])
            
        duration_str = yt_track.get('duration')
        if not duration_str: return 180 # Default 3 mins
        if ':' in duration_str:
            parts = list(map(int, duration_str.split(':')))
            if len(parts) == 2: return parts[0] * 60 + parts[1]
            if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return int(duration_str)
    except:
        return 180

# Configuration Persistence
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

class ConfigManager:
    @staticmethod
    def load():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    @staticmethod
    def save(config):
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

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
            <button class="theme-btn" onclick="toggleTheme()" title="Toggle theme">
                <svg id="theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
                </svg>
            </button>
        </div>
    </header>

    <main class="container">
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

        // Clean up legacy URL params
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('google_auth')) {
            window.history.replaceState({}, '', '/');
        }

        loadConfig();
        checkStatus();
        setInterval(checkStatus, 5000); // Poll status every 5s
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


# Removed Google OAuth Routes


@app.route('/api/status', methods=['POST'])
def status():
    config = request.json or {}
    
    # Check Last.fm
    network, _ = get_lastfm_network(config)
    if network:
        try:
            network.get_authenticated_user()
            lastfm_status = {'connected': True}
        except:
            lastfm_status = {'connected': False}
    else:
        lastfm_status = {'connected': False}
    
    # Check YT Music
    ytmusic, _ = get_ytmusic_client(config)
    if ytmusic:
        try:
            # 1. Try history first
            ytmusic.get_history()
            ytmusic_status = {'connected': True}
        except Exception as e:
            import traceback
            print(f"Status check error (History): {e}")
            traceback.print_exc()
            try:
                # 2. Key Fallback: Try search if history fails
                # This helps verify if connection works even if history is empty/buggy
                ytmusic.search("test", limit=1)
                ytmusic_status = {'connected': True, 'warning': "History unavailable"}
            except Exception as e2:
                import traceback
                print(f"Status check error (Search): {e2}")
                traceback.print_exc()
                ytmusic_status = {'connected': False}
    else:
        ytmusic_status = {'connected': False}
    
    global last_sync_time, sync_logs
    history, meta_map = load_scrobbles()
    # Get last track from logs if possible
    last_track_title = sync_logs[0]['title'] if sync_logs else None
    
    return jsonify({
        'lastfm': lastfm_status, 
        'ytmusic': ytmusic_status,
        'last_sync': last_sync_time,
        'now': int(time.time()),
        'last_track': last_track_title,
        'logs': sync_logs
    })


@app.route('/api/history', methods=['POST'])
def history():
    config = request.json or {}
    
    ytmusic, error = get_ytmusic_client(config)
    if not ytmusic:
        return jsonify({'error': error or 'Not configured'})
    
    try:
        history = ytmusic.get_history()
        
        tracks = []
        for item in history[:20]:
            title = item.get('title', 'Unknown')
            artist = item.get('artists', [{}])[0].get('name', 'Unknown')
            # Use videoId if available, else fallback to title_artist hash
            track_uid = item.get('videoId') or f"{title}_{artist}"
            
            tracks.append({
                'title': title,
                'artist': artist,
                'album': item.get('album', {}).get('name', '') if item.get('album') else '',
                'videoId': item.get('videoId') or 'no-id',
                'scrobbled': track_uid in scrobbled_tracks
            })
        
        return jsonify({'tracks': tracks})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/scrobble', methods=['POST'])
def scrobble():
    config = request.json or {}
    
    network, lastfm_error = get_lastfm_network(config)
    if not network:
        return jsonify({'success': False, 'error': lastfm_error or 'Last.fm not configured'})
    
    ytmusic, ytmusic_error = get_ytmusic_client(config)
    if not ytmusic:
        return jsonify({'success': False, 'error': ytmusic_error or 'YT Music not configured'})
    
    try:
        # Reset session state and load fresh from disk
        clear_session_state()
        global scrobbled_tracks, track_meta_map
        scrobbled_tracks, track_meta_map = load_scrobbles()
        
        # Optional: Sync with Last.fm to avoid duplicates
        if not getattr(app, '_lastfm_synced', False):
            try:
                authenticated_user = network.get_authenticated_user()
                recent = network.get_user(authenticated_user).get_recent_tracks(limit=50)
                for r in recent:
                    r_uid = f"{r.track.title}_{r.track.artist.name}"
                    scrobbled_tracks.add(r_uid)
                app._lastfm_synced = True
            except Exception as e:
                print(f"[WARN] Last.fm sync check failed: {e}")

        history = ytmusic.get_history()
        if not history:
            return jsonify({'success': True, 'count': 0, 'message': 'No history found'})
        
        scrobbled_count = 0
        current_time = int(time.time())
        
        # Process history - limit to 20 for manual sync to be safe
        for i, item in enumerate(history[:20]):
            title = item.get('title', 'Unknown')
            artists = item.get('artists', [])
            artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
            album = item.get('album', {}).get('name', '') if item.get('album') else ''
            video_id = item.get('videoId')
            
            # Skip items without proper identifiers
            if not video_id and title == 'Unknown':
                continue
            
            track_uid = video_id or f"{title}_{artist}"
            duration = get_track_duration(item)
            
            # Check if we should scrobble this track
            can_scrobble, reason = should_scrobble(
                track_uid, track_meta_map, current_time, duration, position=i
            )
            
            if not can_scrobble:
                print(f"[DEBUG] Skipping '{title}' - {reason}")
                continue
            
            print(f"[DEBUG] Scrobbling '{title}' - {reason}")

            try:
                with scrobble_lock:
                    timestamp = current_time - (i * 3)  # Slight offset per track
                    network.scrobble(
                        artist=artist,
                        title=title,
                        timestamp=timestamp,
                        album=album if album else None
                    )
                    # Update state immediately after successful scrobble
                    scrobbled_tracks, track_meta_map = save_scrobble(track_uid, {
                        'timestamp': timestamp,
                        'track_title': title,
                        'artist': artist
                    })
                    add_sync_log(artist, title)
                    scrobbled_count += 1
            except pylast.WSError as e:
                print(f"[ERROR] Last.fm API error for '{title}': {e}")
                add_sync_log(artist, title, status=f"API: {str(e)[:12]}")
            except Exception as e:
                print(f"[ERROR] Scrobble failed for '{title}': {e}")
                add_sync_log(artist, title, status=f"Err: {str(e)[:12]}")
        
        status_msg = f"Scrobbled {scrobbled_count}" if scrobbled_count > 0 else "No new tracks"
        add_sync_log("System", status_msg, status="Done")
        global last_sync_time
        last_sync_time = int(time.time())
        return jsonify({'success': True, 'count': scrobbled_count})
    except Exception as e:
        import traceback
        traceback.print_exc()
        add_sync_log("System", "Sync failed", status="Error")
        return jsonify({'success': False, 'error': str(e)})

# Background Worker
class BackgroundScrobbler:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        
    def run(self):
        print("[INFO] Background Scrobbler Started")
        while not self.stop_event.is_set():
            config = ConfigManager.load()
            auto_enabled = config.get('auto_scrobble') == True
            interval = int(config.get('interval', 300))
            
            if auto_enabled:
                now = time.time()
                # Responsive check: Has the interval passed? 
                if now - last_sync_time >= interval:
                    print(f"[INFO] Background Sync: Starting... (Interval: {interval}s)")
                    try:
                        self._perform_sync(config)
                    except Exception as e:
                        print(f"[ERROR] Background Sync failed: {e}")
            
            # Wake up every 5s to check for config changes/interval
            self.stop_event.wait(5)

    def _perform_sync(self, config):
        global last_sync_time
        last_sync_time = int(time.time())  # Update immediately to prevent spam
        
        network, net_err = get_lastfm_network(config)
        ytmusic, yt_err = get_ytmusic_client(config)
        
        if not network:
            print(f"[WARN] Background sync: Last.fm not available - {net_err}")
            return
        if not ytmusic:
            print(f"[WARN] Background sync: YT Music not available - {yt_err}")
            return
        
        # Reset session state for this sync
        clear_session_state()
        history_set, meta_map = load_scrobbles()
        
        try:
            history = ytmusic.get_history()
        except Exception as e:
            print(f"[ERROR] Background sync: Failed to get history - {e}")
            return
        
        if not history:
            return
        
        current_time = int(time.time())
        scrobbled_count = 0
        
        # Background sync: ONLY check first 3 items (most strict)
        for i, item in enumerate(history[:3]):
            title = item.get('title', 'Unknown')
            artists = item.get('artists', [])
            artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
            album = item.get('album', {}).get('name', '') if item.get('album') else ''
            video_id = item.get('videoId')
            
            if not video_id and title == 'Unknown':
                continue
            
            track_uid = video_id or f"{title}_{artist}"
            duration = get_track_duration(item)
            
            # Use unified should_scrobble logic
            can_scrobble, reason = should_scrobble(
                track_uid, meta_map, current_time, duration, position=i
            )
            
            if not can_scrobble:
                print(f"[BG] Skip '{title}' - {reason}")
                continue
            
            is_repeat = track_uid in history_set
            print(f"[BG] {'Repeat' if is_repeat else 'New'}: '{title}' - {reason}")

            try:
                with scrobble_lock:
                    network.scrobble(
                        artist=artist,
                        title=title,
                        timestamp=current_time,
                        album=album if album else None
                    )
                    history_set, meta_map = save_scrobble(track_uid, {
                        'timestamp': current_time,
                        'track_title': title,
                        'artist': artist
                    })
                    add_sync_log(artist, title, status="Loop" if is_repeat else "Auto")
                    scrobbled_count += 1
            except pylast.WSError as e:
                print(f"[BG] Last.fm API error: {e}")
                add_sync_log(artist, title, status="API Err")
            except Exception as e:
                print(f"[BG] Scrobble error: {e}")
                add_sync_log(artist, title, status="Error")
        
        if scrobbled_count > 0:
            print(f"[INFO] Background Sync: {scrobbled_count} tracks scrobbled")

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

# Initialize background worker
bg_scrobbler = BackgroundScrobbler()
bg_scrobbler.start()

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
