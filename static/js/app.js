// YT Music Scrobbler - Core Frontend Engine

document.getElementById('year').textContent = new Date().getFullYear();

// Toast Notifications
function toast(msg, type = 'success') {
    const container = document.getElementById('toasts');
    if (!container) return;
    const toastEl = document.createElement('div');
    toastEl.className = `toast ${type}`;
    toastEl.innerHTML = `<span class="toast-dot"></span>${msg}`;
    container.appendChild(toastEl);
    requestAnimationFrame(() => toastEl.classList.add('show'));
    setTimeout(() => {
        toastEl.classList.remove('show');
        setTimeout(() => toastEl.remove(), 250);
    }, 3200);
}

// Theme Toggle
function toggleTheme() {
    const isLight = document.body.getAttribute('data-theme') === 'light';
    document.body.setAttribute('data-theme', isLight ? 'dark' : 'light');
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.innerHTML = isLight
            ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
            : '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
    }
    localStorage.setItem('theme', isLight ? 'dark' : 'light');
}
if (localStorage.getItem('theme') === 'light') {
    document.body.setAttribute('data-theme', 'light');
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
    }
}

// Navigation Tabs
function showTab(id) {
    const tabMap = { 'dashboard': 0, 'connect': 1, 'history': 2, 'guide': 3 };
    document.querySelectorAll('.tab').forEach((t, i) => {
        t.classList.toggle('active', i === tabMap[id]);
    });
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const target = document.getElementById(id);
    if (target) target.classList.add('active');
}

// Configuration accessor
function getConfig() {
    return {
        lastfm: JSON.parse(localStorage.getItem('lastfm') || '{}'),
        ytmusic: JSON.parse(localStorage.getItem('ytmusic') || '{}')
    };
}

// Logging helper
function log(msg) {
    const l = document.getElementById('log');
    if (!l) return;
    const time = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    l.innerHTML = `<div class="log-entry"><span class="time">[${time}]</span> ${msg}</div>` + l.innerHTML;
}

let lastSyncTimestamp = 0;
async function checkStatus() {
    try {
        const res = await fetch('/api/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getConfig())
        });
        const data = await res.json();

        // Update Logs
        if (data.logs && data.logs.length > 0) {
            const l = document.getElementById('log');
            if (l) {
                l.innerHTML = data.logs.map(entry => {
                    const time = new Date(entry.time * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                    const statusClass = (entry.status && entry.status.includes('Error')) ? 'error' : '';
                    return `<div class="log-entry ${statusClass}">
                        <span class="time">[${time}]</span> 
                        <b>${entry.artist}</b> - ${entry.title} 
                        &nbsp;<span style="font-size:10px;color:var(--text-tertiary);float:right;">${entry.status}</span>
                    </div>`;
                }).join('');
            }
        }

        if (data.last_sync > lastSyncTimestamp) {
            lastSyncTimestamp = data.last_sync;
            loadHistory();
        }

        // Update Status Badges & Nav Pill
        const lfm = document.getElementById('lastfm-status');
        const lfmNav = document.getElementById('nav-lfm');
        const lfmSub = document.getElementById('lastfm-username');

        if (lfm) {
            if (data.lastfm.connected) {
                lfm.innerHTML = '<span class="dot"></span> Online';
                lfm.className = 'status-badge online';
                if (lfmNav) lfmNav.className = 'nav-status-pill online';
                if (lfmSub) lfmSub.textContent = `@${data.lastfm.username || 'Authorized'}`;
            } else {
                lfm.innerHTML = '<span class="dot"></span> Offline';
                lfm.className = 'status-badge offline';
                if (lfmNav) lfmNav.className = 'nav-status-pill';
                if (lfmSub) lfmSub.textContent = 'Not connected';
            }
        }

        const ytm = document.getElementById('ytmusic-status');
        const ytmNav = document.getElementById('nav-ytm');
        const ytmSub = document.getElementById('ytmusic-sub');

        if (ytm) {
            if (data.ytmusic.connected) {
                ytm.innerHTML = '<span class="dot"></span> Online';
                ytm.className = 'status-badge online';
                if (ytmNav) ytmNav.className = 'nav-status-pill online';
                if (ytmSub) ytmSub.textContent = data.ytmusic.warning || 'Connected via Headers';
            } else {
                ytm.innerHTML = '<span class="dot"></span> Offline';
                ytm.className = 'status-badge offline';
                if (ytmNav) ytmNav.className = 'nav-status-pill';
                if (ytmSub) ytmSub.textContent = 'Headers required';
            }
        }

        // Sync Info Text
        const syncInfo = document.getElementById('sync-info');
        const npText = document.getElementById('now-playing-text');

        if (syncInfo && data.last_sync > 0) {
            const diff = Math.floor((data.now - data.last_sync) / 60);
            const syncText = diff === 0 ? 'Synced just now' : `Synced ${diff}m ago`;
            syncInfo.innerText = syncText;
            syncInfo.className = 'sync-info active';
            if (data.last_track && npText) {
                npText.innerHTML = `Last Scrobbled: <strong>${data.last_track}</strong>`;
            }
        } else if (syncInfo) {
            syncInfo.innerText = 'Waiting for first sync...';
            syncInfo.className = 'sync-info';
        }
    } catch (e) { console.error('Status check failed', e); }
}

