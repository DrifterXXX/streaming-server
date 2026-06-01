#!/usr/bin/env python3
"""Fetch TMDB posters for the 71 new international entries."""
import json, os, re, urllib.request, urllib.parse, time

DB_FILE = os.path.expanduser("~/streaming-server/database.json")
POSTER_DIR = os.path.expanduser("~/streaming-server/posters")
os.makedirs(POSTER_DIR, exist_ok=True)

SEARCH_QUERIES = {
    "肖申克的救赎": "The Shawshank Redemption",
    "教父": "The Godfather",
    "教父2": "The Godfather Part II",
    "蝙蝠侠：黑暗骑士": "The Dark Knight",
    "辛德勒的名单": "Schindler's List",
    "泰坦尼克号": "Titanic",
    "阿甘正传": "Forrest Gump",
    "盗梦空间": "Inception",
    "星际穿越": "Interstellar",
    "楚门的世界": "The Truman Show",
    "这个杀手不太冷": "Leon The Professional",
    "美丽人生": "Life is Beautiful",
    "海上钢琴师": "The Legend of 1900",
    "千与千寻": "Spirited Away",
    "疯狂动物城": "Zootopia",
    "机器人总动员": "WALL-E",
    "寻梦环游记": "Coco",
    "飞屋环游记": "Up",
    "龙猫": "My Neighbor Totoro",
    "哈尔的移动城堡": "Howl Moving Castle",
    "怦然心动": "Flipped",
    "搏击俱乐部": "Fight Club",
    "指环王：护戒使者": "Lord of the Rings Fellowship",
    "指环王：双塔奇兵": "Lord of the Rings Two Towers",
    "指环王：王者无敌": "Lord of the Rings Return King",
    "当幸福来敲门": "Pursuit of Happyness",
    "放牛班的春天": "Les Choristes",
    "天堂电影院": "Cinema Paradiso",
    "致命魔术": "The Prestige",
    "黑客帝国": "The Matrix",
    "终结者2：审判日": "Terminator 2 Judgment Day",
    "侏罗纪公园": "Jurassic Park",
    "阿凡达": "Avatar",
    "沙丘": "Dune",
    "银翼杀手2049": "Blade Runner 2049",
    "疯狂的麦克斯4：狂暴之路": "Mad Max Fury Road",
    "三傻大闹宝莱坞": "3 Idiots",
    "摔跤吧！爸爸": "Dangal",
    "飞越疯人院": "One Flew Over Cuckoo Nest",
    "控方证人": "Witness for the Prosecution",
    "十二怒汉": "12 Angry Men",
    "头脑特工队": "Inside Out",
    "怪兽电力公司": "Monsters Inc",
    "玩具总动员": "Toy Story",
    "冰雪奇缘": "Frozen",
    "狮子王": "The Lion King",
    "幽灵公主": "Princess Mononoke",
    "你的名字。": "Your Name",
    "蝴蝶效应": "The Butterfly Effect",
    "致命ID": "Identity",
    "权力的游戏": "Game of Thrones",
    "绝命毒师": "Breaking Bad",
    "老友记": "Friends",
    "生活大爆炸": "Big Bang Theory",
    "西部世界": "Westworld",
    "黑镜": "Black Mirror",
    "神探夏洛克": "Sherlock",
    "纸牌屋": "House of Cards",
    "越狱": "Prison Break",
    "我们这一天": "This Is Us",
    "怪奇物语": "Stranger Things",
    "王冠": "The Crown",
    "致命女人": "Why Women Kill",
    "后翼弃兵": "Queen Gambit",
    "切尔诺贝利": "Chernobyl",
    "风骚律师": "Better Call Saul",
    "毒枭": "Narcos",
    "继承之战": "Succession",
    "暗黑": "Dark",
    "最后生还者": "The Last of Us",
    "瑞克和莫蒂": "Rick and Morty",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

def search_and_get_poster(search_query, expected_title):
    url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(search_query)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  SEARCH FAILED: {e}")
        return None

    blocks = html.split("data-object-id=")

    # Strategy 1: exact title match
    for block in blocks[1:]:
        title_match = re.search(r'alt="([^"]+)"', block)
        img_match = re.search(r'src="(https://media\.themoviedb\.org/t/p/[^"]+\.(?:jpg|png|webp))"', block)
        if not (title_match and img_match):
            continue
        result_title = title_match.group(1)
        img_url = img_match.group(1)
        if result_title.lower() == expected_title.lower():
            orig_url = img_url.replace("/t/p/w94_and_h141_face/", "/t/p/original/").replace("media.themoviedb.org", "image.tmdb.org")
            # Also handle other sizes
            if "w94_and_h141_face" not in img_url and "original" not in img_url:
                orig_url = re.sub(r'/t/p/[^/]+/', '/t/p/original/', img_url)
            orig_url = orig_url.replace("media.themoviedb.org", "image.tmdb.org")
            print(f"  MATCH (exact): {result_title}")
            return orig_url

    # Strategy 2: first result
    if blocks[1:]:
        block = blocks[1]
        title_match = re.search(r'alt="([^"]+)"', block)
        img_match = re.search(r'src="(https://media\.themoviedb\.org/t/p/[^"]+\.(?:jpg|png|webp))"', block)
        if title_match and img_match:
            result_title = title_match.group(1)
            img_url = img_match.group(1)
            if "w94_and_h141_face" in img_url:
                orig_url = img_url.replace("/t/p/w94_and_h141_face/", "/t/p/original/")
            else:
                orig_url = re.sub(r'/t/p/[^/]+/', '/t/p/original/', img_url)
            orig_url = orig_url.replace("media.themoviedb.org", "image.tmdb.org")
            print(f"  MATCH (first): {result_title}")
            return orig_url

    print(f"  NO MATCH")
    return None

def download_poster(url, save_path):
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

# Only new entries (id >= movie_016 or tv_015)
new_entries = [m for m in db["movies"] if m["id"] >= "movie_016" or m["id"] >= "tv_015"]
# Actually, let's just process all entries with empty poster
new_entries = [m for m in db["movies"] if not m.get("poster") or m["poster"] == ""]
print(f"Entries needing posters: {len(new_entries)}")

success = 0
fail = 0
skipped = 0

for m in new_entries:
    poster_id = m["id"]
    title = m["title"]
    local_path = os.path.join(POSTER_DIR, f"{poster_id}.jpg")

    if os.path.exists(local_path) and os.path.getsize(local_path) > 50000:
        print(f"  SKIP {title}: already has poster")
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