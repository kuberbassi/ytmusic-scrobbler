<div align="center">
  <img src="public/Icon.png" alt="YT Music Scrobbler logo" width="108" height="108">
  <h1>YT Music Scrobbler</h1>
  <p><strong>Automatically synchronize YouTube Music listening history with Last.fm.</strong></p>
  <p>
    <a href="https://ytscrobbler.kuberbassi.com">Live application</a> ·
    <a href="https://github.com/kuberbassi/ytmusic-scrobbler">GitHub</a> ·
    <a href="LICENSE">MIT License</a>
  </p>
</div>

## Features

- Background synchronization every five minutes through cron-job.org
- Works after the browser tab is closed
- Google sign-in and isolated per-user configuration
- Server-only storage for Last.fm secrets, session keys, and YouTube headers
- Persistent duplicate protection across function instances
- Music-focused filtering for YouTube Music history
- Atomic user claims, failure backoff, and bounded Vercel function runtime
- Clear connection health, expired-session warnings, and guided reconnection
- Supabase Row Level Security with no anon access to credential-bearing tables

## Accuracy limitation

YouTube Music exposes a recent-history snapshot, not exact playback timestamps or
duration. The backend can reliably detect newly observed tracks and avoid duplicate
submissions, but it cannot distinguish every legitimate replay of an already-known
track. Exact playback-aware repeat scrobbling would require a browser, desktop, or
mobile companion that observes the player directly.

## Project structure

```text
api/
  database.py       Supabase REST persistence and sync claims
  index.py          Flask routes and scrobbling engine
public/             Icon, robots.txt, and sitemap.xml
static/
  css/styles.css    Application styles
  js/app.js         Dashboard behavior
  js/legal.js       Legal-page behavior
supabase/
  migrations/       Production-safe database migrations
templates/          Dashboard, callback, and legal templates
tests/              Focused regression tests
local_run.py        Local development entry point
schema.sql          New-project Supabase schema
vercel.json         Vercel routes and function configuration
```

## User setup

1. Visit [ytscrobbler.kuberbassi.com](https://ytscrobbler.kuberbassi.com) and sign in with Google.
2. Create a Last.fm API application using the instructions in **Setup Guide**.
3. Save and authorize the Last.fm account.
4. Paste YouTube Music browser request headers.
5. Enable **Auto Scrobble**.

The dashboard contains exact Last.fm callback/homepage values and step-by-step
instructions for collecting YouTube Music headers.

## Local development

```powershell
git clone https://github.com/kuberbassi/ytmusic-scrobbler.git
cd ytmusic-scrobbler
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python local_run.py
```

Open `http://localhost:3000`.

Copy `.env.example` to `.env` and supply the required values. Never commit `.env`,
`.env.local`, Supabase service-role keys, Last.fm secrets, session keys, or YouTube
browser headers.

## Production deployment

Required Vercel environment variables:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-server-only-service-role-key
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
GOOGLE_REDIRECT_URI=https://your-domain.com/auth/google/callback
SECRET_KEY=your-random-session-secret
CRON_SECRET=your-cron-secret
```

For an existing database, apply
`supabase/migrations/20260825_harden_sync_backend.sql` in Supabase SQL Editor.
For a new database, use `schema.sql`.

Configure cron-job.org to request `/api/cron` every five minutes with:

```text
Authorization: Bearer <CRON_SECRET>
```

After deployment, invoke cron once, confirm `last_sync_success_at` updates, and
verify that Supabase anon/authenticated roles cannot read `users` or `scrobbles`.

## Validation

```powershell
python -m unittest discover -s tests -v
python -m py_compile api/index.py api/database.py
node --check static/js/app.js
git diff --check
```

## License

[MIT](LICENSE)