// Save Last.fm Configuration
async function saveLastfm() {
    const config = {
        api_key: document.getElementById('lastfm-key').value.trim(),
        api_secret: document.getElementById('lastfm-secret').value.trim(),
        session_key: document.getElementById('lastfm-session').value.trim()
    };
    if (!config.api_key || !config.api_secret) return toast('Enter API key and secret', 'error');
    localStorage.setItem('lastfm', JSON.stringify(config));

    await saveConfigToServer();

    toast('Last.fm account saved!');
    log('Last.fm config saved');
    checkStatus();
}

// Disconnect YouTube Music
async function disconnectYT() {
    localStorage.removeItem('yt_headers');
    localStorage.removeItem('ytmusic');

    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ytmusic: { headers: null } })
        });
    } catch (e) { console.error("Failed to clear YT config on server", e); }

    toast('Disconnected from YouTube', 'info');
    log('Disconnected YouTube Music');
    checkStatus();
    updateYTState();
}

// Save Browser Headers
async function saveYTHeaders() {
    const headers = document.getElementById('yt-headers').value.trim();
    if (!headers) return toast('Paste headers first', 'error');

    localStorage.setItem('yt_headers', headers);
    localStorage.setItem('ytmusic', JSON.stringify({ headers: headers }));

    await saveConfigToServer();

    toast('Headers connected!');
    log('YouTube Music connected');

    updateYTState();
    checkStatus();
    setTimeout(loadHistory, 300);
}

// Update YouTube State
function updateYTState() {
    const headers = localStorage.getItem('yt_headers');
    const disconnectBtn = document.getElementById('disconnect-btn');
    const headerSection = document.getElementById('method-headers');

    if (disconnectBtn && headerSection) {
        if (headers) {
            disconnectBtn.style.display = 'inline-flex';
            headerSection.style.display = 'none';
        } else {
            disconnectBtn.style.display = 'none';
            headerSection.style.display = 'block';
        }
    }
}

// Authorize Last.fm Popup
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
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: key, api_secret: secret, token: e.data.token })
            });
            const data = await res.json();
            if (data.session_key) {
                document.getElementById('lastfm-session').value = data.session_key;
                toast('Authorized as ' + data.username);
                log('Last.fm: Authorized as ' + data.username);
                saveLastfm();
            } else toast(data.error || 'Failed', 'error');
        } catch { toast('Auth failed', 'error'); }
    }
});

