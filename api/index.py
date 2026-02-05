from flask import Flask, request, jsonify, render_template_string, redirect, session
import os
import json
import hashlib
import time
import urllib.parse
from datetime import datetime
import pylast
from ytmusicapi import YTMusic, OAuthCredentials
import secrets
import requests

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Google OAuth Config - Set these in environment variables
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:3000/api/google-callback')

# YouTube Music OAuth scopes - full access for history
GOOGLE_SCOPES = [
    'https://www.googleapis.com/auth/youtube',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtubepartner'
]

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
        .log-entry { padding: 4px 0; color: var(--text-secondary); }
        .log-entry .time { color: var(--text-tertiary); }

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

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
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
                            <span class="status-name">Last.fm</span>
                        </div>
                        <span class="status-badge offline" id="lastfm-badge"><span class="dot"></span>Not Connected</span>
                    </div>
                    <div class="status-item">
                        <div class="status-left">
                            <div class="status-icon ytmusic">Y</div>
                            <span class="status-name">YouTube Music</span>
                        </div>
                        <span class="status-badge offline" id="ytmusic-badge"><span class="dot"></span>Not Connected</span>
                    </div>
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
                    <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;">Connect your Google account to access your YouTube Music listening history.</p>
                    
                    <button class="btn btn-google" onclick="signInWithGoogle()" id="google-btn">
                        <svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
                        <span id="google-btn-text">Sign in with Google</span>
                    </button>
                    
                    <button class="btn btn-secondary" onclick="disconnectGoogle()" id="disconnect-btn" style="display:none;margin-top:8px;">Disconnect</button>
                    
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
    </footer>

    <script>
        document.getElementById('year').textContent = new Date().getFullYear();

        // Toast
        function toast(msg, type = 'success') {
            const c = document.getElementById('toasts');
            const t = document.createElement('div');
            t.className = `toast ${type}`;
            t.innerHTML = `<span class="toast-dot"></span>${msg}`;
            c.appendChild(t);
            requestAnimationFrame(() => t.classList.add('show'));
            setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 200); }, 3000);
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
                ytmusic: JSON.parse(localStorage.getItem('ytmusic') || '{}'),
                google_token: localStorage.getItem('google_token')
            };
        }

        // Log
        function log(msg) {
            const l = document.getElementById('log');
            const time = new Date().toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit'});
            l.innerHTML = `<div class="log-entry"><span class="time">[${time}]</span> ${msg}</div>` + l.innerHTML;
        }

        // Status
        async function checkStatus() {
            try {
                const res = await fetch('/api/status', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(getConfig())
                });
                const data = await res.json();
                
                const lfm = document.getElementById('lastfm-badge');
                lfm.innerHTML = `<span class="dot"></span>${data.lastfm.connected ? 'Connected' : 'Not Connected'}`;
                lfm.className = `status-badge ${data.lastfm.connected ? 'online' : 'offline'}`;
                
                const ytm = document.getElementById('ytmusic-badge');
                ytm.innerHTML = `<span class="dot"></span>${data.ytmusic.connected ? 'Connected' : 'Not Connected'}`;
                ytm.className = `status-badge ${data.ytmusic.connected ? 'online' : 'offline'}`;
            } catch (e) { console.error(e); }
        }

        // Save Last.fm
        function saveLastfm() {
            const config = {
                api_key: document.getElementById('lastfm-key').value.trim(),
                api_secret: document.getElementById('lastfm-secret').value.trim(),
                session_key: document.getElementById('lastfm-session').value.trim()
            };
            if (!config.api_key || !config.api_secret) return toast('Enter API key and secret', 'error');
            localStorage.setItem('lastfm', JSON.stringify(config));
            toast('Last.fm saved');
            log('Last.fm saved');
            checkStatus();
        }

        // Disconnect Google
        function disconnectGoogle() {
            localStorage.removeItem('google_token');
            localStorage.removeItem('ytmusic');
            document.getElementById('google-btn-text').textContent = 'Sign in with Google';
            document.getElementById('disconnect-btn').style.display = 'none';
            toast('Disconnected', 'info');
            log('Google disconnected');
            checkStatus();
        }
        
        // Update Google button state
        function updateGoogleButton() {
            const token = localStorage.getItem('google_token');
            if (token) {
                document.getElementById('google-btn-text').textContent = 'Connected';
                document.getElementById('disconnect-btn').style.display = 'inline-flex';
            }
        }

        // Google Sign In
        function signInWithGoogle() {
            window.location.href = '/api/google-auth';
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

        // Auto scrobble
        let autoInterval = null;
        function toggleAuto() {
            const toggle = document.getElementById('auto-toggle');
            if (autoInterval) {
                clearInterval(autoInterval);
                autoInterval = null;
                toggle.classList.remove('active');
                log('Auto off');
                toast('Auto scrobble off', 'info');
            } else {
                const ms = parseInt(document.getElementById('interval-select').value) * 1000;
                autoInterval = setInterval(scrobbleNow, ms);
                toggle.classList.add('active');
                log('Auto on');
                toast('Auto scrobble on');
                scrobbleNow();
            }
        }

        function updateInterval() {
            const sec = parseInt(document.getElementById('interval-select').value);
            localStorage.setItem('interval', sec);
            if (autoInterval) {
                clearInterval(autoInterval);
                autoInterval = setInterval(scrobbleNow, sec * 1000);
                toast(`Interval: ${sec/60} min`, 'info');
            }
        }

        // Load config
        function loadConfig() {
            const lastfm = JSON.parse(localStorage.getItem('lastfm') || '{}');
            const interval = localStorage.getItem('interval');
            
            if (lastfm.api_key) document.getElementById('lastfm-key').value = lastfm.api_key;
            if (lastfm.api_secret) document.getElementById('lastfm-secret').value = lastfm.api_secret;
            if (lastfm.session_key) document.getElementById('lastfm-session').value = lastfm.session_key;
            if (interval) document.getElementById('interval-select').value = interval;
            
            updateGoogleButton();
        }

        // Check URL for Google callback
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('google_auth') === 'success') {
            toast('YouTube Music connected');
            log('Google auth success');
            updateGoogleButton();
            window.history.replaceState({}, '', '/');
        } else if (urlParams.get('google_auth') === 'error') {
            toast('Google auth failed', 'error');
            window.history.replaceState({}, '', '/');
        }

        loadConfig();
        checkStatus();
    </script>
