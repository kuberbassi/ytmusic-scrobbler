// YT Music Scrobbler - Core Frontend Engine

document.getElementById('year').textContent = new Date().getFullYear();

// Toast Notifications
const escapeHtml = value => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function toast(msg, type = 'success') {
    const container = document.getElementById('toasts');
    if (!container) return;
    const toastEl = document.createElement('div');
    toastEl.className = `toast ${type}`;
    const dot = document.createElement('span');
    dot.className = 'toast-dot';
    toastEl.append(dot, document.createTextNode(String(msg)));
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
    if (id === 'history') loadHistory();
}

// Configuration accessor
function getConfig() {
    return {};
}

// Logging helper
function log(msg) {
    const l = document.getElementById('log');
    if (!l) return;
    const time = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    l.insertAdjacentHTML('afterbegin', `<div class="log-entry"><span class="time">[${time}]</span> ${escapeHtml(msg)}</div>`);
}

let lastSyncTimestamp = 0;
let statusRequestInFlight = false;
let lastValidatedAt = 0;
async function checkStatus(validate = false) {
    if (statusRequestInFlight || document.hidden) return;
    statusRequestInFlight = true;
    try {
        const res = await fetch(`/api/status${validate ? '?validate=1' : ''}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getConfig())
        });
        const data = await res.json();
        if (validate) lastValidatedAt = Date.now();

        // Update Logs
        if (data.logs && data.logs.length > 0) {
            const l = document.getElementById('log');
            if (l) {
                l.innerHTML = data.logs.map(entry => {
                    const time = new Date(entry.time * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                    const statusClass = (entry.status && entry.status.includes('Error')) ? 'error' : '';
                    return `<div class="log-entry ${statusClass}">
                        <span class="time">[${time}]</span> 
                        <b>${escapeHtml(entry.artist)}</b> - ${escapeHtml(entry.title)}
                        &nbsp;<span style="font-size:10px;color:var(--text-tertiary);float:right;">${escapeHtml(entry.status)}</span>
                    </div>`;
                }).join('');
            }
        }

        if (data.last_sync > lastSyncTimestamp) {
            lastSyncTimestamp = data.last_sync;
            const historyTab = document.getElementById('history');
            if (historyTab && historyTab.classList.contains('active')) loadHistory();
        }

        // Update Status Badges & Nav Pill
        const lfm = document.getElementById('lastfm-status');
        const lfmNav = document.getElementById('nav-lfm');
        const lfmSub = document.getElementById('lastfm-username');

        if (lfm && (data.lastfm.validated || lastfmConnectionValid === null)) {
            if (data.lastfm.connected) {
                lastfmConnectionValid = true;
                lfm.innerHTML = '<span class="dot"></span> Online';
                lfm.className = 'status-badge online';
                if (lfmNav) lfmNav.className = 'nav-status-pill online';
                if (lfmSub) lfmSub.textContent = `@${data.lastfm.username || 'Authorized'}`;
            } else {
                lastfmConnectionValid = false;
                lfm.innerHTML = '<span class="dot"></span> Offline';
                lfm.className = 'status-badge offline';
                if (lfmNav) lfmNav.className = 'nav-status-pill';
                if (lfmSub) lfmSub.textContent = data.lastfm.error || 'Credentials required';
            }
            updateLastfmState();
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
                if (ytmSub) ytmSub.textContent = data.ytmusic.error || 'Headers required';
            }
        }

        // Sync Info Text
        const syncInfo = document.getElementById('sync-info');
        const npText = document.getElementById('now-playing-text');

        if (syncInfo && data.sync_error) {
            syncInfo.textContent = `Auto-sync needs attention: ${data.sync_error}`;
            syncInfo.className = 'sync-info error';
        } else if (syncInfo && data.last_sync > 0) {
            const diff = Math.floor((data.now - data.last_sync) / 60);
            const syncText = diff === 0 ? 'Synced just now' : `Synced ${diff}m ago`;
            syncInfo.innerText = syncText;
            syncInfo.className = 'sync-info active';
            if (data.last_track && npText) {
                npText.innerHTML = `Last Scrobbled: <strong>${escapeHtml(data.last_track)}</strong>`;
            }
        } else if (syncInfo) {
            syncInfo.innerText = 'Waiting for first sync...';
            syncInfo.className = 'sync-info';
        }
    } catch (e) {
        console.error('Status check failed', e);
    } finally {
        statusRequestInFlight = false;
    }
}

// Save Last.fm Configuration
async function saveLastfm() {
    const sessionKey = document.getElementById('lastfm-session').value.trim();
    const config = {
        api_key: document.getElementById('lastfm-key').value.trim(),
        api_secret: document.getElementById('lastfm-secret').value.trim(),
        ...(sessionKey ? { session_key: sessionKey } : {})
    };
    if (!config.api_key || !config.api_secret) return toast('Enter API key and secret', 'error');
    const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lastfm: config })
    });
    if (!res.ok) return toast('Could not save Last.fm account', 'error');
    document.getElementById('lastfm-key').value = '';
    document.getElementById('lastfm-secret').value = '';
    document.getElementById('lastfm-session').value = '';

    lastfmApiConfigured = true;
    lastfmSessionConfigured = Boolean(sessionKey) || lastfmSessionConfigured;
    lastfmEditing = false;
    updateLastfmState();

    toast('Last.fm account saved!');
    log('Last.fm config saved');
    checkStatus(true);
}

let lastfmApiConfigured = false;
let lastfmSessionConfigured = false;
let lastfmConnectionValid = null;
let lastfmEditing = false;

function getLastfmAuthorizationNotice() {
    try {
        return JSON.parse(sessionStorage.getItem('lastfm-authorization-success') || 'null');
    } catch {
        return null;
    }
}

function updateLastfmState() {
    const form = document.getElementById('lastfm-form');
    const panel = document.getElementById('lastfm-connected-panel');
    const title = document.getElementById('lastfm-connected-title');
    const description = document.getElementById('lastfm-connected-description');
    const sessionStatus = document.getElementById('lastfm-session-status');
    const cancel = document.getElementById('lastfm-cancel-edit');
    const showPanel = lastfmApiConfigured && !lastfmEditing;
    if (form) form.style.display = showPanel ? 'none' : 'block';
    if (panel) {
        panel.style.display = showPanel ? 'flex' : 'none';
        const needsAttention = lastfmConnectionValid === false || !lastfmSessionConfigured;
        panel.classList.toggle('needs-attention', needsAttention);
        const icon = panel.querySelector('.account-connected-icon');
        if (icon) icon.textContent = needsAttention ? '!' : '✓';
        const authorizationNotice = !needsAttention && getLastfmAuthorizationNotice();
        if (title) title.textContent = needsAttention
            ? 'Last.fm authorization required'
            : authorizationNotice
                ? 'Last.fm authorization successful'
                : 'Last.fm connected';
        if (description) description.textContent = needsAttention
            ? 'Your API credentials are saved securely. Reauthorize the Last.fm session.'
            : authorizationNotice
                ? `Session key received${authorizationNotice.username ? ` for ${authorizationNotice.username}` : ''} and stored securely. It is intentionally hidden.`
                : 'API key, secret, and session key are stored securely. The secret values are intentionally hidden.';
        if (sessionStatus) sessionStatus.innerHTML = needsAttention
            ? '<b>!</b> Session key authorization required'
            : '<b>✓</b> Session key received and saved securely';
    }
    if (cancel) cancel.style.display = lastfmEditing && lastfmApiConfigured ? 'inline-flex' : 'none';
}

function showLastfmEditor() {
    lastfmEditing = true;
    updateLastfmState();
    document.getElementById('lastfm-key')?.focus();
}

function cancelLastfmEditor() {
    lastfmEditing = false;
    for (const id of ['lastfm-key', 'lastfm-secret', 'lastfm-session']) {
        const input = document.getElementById(id);
        if (input) input.value = '';
    }
    updateLastfmState();
}

// Disconnect YouTube Music
async function disconnectYT() {
    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ytmusic: { headers: null } })
        });
        if (!response.ok) throw new Error('Server rejected configuration update');
    } catch (e) {
        console.error("Failed to clear YT config on server", e);
        return toast('Could not disconnect YouTube Music', 'error');
    }

    toast('Disconnected from YouTube', 'info');
    ytmusicConfigured = false;
    log('Disconnected YouTube Music');
    checkStatus();
    updateYTState();
}

// Save Browser Headers
async function saveYTHeaders() {
    const headers = document.getElementById('yt-headers').value.trim();
    if (!headers) return toast('Paste headers first', 'error');

    const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ytmusic: { headers } })
    });
    if (!res.ok) return toast('Could not save YouTube Music headers', 'error');
    document.getElementById('yt-headers').value = '';
    ytmusicConfigured = true;

    toast('Headers connected!');
    log('YouTube Music connected');

    updateYTState();
    checkStatus(true);
    setTimeout(loadHistory, 300);
}

// Update YouTube State
let ytmusicConfigured = false;
function updateYTState() {
    const disconnectBtn = document.getElementById('disconnect-btn');
    const headerSection = document.getElementById('method-headers');

    if (disconnectBtn && headerSection) {
        if (ytmusicConfigured) {
            disconnectBtn.style.display = 'inline-flex';
            headerSection.style.display = 'none';
        } else {
            disconnectBtn.style.display = 'none';
            headerSection.style.display = 'block';
        }
    }
}

// Authorize Last.fm Popup. Stored credentials are used server-side after the
// first save, so reconnecting never requires secrets to be returned to JS.
async function authorizeLastfm() {
    const key = document.getElementById('lastfm-key').value.trim();
    const secret = document.getElementById('lastfm-secret').value.trim();
    if (key) {
        if (!secret) return toast('Enter the API secret before authorizing', 'error');

        // Open synchronously so browsers do not block the popup while credentials save.
        const authWindow = window.open('', 'lastfm', 'width=500,height=600');
        try {
            const saveResponse = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lastfm: { api_key: key, api_secret: secret, session_key: null } })
            });
            if (!saveResponse.ok) throw new Error('Could not save credentials');
            lastfmApiConfigured = true;
            lastfmSessionConfigured = false;
            lastfmConnectionValid = false;
            try { sessionStorage.removeItem('lastfm-authorization-success'); } catch { /* optional */ }
            updateLastfmState();
            toast('Credentials saved. Continue authorization in the popup.');
            const cb = encodeURIComponent(window.location.origin + '/api/lastfm-callback');
            const authUrl = `https://www.last.fm/api/auth/?api_key=${encodeURIComponent(key)}&cb=${cb}`;
            if (authWindow) authWindow.location.href = authUrl;
            else window.location.href = authUrl;
        } catch {
            if (authWindow) authWindow.close();
            toast('Could not save Last.fm credentials', 'error');
        }
        return;
    }
    try {
        const res = await fetch('/api/lastfm-auth-url');
        const data = await res.json();
        if (!res.ok || !data.url) return toast(data.error || 'Save Last.fm credentials first', 'error');
        window.open(data.url, 'lastfm', 'width=500,height=600');
    } catch {
        toast('Could not start Last.fm authorization', 'error');
    }
}