// Trigger Manual Scrobble
const recentlyScrobbledVideoIds = new Set();
async function scrobbleNow() {
    const btn = document.querySelector('button[onclick="scrobbleNow()"]');
    const list = document.getElementById('history-list');
    if (btn) { btn.disabled = true; btn.textContent = 'Syncing…'; }
    log('Syncing YouTube Music history...');
    try {
        const res = await fetch('/api/scrobble', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getConfig())
        });
        const data = await res.json();
        if (data.success) {
            if (data.count === 0) {
                toast('Nothing new to scrobble');
                log('Up to date — 0 new tracks');
            } else {
                toast(`Scrobbled ${data.count} track${data.count !== 1 ? 's' : ''}!`);
                log(`Success: ${data.count} tracks scrobbled`);

                // Instant badge update for newly scrobbled tracks
                for (const vid of (data.scrobbled_video_ids || [])) {
                    if (vid && vid !== 'no-id') recentlyScrobbledVideoIds.add(vid);
                    if (list) {
                        const el = list.querySelector(`.track[data-video-id="${vid}"]`);
                        if (el) {
                            const pendingBadge = el.querySelector('.track-badge.pending');
                            if (pendingBadge) pendingBadge.remove();
                            if (!el.querySelector('.track-badge.done')) {
                                const badge = document.createElement('span');
                                badge.className = 'track-badge done';
                                badge.textContent = 'Scrobbled';
                                badge.style.cssText = 'opacity:0;transition:opacity 0.35s ease';
                                el.appendChild(badge);
                                requestAnimationFrame(() => requestAnimationFrame(() => { badge.style.opacity = '1'; }));
                            }
                        }
                    }
                }
            }
            await loadHistory();
        } else {
            toast(data.error || 'Failed', 'error');
            log('Error: ' + (data.error || 'Failed'));
        }
    } catch (e) { toast('Scrobble error', 'error'); log('Scrobble error'); }
    finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> Scrobble Now'; }
    }
}

// Load History List
let currentTracksCache = [];
async function loadHistory() {
    const list = document.getElementById('history-list');
    if (!list) return;
    if (!currentTracksCache.length) list.innerHTML = '<div class="empty">Loading history...</div>';
    try {
        const res = await fetch('/api/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getConfig())
        });
        const data = await res.json();
        if (data.error) return list.innerHTML = `<div class="empty">${data.error}</div>`;
        if (!data.tracks?.length) return list.innerHTML = '<div class="empty">No watch history found</div>';

        // Merge recently scrobbled video IDs so badges never revert to pending
        currentTracksCache = data.tracks.map(t => {
            if (t.videoId && t.videoId !== 'no-id' && recentlyScrobbledVideoIds.has(t.videoId)) {
                t.scrobbled = true;
            }
            return t;
        });
        renderHistoryList(currentTracksCache);
    } catch { list.innerHTML = '<div class="empty">Failed to load history</div>'; }
}

function renderHistoryList(tracks) {
    const list = document.getElementById('history-list');
    if (!list) return;
    const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    list.innerHTML = tracks.map((t, idx) => `
        <div class="track" data-track-index="${idx}" data-video-id="${esc(t.videoId)}">
            <div class="track-left">
                <div class="track-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
                </div>
                <div class="track-info">
                    <h4>${esc(t.title)}</h4>
                    <p>${esc(t.artist)}${t.album ? ' • ' + esc(t.album) : ''}</p>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:10px;">
                <button class="btn btn-secondary btn-sm" style="padding:4px 10px;font-size:12px;height:28px;" onclick="scrobbleSingle(${idx}, this)">
                    ${t.scrobbled ? 'Re-scrobble' : 'Scrobble'}
                </button>
                ${t.scrobbled ? '<span class="track-badge done">Scrobbled</span>' : '<span class="track-badge pending">Pending</span>'}
            </div>
        </div>
    `).join('');
}

