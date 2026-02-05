# YT Music Scrobbler 🎵

Automatically scrobble your YouTube Music listening history to Last.fm. This scrobbler works for **all devices** (Phone, PC, TV, Nest) by reading your central YouTube Watch History.

### 🌟 Features
- **Header-Based Sync:** Connects using industry-standard browser headers for maximum reliability.
- **Background Sync:** Once enabled, the server scrobbles in the background even if the browser tab is closed.
- **Smart Repeat Detection:** Uses track duration to correctly scrobble songs played on repeat (overcoming YouTube Music's history limitations).
- **Auto-Refreshing UI:** The dashboard updates your history in real-time as background scrobbles complete.
- **Persistent Tracking:** Never scrobbles the same track twice, even after a server restart.
- **No Complex API Setup:** No need for Google Cloud Console OAuth setup.
- **Light/Dark Mode:** Dynamic, premium interface.

## 🚀 Getting Started

### 1. Requirements
- Python 3.8+
- Last.fm API Key (Get it from [last.fm/api](https://www.last.fm/api/account/create))

### 2. Installation
```bash
git clone https://github.com/kuberbassi/ytmusic-scrobbler.git
cd ytmusic-scrobbler
pip install -r requirements.txt
```

### 3. Run
```bash
python local_run.py
```
Open `http://localhost:5000` in your browser.

### 4. Setup
1.  **Last.fm:** Enter your API Key/Secret and click "Authorize".
2.  **YT Music:** Paste your browser headers from the Network tab (see [instructions](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html)).
3.  **Go!** Enable "Auto Scrobble" and close the tab. The scrobbler will keep working in the background.

## 📱 Mobile Support
This scrobbler reads your **Global YouTube Watch History**. As long as your phone is logged into the same YouTube account and "Watch History" is enabled, your mobile listening will be scrobbled automatically!

## 📄 License
MIT License.
