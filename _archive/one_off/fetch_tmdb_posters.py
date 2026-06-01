#!/usr/bin/env python3
"""Fetch real poster images from TMDB for all movies/TV shows in the database."""
import json
import os
import re
import urllib.request
import urllib.parse
import time

DB_FILE = os.path.expanduser("~/streaming-server/database.json")
POSTER_DIR = os.path.expanduser("~/streaming-server/posters")
os.makedirs(POSTER_DIR, exist_ok=True)

# English search terms for each title (no years - TMDB doesn't like them)
SEARCH_QUERIES = {
    "流浪地球2": "The Wandering Earth 2",
    "长安三万里": "Chang An",
    "奥本海默": "Oppenheimer",
    "封神第一部：朝歌风云": "Creation of the Gods I",
    "芭比": "Barbie",
    "满江红": "Full River Red",
    "消失的她": "Lost in the Stars",
    "八角笼中": "Never Say Never",
    "年会不能停！": "Johnny Keep Walking",
    "飞驰人生2": "Pegasus 2",
    "热辣滚烫": "Yolo",
    "刺杀小说家": "A Writer's Odyssey",
    "扬名立万": "Be Somebody",
    "孤注一掷": "No More Bets",
    "涉过愤怒的海": "Across the Furious Sea",
    "漫长的季节": "The Long Season",
    "繁花": "Blossoms Shanghai",
    "三体": "Three Body",
    "狂飙": "The Knockout",
    "去有风的地方": "Meet Yourself",
    "山海情": "Minning Town",
    "沉默的真相": "The Long Night",
    "隐秘的角落": "The Bad Kids",
    "觉醒年代": "The Age of Awakening",
    "大江大河2": "Like a Flowing River 2",
    "大山的女儿": "Daughters of the Mountain",
    "人世间": "A Lifelong Journey",
    "警察荣誉": "Ordinary Greatness",
    "风犬少年的天空": "Yongers",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def search_and_get_poster(search_query, expected_title):
    """Search TMDB, find matching result, return original-size poster URL."""
    # Try the clean query first
    url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(search_query)}"
    req = urllib.request.Request(url, headers=HEADERS)
    
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  SEARCH FAILED: {e}")
        return None
    
    # Split by data-object-id to get individual result blocks
    blocks = html.split("data-object-id=")
    
    # Strategy 1: try to match by title
    for block in blocks[1:]:
        title_match = re.search(r'alt="([^"]+)"', block)
        img_match = re.search(r'src="(https://media\.themoviedb\.org/t/p/[^"]+\.jpg)"', block)
        
        if not (title_match and img_match):
            continue
        
        result_title = title_match.group(1)
        img_url = img_match.group(1)
        
        # Check if titles match exactly
        if result_title == expected_title:
            orig_url = img_url.replace("/t/p/w94_and_h141_face/", "/t/p/original/").replace("media.themoviedb.org", "image.tmdb.org")
            print(f"  MATCH (exact): {result_title}")
            return orig_url
    
    # Strategy 2: use first result as best guess
    if blocks[1:]:
        block = blocks[1]
        title_match = re.search(r'alt="([^"]+)"', block)
        img_match = re.search(r'src="(https://media\.themoviedb\.org/t/p/[^"]+\.jpg)"', block)
        if title_match and img_match:
            result_title = title_match.group(1)
            img_url = img_match.group(1)
            orig_url = img_url.replace("/t/p/w94_and_h141_face/", "/t/p/original/").replace("media.themoviedb.org", "image.tmdb.org")
            print(f"  MATCH (first): {result_title}")
            return orig_url
    
    # Strategy 3: try searching by Chinese title directly
    url2 = f"https://www.themoviedb.org/search?query={urllib.parse.quote(expected_title)}"
    req2 = urllib.request.Request(url2, headers=HEADERS)
    try:
        with urllib.request.urlopen(req2, timeout=20) as resp2:
            html2 = resp2.read().decode("utf-8", errors="replace")
        blocks2 = html2.split("data-object-id=")
        if blocks2[1:]:
            block = blocks2[1]
            title_match = re.search(r'alt="([^"]+)"', block)
            img_match = re.search(r'src="(https://media\.themoviedb\.org/t/p/[^"]+\.jpg)"', block)
            if title_match and img_match:
                orig_url = img_match.group(1).replace("/t/p/w94_and_h141_face/", "/t/p/original/").replace("media.themoviedb.org", "image.tmdb.org")
                print(f"  MATCH (Chinese search): {title_match.group(1)}")
                return orig_url
    except Exception as e:
        print(f"  Chinese search failed: {e}")
    
    print(f"  NO MATCH for '{expected_title}'")
    return None


def download_poster(url, save_path):
    """Download a poster image. Returns True on success."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if len(data) > 10000:
            with open(save_path, "wb") as f:
                f.write(data)
            size_kb = len(data) / 1024
            print(f"  DOWNLOADED: {size_kb:.0f} KB")
            return True
        else:
            print(f"  TOO SMALL: {len(data)} bytes")
            return False
    except Exception as e:
        print(f"  DOWNLOAD ERROR: {e}")
        return False


# Load database
with open(DB_FILE) as f:
    db = json.load(f)

movies = db.get("movies", [])
print(f"Total entries: {len(movies)}")

success = 0
fail = 0
skipped = 0

for m in movies:
    poster_id = m.get("id", "unknown")
    title = m.get("title", "?")
    local_path = os.path.join(POSTER_DIR, f"{poster_id}.jpg")
    
    # Skip if already has a real poster (>50KB = real image, not placeholder)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 50000:
        print(f"  SKIP {title}: already has real poster ({os.path.getsize(local_path)//1024} KB)")
        skipped += 1
        continue
    
    search_term = SEARCH_QUERIES.get(title, title)
    print(f"\n  [{poster_id}] {title}")
    print(f"  Search: {search_term}")
    
    poster_url = search_and_get_poster(search_term, title)
    
    if poster_url:
        if download_poster(poster_url, local_path):
            success += 1
        else:
            fail += 1
    else:
        fail += 1
    
    time.sleep(1.5)

print(f"\n{'='*50}")
print(f"Results: {success} OK, {fail} FAIL, {skipped} SKIPPED")
print(f"{'='*50}")