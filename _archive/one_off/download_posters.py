#!/usr/bin/env python3
"""Download all poster images from Douban CDN to local posters/ directory."""
import json
import os
import urllib.request
import urllib.error
import time
import sys

DB_FILE = os.path.expanduser("~/streaming-server/database.json")
POSTER_DIR = os.path.expanduser("~/streaming-server/posters")
os.makedirs(POSTER_DIR, exist_ok=True)

# Load database
with open(DB_FILE) as f:
    db = json.load(f)

movies = db.get("movies", [])
print(f"Total entries: {len(movies)}")

success = 0
fail = 0

# Try alternative: use images.weserv.nl as a proxy
PROXY_ENABLED = True

for m in movies:
    poster_url = m.get("poster", "")
    poster_id = m.get("id", "unknown")
    title = m.get("title", "?")
    
    if not poster_url:
        print(f"  SKIP {title}: no poster URL")
        continue
    
    # Determine local filename
    ext = os.makedirs(POSTER_DIR, exist_ok=True)
    # Determine file extension from URL
    if ".jpg" in poster_url or ".jpeg" in poster_url:
        ext = ".jpg"
    elif ".png" in poster_url:
        ext = ".png"
    else:
        ext = ".jpg"
    
    local_path = os.path.join(POSTER_DIR, f"{poster_id}{ext}")
    
    # Skip if already exists and has content
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        size_kb = os.path.getsize(local_path) / 1024
        print(f"  OK {title}: already exists ({size_kb:.0f} KB)")
        success += 1
        continue
    
    # Strategy 1: Direct with Referer
    try:
        req = urllib.request.Request(poster_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://movie.douban.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if len(data) > 1000:
            with open(local_path, "wb") as f:
                f.write(data)
            size_kb = len(data) / 1024
            print(f"  OK {title}: {size_kb:.0f} KB (direct)")
            success += 1
            time.sleep(0.5)
            continue
    except Exception as e:
        pass  # Fall through to proxy
    
    # Strategy 2: Use images.weserv.nl proxy
    if PROXY_ENABLED:
        try:
            proxy_url = f"https://images.weserv.nl/?url={poster_url.replace('https://', '').replace('http://', '')}"
            req = urllib.request.Request(proxy_url, headers={
                "User-Agent": "Mozilla/5.0",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) > 1000:
                with open(local_path, "wb") as f:
                    f.write(data)
                size_kb = len(data) / 1024
                print(f"  OK {title}: {size_kb:.0f} KB (weserv proxy)")
                success += 1
                time.sleep(0.3)
                continue
        except Exception as e:
            print(f"  FAIL {title}: weserv proxy: {e}")
    
    # Strategy 3: Try without s_ratio_poster (use raw poster)
    try:
        raw_url = poster_url.replace("/s_ratio_poster/", "/raw/")
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://movie.douban.com/",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        if len(data) > 1000:
            with open(local_path, "wb") as f:
                f.write(data)
            size_kb = len(data) / 1024
            print(f"  OK {title}: {size_kb:.0f} KB (raw)")
            success += 1
            time.sleep(0.5)
            continue
    except Exception as e:
        pass
    
    print(f"  FAIL {title}: all strategies exhausted")
    fail += 1
    time.sleep(0.3)

print(f"\nDone: {success} OK, {fail} FAIL")