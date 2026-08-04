<div align="center">
  <img src="public/Icon.png" alt="YT Music Scrobbler Logo" width="108" height="108" style="border-radius: 22px; box-shadow: 0 8px 24px rgba(255, 42, 75, 0.25);">
  <h1>YT Music Scrobbler</h1>
  <p><strong>Sync YouTube Music listening history to Last.fm automatically across all your devices.</strong></p>

  <p>
    <a href="https://ytscrobbler.kuberbassi.com"><img src="https://img.shields.io/badge/Live_App-ytscrobbler.kuberbassi.com-ff2a4b?style=for-the-badge&logo=youtube-music&logoColor=white" alt="Live App"></a>
    <a href="https://github.com/kuberbassi/ytmusic-scrobbler"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="License"></a>
  </p>
</div>

---

## 🚀 Key Features

- **📱 Cross-Device Coverage** — Scrobbles listening history from Phone, Desktop Web, Smart TV, or Google Nest devices via central YouTube Watch History.
- **⚡ Modular Web Architecture** — Clean separation of concerns with HTML templates, Vanilla CSS design tokens, modular JavaScript engines, and lightweight Python backend API routes.
- **🎸 Smart Performance Video Filter** — Intelligently scrobbles music performance videos, acoustic covers, live renditions, studio sessions, and instrumentals (e.g., *Twin Strings Acoustic*, *Davie504 Bass Cover*) while auto-filtering non-music videos (e.g., reaction videos, vlogs, reviews, gameplay).
- **🔒 Bulletproof Deduplication & Single-Scrobble Lock** — Multi-UID mapping (`videoId`, raw string, normalized title/artist, primary artist) combined with in-process locking prevents duplicate scrobbles during rapid syncs.
- **🕒 20-Minute History Freshness Cooldown** — History list items refresh intelligently so songs scrobbled hours ago render as pending when played again.
- **🤖 Background Sync Engine** — Automated background sync with Vercel Cron or local worker polling.
- **🔐 Multi-User Cloud Support** — Google Sign-In with isolated PostgreSQL Row-Level Security in Supabase.

---

## 📁 Project Structure

```
ytmusic-scrobbler/
├── api/
│   ├── database.py       # Supabase PostgreSQL database layer & RLS
│   └── index.py          # Flask backend API routes & scrobbling engine
├── static/
│   ├── css/
│   │   └── styles.css    # Unified Vanilla CSS design system & tokens
│   └── js/
│       ├── app.js        # Main client application engine
│       └── legal.js      # Legal pages script helper
├── templates/
│   ├── index.html        # Main dashboard & landing page
│   ├── legal.html        # Terms of Service & Privacy Policy template
│   └── callback.html     # Last.fm OAuth popup verification template
├── public/               # Static public assets (Icon.png, robots.txt, sitemap.xml)
├── local_run.py          # Local development server runner
├── vercel.json           # Vercel CDN static routes & serverless config
└── requirements.txt      # Python dependencies
```

---

## ⚡ Quick Start

1. Visit [ytscrobbler.kuberbassi.com](https://ytscrobbler.kuberbassi.com)
2. Sign in with Google.
3. Save your Last.fm API credentials and session key in the **Accounts** tab.
4. Paste your YT Music browser request headers.
5. Enable **Auto Scrobble**.

---

## 💻 Local Development

```bash
# 1. Clone repository
git clone https://github.com/kuberbassi/ytmusic-scrobbler.git
cd ytmusic-scrobbler

# 2. Setup virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run locally
python local_run.py
```

Open `http://localhost:3000` in your browser.

---

## 🌐 Deployment (Vercel)

Set the following environment variables on Vercel:

```env
SUPABASE_URL=https://your-supabase-project.supabase.co
SUPABASE_KEY=your-supabase-anon-key
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
GOOGLE_REDIRECT_URI=https://your-domain.com/auth/google/callback
SECRET_KEY=your-random-session-secret
CRON_SECRET=your-cron-secret
```

---

## 📜 License

[MIT](LICENSE)
