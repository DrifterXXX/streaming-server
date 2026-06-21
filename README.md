# Streaming Server

A self-hosted video streaming media server with multi-cloud drive download engine. Watch your media collection from anywhere with a Netflix-style web UI.

## Features

- **Netflix-style Web UI** — beautiful poster view with metadata display
- **HTTP Range Support** — seekable video playback with draggable progress bar
- **Multi-Cloud Drive Download** — Alibaba Cloud Drive, Quark Pan, Baidu Netdisk, Tianyi Cloud, Xunlei Cloud
- **External Search Integration** — Douban and TMDB metadata scraping
- **Background Download Queue** — concurrent downloads with progress tracking
- **Subscription Management** — episode tracking and season management
- **Mobile Friendly** — responsive design with landscape playback support

## Architecture

```
streaming-server
├── stream_server_v3.py      # Main HTTP server with range request support
├── stream_server_v3_dl.py   # Download enhancement module
├── cloud_download.py         # Multi-cloud drive download engine
├── cloud_disk_dl.py          # Cloud disk download implementations
├── pan_api.py                # Cloud storage API adapters
├── router.py                 # URL routing and request handling
├── database.py               # SQLite database for media metadata
├── search_service.py         # Douban/TMDB external search
├── space_manager.py          # Storage space management
├── add_seasons.py            # Season/episode management
└── templates/                # HTML templates
```

## Quick Start

### Prerequisites
- Python 3.9+
- macOS or Linux

### Installation

```bash
# Clone the repository
git clone https://github.com/DrfterX/streaming-server.git
cd streaming-server

# Create videos directory
mkdir -p videos

# Start the server
python3 stream_server_v3.py
```

### Configuration

Create a `.env` file (optional, for cloud drive features):

```bash
# Cloud drive credentials are stored in data/pan_creds.json
# This file is automatically gitignored for security
```

### Cloud Drive Setup

1. Run `python3 pan_login.py` to authenticate with supported cloud drives
2. Credentials are stored locally in `data/pan_creds.json` (never committed to git)
3. Use `python3 cloud_download.py` to download media from cloud drives

### Video Management

- Place video files in `videos/` directory
- Supported formats: MP4, MKV, AVI, MOV, WEBM
- Naming convention: `ShowName.ep01.mp4`, `ShowName.ep02.mp4`

### Public Access (Optional)

Use Cloudflare Tunnel to expose your server:

```bash
cloudflared tunnel --config ~/.cloudflared/config.yml run
```

## Supported Cloud Drives

| Provider | Status | Features |
|----------|--------|----------|
| Alibaba Cloud Drive | ✅ | JWT auth, file listing, download |
| Quark Pan | ✅ | Cookie auth, welfare explore |
| Baidu Netdisk | ✅ | BDUSS auth, file listing |
| Tianyi Cloud | ✅ | Cookie auth, download |
| Xunlei Cloud | ✅ | Session auth, file listing |

## Security

- All cloud drive credentials are stored locally and never pushed to git
- `data/`, `pan_creds.json`, `.env` are excluded via `.gitignore`
- Review `pan_creds.json` before sharing your server directory

## License

MIT
