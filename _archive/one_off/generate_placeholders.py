#!/usr/bin/env python3
"""Generate placeholder poster images for missing posters."""
import json, os
from PIL import Image, ImageDraw, ImageFont

DB_FILE = os.path.expanduser("~/streaming-server/database.json")
POSTER_DIR = os.path.expanduser("~/streaming-server/posters")

# Clean 0-byte files
for f in os.listdir(POSTER_DIR):
    fp = os.path.join(POSTER_DIR, f)
    if os.path.isfile(fp) and os.path.getsize(fp) < 100:
        os.remove(fp)
        print("Cleaned:", f)

# Load database
with open(DB_FILE) as f:
    db = json.load(f)
movies = db.get("movies", [])

# Find missing
missing = []
for m in movies:
    pid = m["id"]
    local = os.path.join(POSTER_DIR, pid + ".jpg")
    if not os.path.exists(local) or os.path.getsize(local) < 1000:
        missing.append(m)
print("Need", len(missing), "placeholders")

# Font
font_large = font_small = ImageFont.load_default()
for fp in ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc"]:
    if os.path.exists(fp):
        font_large = ImageFont.truetype(fp, 28)
        font_small = ImageFont.truetype(fp, 18)
        print("Font:", fp)
        break

# Colors by type
dark_bg = {
    "电影": [(20, 30, 60), (60, 20, 40)],
    "电视剧": [(20, 50, 30), (40, 20, 50)],
}

for m in missing:
    pid = m["id"]
    title = m["title"]
    mtype = m.get("type", "电影")
    rating = m.get("rating", 7.0)
    local_path = os.path.join(POSTER_DIR, pid + ".jpg")

    # Create gradient image
    img = Image.new("RGB", (400, 600))
    draw = ImageDraw.Draw(img)
    c = dark_bg.get(mtype, dark_bg["电影"])
    for y in range(600):
        r = int(c[0][0] + (c[1][0] - c[0][0]) * y / 600)
        g = int(c[0][1] + (c[1][1] - c[0][1]) * y / 600)
        b = int(c[0][2] + (c[1][2] - c[0][2]) * y / 600)
        draw.line([(0, y), (400, y)], fill=(r, g, b))

    # Rating circle
    bc = (0, 210, 106) if rating >= 8 else (255, 193, 7)
    draw.ellipse([150, 130, 250, 230], outline=bc, width=3)
    draw.text((200, 180), "{:.1f}".format(rating), fill=bc, font=font_large, anchor="mm")

    # Title wrapping
    lines = []
    cur = ""
    for ch in title:
        test = cur + ch
        tw = draw.textbbox((0, 0), test, font=font_large)[2]
        if tw > 350 and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    lines.append(cur)

    y0 = 280
    for i, line in enumerate(lines):
        ft = font_large if len(lines) <= 2 else font_small
        tw = draw.textbbox((0, 0), line, font=ft)[2]
        draw.text(((400 - tw) / 2, y0 + i * 38), line, fill=(255, 255, 255), font=ft)

    # Type/year label
    lbl = mtype if not m.get("year") else mtype + " " + m["year"]
    tw = draw.textbbox((0, 0), lbl, font=font_small)[2]
    draw.text(((400 - tw) / 2, 380), lbl, fill=(180, 180, 200), font=font_small)

    # Download label
    lbl2 = "点击下载"
    tw = draw.textbbox((0, 0), lbl2, font=font_small)[2]
    draw.text(((400 - tw) / 2, 500), lbl2, fill=(233, 69, 96), font=font_small)

    img.save(local_path, "JPEG", quality=85)
    print("  Created:", title)

count = sum(1 for f in os.listdir(POSTER_DIR) if os.path.isfile(os.path.join(POSTER_DIR, f)) and os.path.getsize(os.path.join(POSTER_DIR, f)) > 1000)
print("\nTotal poster files:", count)