async function scrobbleSingle(idx, btnEl) {
    const track = currentTracksCache[idx];
    if (!track) return toast('Track info not found', 'error');
    const { artist, title, album, videoId } = track;

    if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Scrobbling…'; }
    try {
        const res = await fetch('/api/scrobble-single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...getConfig(), artist, title, album, videoId })
        });
        const data = await res.json();
        if (data.success) {
            toast(`Scrobbled "${data.title}"!`);
            log(`Success: Scrobbled ${data.artist} - ${data.title}`);
            if (videoId && videoId !== 'no-id') recentlyScrobbledVideoIds.add(videoId);
            track.scrobbled = true;

            const trackEl = document.querySelector(`.track[data-track-index="${idx}"]`);
            if (trackEl) {
                const pendingBadge = trackEl.querySelector('.track-badge.pending');
                if (pendingBadge) pendingBadge.remove();
                if (!trackEl.querySelector('.track-badge.done')) {
                    const badge = document.createElement('span');
                    badge.className = 'track-badge done';
                    badge.textContent = 'Scrobbled';
                    trackEl.appendChild(badge);
                }
            }
            if (btnEl) { btnEl.textContent = 'Re-scrobble'; btnEl.disabled = false; }
        } else {
            toast(data.error || 'Scrobble failed', 'error');
            if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Try Again'; }
        }
    } catch (e) {
        toast('Scrobble error', 'error');
        if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Try Again'; }
    }
}

async function resetHistoryCache() {
    if (!confirm('Are you sure you want to reset your scrobble history cache? This will reset all track badges to Pending.')) return;
    try {
        const res = await fetch('/api/reset-history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getConfig())
        });
        const data = await res.json();
        if (data.success) {
            recentlyScrobbledVideoIds.clear();
            currentTracksCache = [];
            toast('Scrobble history cache reset');
            await loadHistory();
        } else toast(data.error || 'Reset failed', 'error');
    } catch { toast('Reset failed', 'error'); }
}

function filterHistory() {
    const input = document.getElementById('history-search');
    if (!input) return;
    const query = input.value.toLowerCase().trim();
    if (!query) return renderHistoryList(currentTracksCache);
    const filtered = currentTracksCache.filter(t =>
        (t.title && t.title.toLowerCase().includes(query)) ||
        (t.artist && t.artist.toLowerCase().includes(query))
    );
    renderHistoryList(filtered);
}

// Toggle Auto-Scrobble
async function toggleAuto() {
    const toggle = document.getElementById('auto-toggle');
    if (!toggle) return;
    const isEnabled = !toggle.classList.contains('active');

    if (isEnabled) {
        toggle.classList.add('active');
        localStorage.setItem('autoScrobble', 'true');
        log('Auto Scrobble Engine: ON');
        toast('Server Auto Scrobble ON');
    } else {
        toggle.classList.remove('active');
        localStorage.setItem('autoScrobble', 'false');
        log('Auto Scrobble Engine: OFF');
        toast('Server Auto Scrobble OFF', 'info');
    }

    await saveConfigToServer();
}

// Server Config Sync
async function saveConfigToServer() {
    const lastfm = JSON.parse(localStorage.getItem('lastfm') || '{}');
    const yt_headers = localStorage.getItem('yt_headers');
    const auto_scrobble = localStorage.getItem('autoScrobble') === 'true';

    const config = {
        lastfm: lastfm,
        ...(yt_headers ? { ytmusic: { headers: yt_headers } } : {}),
        auto_scrobble: auto_scrobble,
        interval: 300
    };

    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
    } catch (e) { console.error("Sync to server failed", e); }
}

