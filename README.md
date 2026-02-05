# YT Music Scrobbler 🎵

Automatically scrobble your YouTube Music listening history to Last.fm. Works for **all devices** (Phone, PC, TV, Nest) by reading your central YouTube Watch History.

**Live:** [ytscrobbler.kuberbassi.com](https://ytscrobbler.kuberbassi.com)

## 🌟 Features
- **Google Sign-In:** Secure authentication with your Google account
- **Multi-User Support:** Each user gets their own scrobble history stored in the cloud
- **Cross-Device Sync:** Listen on any device, scrobbles sync automatically
- **Background Sync:** Vercel Cron or external cron (cron-job.org) scrobbles every 5 minutes
- **Smart Deduplication:** Triple-UID system (videoId, title_artist, normalized) prevents duplicate scrobbles
- **Real-Time Dashboard:** See your scrobbles update live
- **Persistent Storage:** Supabase database ensures nothing is lost
- **Rate Limiting:** Per-endpoint rate limits protect against abuse
- **Security Headers:** CSP, X-Frame-Options, XSS protection on all responses
- **Scalable:** Optimized for 5000+ users with memory-efficient batch processing

## 🚀 Quick Start (User)

1. Go to [ytscrobbler.kuberbassi.com](https://ytscrobbler.kuberbassi.com)
2. Sign in with Google
3. Connect your Last.fm account (enter API Key/Secret, authorize)
4. Paste your YT Music browser headers
5. Enable Auto Scrobble - done!

Your listening history from any device will now scrobble automatically.

---

## 🛠️ Self-Hosting

### Requirements
- Python 3.8+
- Supabase account (free)
- Google Cloud Console project (for OAuth)
- Vercel account (for deployment)

### 1. Database Setup (Supabase)
1. Create a project at [supabase.com](https://supabase.com)
2. Run `schema.sql` in SQL Editor
3. Get your API URL and anon key from Settings > API

### 2. Google OAuth Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → APIs & Services → Credentials
3. Create OAuth 2.0 Client ID (Web application)
4. Add authorized redirect URI: `https://your-domain.com/auth/google/callback`
5. Copy Client ID and Client Secret

### 3. Deploy to Vercel
1. Fork this repo
2. Connect to Vercel
3. Add environment variables:
   ```
   SUPABASE_URL=https://xxx.supabase.co
   SUPABASE_KEY=your-anon-key
   GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-xxx
   GOOGLE_REDIRECT_URI=https://your-domain.com/auth/google/callback
   SECRET_KEY=random-secret-for-sessions
   CRON_SECRET=random-secret-for-cron-auth
   ```
4. Deploy!

### 4. Local Development
```bash
git clone https://github.com/kuberbassi/ytmusic-scrobbler.git
cd ytmusic-scrobbler
pip install -r requirements.txt
# Create .env with the variables above (use localhost:3000 for redirect)
python local_run.py
```

## 📱 How It Works

1. **Authentication:** Users sign in with Google → stored in `users` table
2. **Credentials:** Each user saves their Last.fm + YT Music credentials → stored per-user in Supabase
3. **Background Sync:** Vercel Cron (or external like cron-job.org) calls `/api/cron` every 5 minutes
4. **Scrobble Logic:** Fetches YT Music history → checks against stored scrobbles → sends new ones to Last.fm
5. **Deduplication:** Triple-UID system (videoId, title_artist, normalized) ensures no duplicates

> **Note:** Repeat detection was intentionally removed. Since the YT Music API doesn't provide real-time playback status (can't tell if music is playing, paused, or stopped), only first plays are scrobbled. This prevents false scrobbles when the user stops listening.

## 🏗️ Architecture

| Component | Technology |
|-----------|------------|
| Backend | Flask (Python) |
| Database | Supabase (PostgreSQL) |
| Auth | Google OAuth 2.0 |
| Hosting | Vercel (Serverless) |
| Background Jobs | Vercel Cron / External (cron-job.org) |

## 🔒 Security & Rate Limiting

**Security Headers** (auto-applied to all responses):
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Content-Security-Policy` (HTML only)

**Rate Limits:**
| Endpoint | Limit |
|----------|-------|
| Default | 60/min |
| Scrobble | 10/min |
| Auth | 5/min |
| Cron | 2/min |

## ⏰ Cron Configuration

For external cron services (e.g., cron-job.org):
```
URL: https://your-domain.com/api/cron
Schedule: Every 5 minutes
Header: Authorization: Bearer YOUR_CRON_SECRET
```

Set `CRON_SECRET` env var to secure the endpoint.

**Query params for large-scale deployments:**
- `batch_size` - Users per batch (default 50, max 100)
- `offset` - Starting offset for distributed processing
- `max_users` - Max users per run (default 200, max 500)

## 📄 License
MIT License
