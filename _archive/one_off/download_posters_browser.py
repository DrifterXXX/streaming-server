#!/usr/bin/env python3
"""Download missing posters using Playwright - navigate directly to CDN URLs."""
import json
import os
import time

from playwright.sync_api import sync_playwright

DB_FILE = os.path.expanduser("~/streaming-server/database.json")
POSTER_DIR = os.path.expanduser("~/streaming-server/posters")
os.makedirs(POSTER_DIR, exist_ok=True)

with open(DB_FILE) as f:
    db = json.load(f)

movies = db.get("movies", [])

# Find missing posters
to_download = []
for m in movies:
    pid = m["id"]
    local = os.path.join(POSTER_DIR, f"{pid}.jpg")
    if not os.path.exists(local) or os.path.getsize(local) < 1000:
        to_download.append(m)

print(f"Need {len(to_download)} posters")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="zh-CN",
        extra_http_headers={"Referer": "https://movie.douban.com/"}
    )

    ok = 0
    fail = 0
    
    for m in to_download:
        pid = m["id"]
        title = m["title"]
        poster_url = m.get("poster", "")
        
        if not poster_url:
            fail += 1
            continue
        
        local_path = os.path.join(POSTER_DIR, f"{pid}.jpg")
        print(f"  {title}: ", end="", flush=True)
        
        try:
            page = context.new_page()
            resp = page.goto(poster_url, timeout=20000, wait_until="load")
            
            if resp and resp.status == 200:
                img_data = resp.body()
                if len(img_data) > 1000:
                    with open(local_path, "wb") as f:
                        f.write(img_data)
                    kb = len(img_data) / 1024
                    print(f"OK ({kb:.0f} KB)")
                    ok += 1
                else:
                    print(f"SMALL ({len(img_data)} bytes)")
                    fail += 1
            else:
                status = resp.status if resp else "None"
                print(f"HTTP {status}")
                fail += 1
            
            page.close()
            time.sleep(0.5)
        except Exception as e:
            print(f"ERROR: {e}")
            fail += 1
    
    browser.close()

print(f"\nResult: {ok} OK, {fail} FAIL")