window.addEventListener('message', async (e) => {
    if (e.origin === window.location.origin && e.data?.type === 'lastfm-token') {
        const key = document.getElementById('lastfm-key').value.trim();
        const secret = document.getElementById('lastfm-secret').value.trim();
        try {
            const res = await fetch('/api/lastfm-session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: key, api_secret: secret, token: e.data.token })
            });
            const data = await res.json();
            if (data.success && data.session_stored) {
                try {
                    sessionStorage.setItem('lastfm-authorization-success', JSON.stringify({ username: data.username || '' }));
                } catch { /* The connected state still confirms success when storage is unavailable. */ }
                toast('Authorized as ' + data.username);
                log('Last.fm: Authorized as ' + data.username);
                document.getElementById('lastfm-secret').value = '';
                document.getElementById('lastfm-session').value = '';
                await loadConfig();
                checkStatus(true);
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
            checkStatus(true);
        }
    } catch (e) {
        toast('Scrobble error', 'error');
        log('Scrobble error');
        checkStatus(true);
    }
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
        if (data.error) return list.innerHTML = `<div class="empty">${escapeHtml(data.error)}</div>`;
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
            if (/invalid session key|re-authenticate/i.test(data.error || '')) {
                lastfmConnectionValid = false;
                lastfmSessionConfigured = false;
                try { sessionStorage.removeItem('lastfm-authorization-success'); } catch { /* optional */ }
                updateLastfmState();
                toast('Last.fm authorization expired. Open Accounts and select Reauthorize.', 'error');
            }
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
    } else {
        toggle.classList.remove('active');
        localStorage.setItem('autoScrobble', 'false');
    }

    if (!await saveConfigToServer()) {
        toggle.classList.toggle('active', !isEnabled);
        localStorage.setItem('autoScrobble', isEnabled ? 'false' : 'true');
        toast('Could not update Auto Scrobble', 'error');
        return;
    }
    log(`Auto Scrobble Engine: ${isEnabled ? 'ON' : 'OFF'}`);
    toast(`Server Auto Scrobble ${isEnabled ? 'ON' : 'OFF'}`, isEnabled ? 'success' : 'info');
}

