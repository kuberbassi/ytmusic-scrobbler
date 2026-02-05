# YT Music Scrobbler

**YouTube Music → Last.fm**

```
┌────────────────┐      ┌────────────────┐
│ YouTube Music  │ ───► │    Last.fm     │
└────────────────┘      └────────────────┘
```

---

## What it does

Syncs your YouTube Music listening history to Last.fm automatically.

---

## Features

- ✓ Sign in with Google
- ✓ Auto scrobble (1-15 min)
- ✓ Dark/Light mode
- ✓ Deploy to Vercel

---

## Deploy

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/kuberbassi/ytmusic-scrobbler&env=GOOGLE_CLIENT_ID,GOOGLE_CLIENT_SECRET,GOOGLE_REDIRECT_URI)

**Set these in Vercel:**

| Variable | Value |
|----------|-------|
| `GOOGLE_CLIENT_ID` | from Google Cloud |
| `GOOGLE_CLIENT_SECRET` | from Google Cloud |
| `GOOGLE_REDIRECT_URI` | `https://your-app.vercel.app/api/google-callback` |

---

## Setup

### Last.fm

1. Go to [last.fm/api/account/create](https://www.last.fm/api/account/create)
2. Create API account
3. Copy API Key & Secret

### Google Cloud

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create project
3. Enable **YouTube Data API v3**
4. Create **OAuth 2.0 Client** (Web application)
5. Add redirect URI: `http://localhost:3000/api/google-callback`

---

## Local Dev

```bash
git clone https://github.com/kuberbassi/ytmusic-scrobbler.git
cd ytmusic-scrobbler
pip install -r requirements.txt
cp .env.example .env  # add your keys
python local_run.py
```

Open http://localhost:3000

---

## License

MIT © [Kuber Bassi](https://kuberbassi.com)
