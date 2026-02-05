# 🎵 YT Music Scrobbler

> Sync YouTube Music → Last.fm automatically

```
┌─────────────────┐         ┌─────────────────┐
│  YouTube Music  │ ──────► │     Last.fm     │
│    (History)    │         │   (Scrobbles)   │
└─────────────────┘         └─────────────────┘
```

## ✨ Features

| | |
|---|---|
| 🔗 | **One-click Google Sign In** |
| 🔄 | **Auto Scrobble** (1-15 min intervals) |
| 🌙 | **Dark/Light Mode** |
| ☁️ | **Deploy to Vercel** |

## 🚀 Deploy

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/kuberbassi/ytmusic-scrobbler)

### Environment Variables

```env
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxx
GOOGLE_REDIRECT_URI=https://your-app.vercel.app/api/google-callback
```

## 🔧 Setup

### 1️⃣ Last.fm
```
last.fm/api → Create API account → Copy Key & Secret
```

### 2️⃣ Google Cloud
```
console.cloud.google.com
    │
    ├── Create Project
    ├── Enable YouTube Data API v3
    ├── Create OAuth (Web app)
    └── Add Redirect URI
```

## 💻 Local Dev

```bash
git clone https://github.com/kuberbassi/ytmusic-scrobbler.git
cd ytmusic-scrobbler
pip install -r requirements.txt
python local_run.py
```

→ Open http://localhost:3000

## 📁 Structure

```
├── api/index.py      # Flask app
├── local_run.py      # Dev server
├── requirements.txt
└── vercel.json
```

## 📝 License

MIT © [Kuber Bassi](https://kuberbassi.com)