// Load Config
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        const config = await res.json();

        if (config.lastfm) {
            localStorage.setItem('lastfm', JSON.stringify(config.lastfm));
            const keyEl = document.getElementById('lastfm-key');
            const secretEl = document.getElementById('lastfm-secret');
            const sessionEl = document.getElementById('lastfm-session');
            if (keyEl) keyEl.value = config.lastfm.api_key || '';
            if (secretEl) secretEl.value = config.lastfm.api_secret || '';
            if (sessionEl) sessionEl.value = config.lastfm.session_key || '';
        }

        if (config.ytmusic?.headers) {
            localStorage.setItem('yt_headers', config.ytmusic.headers);
            localStorage.setItem('ytmusic', JSON.stringify({ headers: config.ytmusic.headers }));
            const ytEl = document.getElementById('yt-headers');
            if (ytEl) ytEl.value = config.ytmusic.headers;
        }

        if (config.auto_scrobble !== undefined) {
            localStorage.setItem('autoScrobble', config.auto_scrobble ? 'true' : 'false');
            const toggle = document.getElementById('auto-toggle');
            if (toggle) toggle.classList.toggle('active', config.auto_scrobble);
        }
    } catch (e) {
        console.error("Failed to load server config, using local storage", e);
        const lastfm = JSON.parse(localStorage.getItem('lastfm') || '{}');
        const yt_headers = localStorage.getItem('yt_headers');
        const autoEnabled = localStorage.getItem('autoScrobble') === 'true';

        const keyEl = document.getElementById('lastfm-key');
        const secretEl = document.getElementById('lastfm-secret');
        const sessionEl = document.getElementById('lastfm-session');
        const ytEl = document.getElementById('yt-headers');
        const toggle = document.getElementById('auto-toggle');

        if (keyEl && lastfm.api_key) keyEl.value = lastfm.api_key;
        if (secretEl && lastfm.api_secret) secretEl.value = lastfm.api_secret;
        if (sessionEl && lastfm.session_key) sessionEl.value = lastfm.session_key;
        if (ytEl && yt_headers) ytEl.value = yt_headers;
        if (toggle && autoEnabled) toggle.classList.add('active');
    }

    updateYTState();
}

// Check Login State
let currentUser = null;
async function checkLoginState() {
    try {
        const res = await fetch('/api/user');
        const data = await res.json();

        const loginScreen = document.getElementById('login-screen');
        const mainApp = document.getElementById('main-app');
        const userArea = document.getElementById('user-area');
        const navStatus = document.getElementById('nav-status-indicators');

        const urlParams = new URLSearchParams(window.location.search);
        const errorMsg = urlParams.get('error');
        if (errorMsg) {
            toast(errorMsg, 'error');
            window.history.replaceState({}, '', '/');
        }

        if (data.logged_in) {
            currentUser = data.user;
            if (loginScreen) loginScreen.style.display = 'none';
            if (mainApp) mainApp.style.display = 'block';
            if (navStatus) navStatus.style.display = 'flex';
            if (userArea) {
                userArea.innerHTML = `
                    <div class="user-menu">
                        <img class="user-avatar" src="${data.user.picture || '/Icon.png'}" alt="">
                        <span class="user-name">${data.user.name || data.user.email}</span>
                        <a href="/auth/logout" class="logout-btn">Logout</a>
                    </div>
                `;
            }
        } else {
            if (loginScreen) loginScreen.style.display = 'flex';
            if (mainApp) mainApp.style.display = 'none';
            if (navStatus) navStatus.style.display = 'none';
        }
    } catch (e) {
        const loginScreen = document.getElementById('login-screen');
        const mainApp = document.getElementById('main-app');
        if (loginScreen) loginScreen.style.display = 'flex';
        if (mainApp) mainApp.style.display = 'none';
    }
}

localStorage.removeItem('guestMode');
const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get('google_auth')) {
    window.history.replaceState({}, '', '/');
}

// Init App
document.addEventListener('DOMContentLoaded', () => {
    checkLoginState().then(() => {
        const mainApp = document.getElementById('main-app');
        if (mainApp && mainApp.style.display !== 'none') {
            loadConfig();
            checkStatus();
            loadHistory();
        }
    });

    // Polling status every 5 seconds
    setInterval(() => {
        const mainApp = document.getElementById('main-app');
        if (mainApp && mainApp.style.display !== 'none') {
            checkStatus();
        }
    }, 5000);

    // Polling history every 10 seconds on history tab
    setInterval(() => {
        const historyTab = document.getElementById('history');
        if (historyTab && historyTab.classList.contains('active')) {
            loadHistory();
        }
    }, 10000);
});
