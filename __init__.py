"""
Hermes Streaming Server v3
==========================
Structure:
  server.py              → Entry point (adds /health)
  stream_server_v3.py    → Main HTTP handler + routing + DB management
  stream_server_v3_dl.py → Download enhancement module
  templates/index.html   → Netflix-style HTML template (109KB)
  cloud_disk_dl.py       → Cloud disk download engines
  pan_api.py             → Cloud disk API integration
  pan_login.py           → Cloud disk login handlers
  data/                  → Downloaded queue/cache files
  videos/                → Downloaded media files
"""