// Server Config Sync
async function saveConfigToServer() {
    const auto_scrobble = localStorage.getItem('autoScrobble') === 'true';

    const config = {
        auto_scrobble: auto_scrobble,
        interval: 300
    };

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        return response.ok;
    } catch (e) {
        console.error("Sync to server failed", e);
        return false;
    }
}

// Load Config
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        const config = await res.json();

        localStorage.removeItem('lastfm');
        localStorage.removeItem('yt_headers');
        localStorage.removeItem('ytmusic');
        ytmusicConfigured = Boolean(config.ytmusic_configured);
        lastfmApiConfigured = Boolean(config.lastfm_api_configured);
        lastfmSessionConfigured = Boolean(config.lastfm_configured);
        const keyInput = document.getElementById('lastfm-key');
        const secretInput = document.getElementById('lastfm-secret');
        const sessionInput = document.getElementById('lastfm-session');
        if (config.lastfm_api_configured) {
            if (keyInput) keyInput.placeholder = 'Saved securely — leave blank to keep';
            if (secretInput) secretInput.placeholder = 'Saved securely — leave blank to keep';
        }
        if (config.lastfm_configured && sessionInput) {
            sessionInput.placeholder = 'Authorized — stored securely';
        }
        updateLastfmState();

        if (config.auto_scrobble !== undefined) {
            localStorage.setItem('autoScrobble', config.auto_scrobble ? 'true' : 'false');
            const toggle = document.getElementById('auto-toggle');
            if (toggle) toggle.classList.toggle('active', config.auto_scrobble);
        }
    } catch (e) {
        console.error("Failed to load server config", e);
        toast('Could not load server configuration', 'error');
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
                        <img class="user-avatar" src="${escapeHtml(data.user.picture || '/Icon.png')}" alt="">
                        <span class="user-name">${escapeHtml(data.user.name || data.user.email)}</span>
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
    checkLoginState().then(async () => {
        const mainApp = document.getElementById('main-app');
        if (mainApp && mainApp.style.display !== 'none') {
            await loadConfig();
            await checkStatus(true);
        }
    });

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            const shouldValidate = Date.now() - lastValidatedAt > 5 * 60 * 1000;
            checkStatus(shouldValidate);
            const historyTab = document.getElementById('history');
            if (historyTab && historyTab.classList.contains('active')) loadHistory();
        }
    });
});
