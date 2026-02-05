#!/usr/bin/env python
"""
Local development server for YT Music Scrobbler
Run with: python local_run.py
"""

from dotenv import load_dotenv
load_dotenv()

from api.index import app

if __name__ == '__main__':
    print("🎵 YT Music Scrobbler")
    print("=" * 40)
    print("Open http://localhost:3000 in your browser")
    print("=" * 40)
    # use_reloader=False prevents duplicate background threads in debug mode
    app.run(host='0.0.0.0', port=3000, debug=True, use_reloader=False)
