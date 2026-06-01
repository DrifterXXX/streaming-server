#!/usr/bin/env python3
"""Try various approaches to download a single Douban poster."""
import urllib.request
import urllib.parse

POSTER_URL = "https://img1f1f4a450a2a5bca4ea3f93f2c95051e"

# Actually let's just try different CDN paths  
POSTER_URL = "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p2887045470.jpg"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://movie.douban.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

def try_url(url, label):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            print(f"{label}: HTTP {resp.status}, {len(data)} bytes")
            return data
    except Exception as e:
        print(f"{label}: {e}")
        return None

# Try different CDN hosts
for cdn in ["img1", "img2", "img3", "img9"]:
    u = POSTER_URL.replace("img1", cdn)
    try_url(u, f"CDN {cdn}")

# Try different sizes
for size in ["s_ratio_poster", "m", "l", "raw"]:
    u = POSTER_URL.replace("s_ratio_poster", size)
    try_url(u, f"Size {size}")

# Try corsproxy
proxy_url = "https://corsproxy.io/?" + urllib.parse.urlencode({"url": POSTER_URL})
try_url(proxy_url, "corsproxy")

# Try thingproxy
proxy2 = "https://thingproxy.freeboard.io/fetch/" + POSTER_URL
try_url(proxy2, "thingproxy")