</body>
</html>
'''

# Store for tracking scrobbled tracks
scrobbled_tracks = set()


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


def get_ytmusic_client(config):
    """Initialize YT Music client"""
    ytmusic_config = config.get('ytmusic', {})
    google_token = config.get('google_token')
    
    # Try Google OAuth token first
    if google_token:
        try:
            token_data = json.loads(google_token) if isinstance(google_token, str) else google_token
            if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
                oauth_creds = OAuthCredentials(client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET)
                ytmusic = YTMusic(auth=token_data, oauth_credentials=oauth_creds)
            else:
                ytmusic = YTMusic(auth=token_data)
            return ytmusic, None
        except Exception as e:
            pass
    
    if not ytmusic_config:
        return None, "Not configured"
    
    try:
        # OAuth format
        if 'access_token' in ytmusic_config:
            if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
                oauth_creds = OAuthCredentials(client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET)
                ytmusic = YTMusic(auth=ytmusic_config, oauth_credentials=oauth_creds)
            else:
                ytmusic = YTMusic(auth=ytmusic_config)
            return ytmusic, None
        # Browser headers format
        elif 'cookie' in ytmusic_config or 'Cookie' in ytmusic_config:
            ytmusic = YTMusic(auth=ytmusic_config)
            return ytmusic, None
        else:
            return None, "Invalid format"
    except Exception as e:
        return None, str(e)


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/google-auth')
def google_auth():
    """Start Google OAuth flow"""
    if not GOOGLE_CLIENT_ID:
        return jsonify({'error': 'Google OAuth not configured'}), 400
    
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    
    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(GOOGLE_SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state
    }
    
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)


@app.route('/api/google-callback')
def google_callback():
    """Handle Google OAuth callback"""
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        return redirect('/?google_auth=error')
    
    if not code:
        return redirect('/?google_auth=error')
    
    try:
        # Exchange code for tokens
        token_response = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': GOOGLE_REDIRECT_URI
        })
        
        if token_response.status_code != 200:
            return redirect('/?google_auth=error')
        
        tokens = token_response.json()
        
        # Format for ytmusicapi
        oauth_token = {
            'access_token': tokens.get('access_token'),
            'refresh_token': tokens.get('refresh_token'),
            'token_type': tokens.get('token_type', 'Bearer'),
            'expires_at': int(time.time()) + tokens.get('expires_in', 3600),
            'expires_in': tokens.get('expires_in', 3600),
            'scope': tokens.get('scope', '')
        }
        
        # Return HTML that saves token and redirects
        return f'''
        <!DOCTYPE html>
        <html>
        <head><title>Success</title></head>
        <body>
            <script>
                localStorage.setItem('google_token', JSON.stringify({json.dumps(oauth_token)}));
                localStorage.setItem('ytmusic', JSON.stringify({json.dumps(oauth_token)}));
                window.location.href = '/?google_auth=success';
            </script>
        </body>
        </html>
        '''
    except Exception as e:
        print(f"OAuth error: {e}")
        return redirect('/?google_auth=error')


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
            ytmusic.get_library_songs(limit=1)
            ytmusic_status = {'connected': True}
        except:
            ytmusic_status = {'connected': False}
    else:
        ytmusic_status = {'connected': False}
    
    return jsonify({'lastfm': lastfm_status, 'ytmusic': ytmusic_status})


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
            if item.get('videoId'):
                track_id = f"{item.get('title')}_{item.get('artists', [{}])[0].get('name', 'Unknown')}"
                tracks.append({
                    'title': item.get('title', 'Unknown'),
                    'artist': item.get('artists', [{}])[0].get('name', 'Unknown'),
                    'album': item.get('album', {}).get('name', ''),
                    'videoId': item.get('videoId'),
                    'scrobbled': track_id in scrobbled_tracks
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
        history = ytmusic.get_history()
        
        scrobbled_count = 0
        current_time = int(time.time())
        
        for i, item in enumerate(history[:10]):
            if not item.get('videoId'):
                continue
            
            title = item.get('title', 'Unknown')
            artist = item.get('artists', [{}])[0].get('name', 'Unknown')
            album = item.get('album', {}).get('name', '')
            
            track_id = f"{title}_{artist}"
            
            if track_id in scrobbled_tracks:
                continue
            
            try:
                timestamp = current_time - (i * 180)
                network.scrobble(
                    artist=artist,
                    title=title,
                    timestamp=timestamp,
                    album=album if album else None
                )
                scrobbled_tracks.add(track_id)
                scrobbled_count += 1
            except Exception as e:
                print(f"Error scrobbling {title}: {e}")
        
        return jsonify({'success': True, 'count': scrobbled_count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


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
