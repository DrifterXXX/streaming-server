"""
流媒体数据库种子脚本 v2 — 通过 TMDB 搜索获取高分影视作品
豆瓣 API 已失效，改用 TMDB（The Movie Database）公开搜索页。
用法: python3 seed_database.py [--limit N]
"""
import sys
import json
import time
import re
import urllib.parse
import urllib.request
import hashlib
from pathlib import Path
from datetime import datetime

DB_DIR = Path.home() / "services/streaming-server"
DB_FILE = DB_DIR / "database.json"
POSTER_DIR = DB_DIR / "posters"
POSTER_DIR.mkdir(parents=True, exist_ok=True)

TMDB_SEARCH = "https://www.themoviedb.org/search"
TMDB_MOVIE = "https://www.themoviedb.org/movie"
TMDB_TV = "https://www.themoviedb.org/tv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 知名作品的已知评分（豆瓣 1-10 分制，0=未知，由脚本自动获取TMDB评分）
KNOWN_RATINGS = {
    "霸王别姬": 9.6, "肖申克的救赎": 9.7, "活着": 9.3, "鬼子来了": 9.3,
    "让子弹飞": 9.0, "我不是药神": 9.0, "功夫": 8.8, "无间道": 9.3,
    "大话西游": 9.2, "喜剧之王": 8.8,
    "泰坦尼克号": 9.4, "阿甘正传": 9.5, "盗梦空间": 9.3, "星际穿越": 9.4,
    "楚门的世界": 9.3,
    "千与千寻": 9.4, "龙猫": 9.2, "哈尔的移动城堡": 9.1, "天空之城": 9.1,
    "幽灵公主": 8.9,
    "教父": 9.3, "辛德勒的名单": 9.5, "这个杀手不太冷": 9.4, "海上钢琴师": 9.3,
    "美丽人生": 9.5,
    "指环王": 9.2, "哈利波特与魔法石": 9.2, "蝙蝠侠黑暗骑士": 9.2,
    "机器人总动员": 9.3, "飞屋环游记": 9.1,
    "疯狂动物城": 9.2, "寻梦环游记": 9.1, "狮子王": 9.1, "冰雪奇缘": 8.4,
    "超能陆战队": 8.7,
    "寄生虫": 8.9, "你的名字": 8.4, "疯狂麦克斯4：狂暴之路": 7.6,
    "流浪地球": 8.2, "哪吒之魔童降世": 8.5, "长安三万里": 8.4, "深海": 8.4,
    "白蛇缘起": 8.1, "琅琊榜(电影)": 9.4,
    "权力的游戏": 9.4, "绝命毒师": 9.3, "风骚律师": 9.2,
    "老友记": 9.3, "生活大爆炸": 9.2,
    "神探夏洛克": 9.1, "黑镜": 9.0, "西部世界": 8.5, "纸牌屋": 8.4, "毒枭": 9.0,
    "大明王朝1566": 9.8, "雍正王朝": 9.3, "甄嬛传": 9.1, "父母爱情": 9.4,
    "漫长的季节": 9.4, "繁花": 8.7, "狂飙": 8.5, "三体(剧版)": 8.7, "人民的名义": 8.7,
    "白夜追凶": 8.7, "隐秘的角落": 8.9, "沉默的真相": 8.9, "武林外传": 9.5,
    "我爱我家": 9.2,
    "信号": 9.1, "请回答1988": 9.2, "机智的医生生活": 9.0, "非自然死亡": 9.1,
    "半泽直树": 9.1,
    "鬼灭之刃": 8.7, "进击的巨人": 9.2, "钢之炼金术师": 9.2, "命运石之门": 9.0,
    "死亡笔记": 8.9,
}

SEED_TITLES = [
    # 电影
    ("霸王别姬", "电影"), ("肖申克的救赎", "电影"), ("活着", "电影"),
    ("鬼子来了", "电影"), ("让子弹飞", "电影"),
    ("我不是药神", "电影"), ("功夫", "电影"), ("无间道", "电影"),
    ("大话西游", "电影"), ("喜剧之王", "电影"),
    ("泰坦尼克号", "电影"), ("阿甘正传", "电影"), ("盗梦空间", "电影"),
    ("星际穿越", "电影"), ("楚门的世界", "电影"),
    ("千与千寻", "电影"), ("龙猫", "电影"), ("哈尔的移动城堡", "电影"),
    ("天空之城", "电影"), ("幽灵公主", "电影"),
    ("教父", "电影"), ("辛德勒的名单", "电影"), ("这个杀手不太冷", "电影"),
    ("海上钢琴师", "电影"), ("美丽人生", "电影"),
    ("指环王", "电影"), ("哈利波特与魔法石", "电影"),
    ("蝙蝠侠黑暗骑士", "电影"), ("机器人总动员", "电影"), ("飞屋环游记", "电影"),
    ("疯狂动物城", "电影"), ("寻梦环游记", "电影"), ("狮子王", "电影"),
    ("冰雪奇缘", "电影"), ("超能陆战队", "电影"),
    ("寄生虫", "电影"), ("你的名字", "电影"), ("疯狂麦克斯：狂暴之路", "电影"),
    ("流浪地球", "电影"), ("哪吒之魔童降世", "电影"), ("长安三万里", "电影"),
    ("深海", "电影"),
    ("权力的游戏", "电视剧"), ("绝命毒师", "电视剧"), ("风骚律师", "电视剧"),
    ("老友记", "电视剧"), ("生活大爆炸", "电视剧"),
    ("神探夏洛克", "电视剧"), ("黑镜", "电视剧"), ("西部世界", "电视剧"),
    ("纸牌屋", "电视剧"), ("毒枭", "电视剧"),
    ("大明王朝1566", "电视剧"), ("雍正王朝", "电视剧"),
    ("甄嬛传", "电视剧"), ("父母爱情", "电视剧"),
    ("漫长的季节", "电视剧"), ("繁花", "电视剧"), ("狂飙", "电视剧"),
    ("三体", "电视剧"), ("人民的名义", "电视剧"),
    ("白夜追凶", "电视剧"), ("隐秘的角落", "电视剧"), ("沉默的真相", "电视剧"),
    ("武林外传", "电视剧"), ("我爱我家", "电视剧"),
    ("信号", "电视剧"), ("请回答1988", "电视剧"), ("机智的医生生活", "电视剧"),
    ("非自然死亡", "电视剧"), ("半泽直树", "电视剧"),
    ("鬼灭之刃", "电视剧"), ("进击的巨人", "电视剧"),
    ("钢之炼金术师", "电视剧"), ("命运石之门", "电视剧"), ("死亡笔记", "电视剧"),
]


def title_already_exists(title):
    if not DB_FILE.exists():
        return False
    try:
        with open(DB_FILE) as f:
            db = json.load(f)
        title_lower = title.lower().strip()
        for m in db.get("movies", []):
            mt = m.get("title", "").lower().strip()
            if mt == title_lower:
                return True
        return False
    except:
        return False


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def get_tmdb_rating(tmdb_type, tmdb_id):
    """从 TMDB 详情页获取百分比评分"""
    try:
        base_url = TMDB_TV if tmdb_type == "tv" else TMDB_MOVIE
        html = fetch(f"{base_url}/{tmdb_id}")
        m = re.search(r'data-percent="(\d+)"', html)
        if m:
            pct = int(m.group(1))
            return round(pct / 10, 1)  # 75% → 7.5
    except:
        pass
    return 0.0


def parse_tmdb_search_cards(html):
    """解析 TMDB 搜索页的卡片"""
    cards = []
    for block in html.split('data-object-id="')[1:]:
        try:
            # 提取标题
            title_match = re.search(r'<span>([^<]+)</span>', block)
            if not title_match:
                continue
            title = title_match.group(1).strip()
            if not title or len(title) < 1:
                continue

            # 海报
            img_src = re.search(r'src="([^"]+w94_and_h141[^"]+)"', block)
            poster = ""
            if img_src:
                poster = img_src.group(1).replace(
                    "/t/p/w94_and_h141_face/", "/t/p/w500/"
                ).replace("media.themoviedb.org", "image.tmdb.org")

            # 年份
            year = ""
            year_match = re.search(r'(\d{4})\s*年', block)
            if year_match:
                year = year_match.group(1)

            # 类型 + TMDB ID
            media_type_match = re.search(r'data-media-type="([^"]+)"', block)
            media_type = media_type_match.group(1) if media_type_match else "movie"
            
            id_match = re.search(r'href="/[^"]+/(\d+)"', block)
            tmdb_id = id_match.group(1) if id_match else ""
            
            # 描述（剪裁到80字）
            desc_match = re.search(r'<p>([^<]+)', block)
            desc = desc_match.group(1).strip()[:80] if desc_match else ""

            cards.append({
                "title": title, "poster": poster, "year": year,
                "tmdb_id": tmdb_id, "tmdb_type": media_type,
                "description": desc,
            })
        except:
            continue
    return cards


def get_known_rating(title):
    """先查已知评分字典，0 = 未知"""
    return KNOWN_RATINGS.get(title, 0.0)


def main():
    limit = None
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
        elif arg == "--dry-run":
            print("试运行：展示将搜索的内容")
            for title, mtype in SEED_TITLES:
                skip = title_already_exists(title)
                print(f"  {'✅' if not skip else '⏭️'} [{mtype}] {title}")
            return

    total = 0
    added = 0
    skipped = 0
    failed = 0
    no_match = 0

    for q, media_type in SEED_TITLES:
        if limit and total >= limit:
            break
        total += 1

        if title_already_exists(q):
            print(f"[{total}/{len(SEED_TITLES)}] ⏭️  已存在: {q}")
            skipped += 1
            continue

        print(f"[{total}/{len(SEED_TITLES)}] 🔍 {q} ({media_type})...", end=" ")
        sys.stdout.flush()
        time.sleep(1.5)

        try:
            html = fetch(f"{TMDB_SEARCH}?query={urllib.parse.quote(q)}")
            cards = parse_tmdb_search_cards(html)
        except Exception as e:
            print(f"❌ 搜索失败: {e}")
            failed += 1
            continue

        if not cards:
            print("❌ 无结果")
            no_match += 1
            continue

        # 精确匹配优先
        best = None
        for c in cards:
            if c["title"].strip().lower() == q.strip().lower():
                best = c
                break
        # 其次模糊匹配：搜索词在标题中
        if not best:
            for c in cards:
                if q[0:2] in c["title"]:
                    best = c
                    break
        # 再次取第一个
        if not best:
            best = cards[0]

        title = best["title"]

        if title_already_exists(title):
            print(f"⏭️  \"{title}\" 已存在")
            skipped += 1
            continue

        # 获取评分
        rating = get_known_rating(q)
        if rating == 0.0 and best["tmdb_id"]:
            rating = get_tmdb_rating(best["tmdb_type"], best["tmdb_id"])

        # 生成媒体ID
        id_hash = hashlib.md5(title.encode()).hexdigest()[:8]
        media_id = f"ext_{id_hash}_{int(time.time())}"

        entry = {
            "id": media_id,
            "title": title,
            "year": best.get("year", ""),
            "rating": rating,
            "type": media_type,
            "genre": media_type,
            "poster": best.get("poster", ""),
            "douban_id": "",
            "douban_url": "",
            "download_urls": [],
            "seasons": [],
            "episodes": 0,
            "description": best.get("description", ""),
            "added_at": datetime.now().isoformat(),
        }

        # 保存到库
        if DB_FILE.exists():
            with open(DB_FILE) as f:
                db = json.load(f)
        else:
            db = {"movies": []}
        db.setdefault("movies", []).append(entry)
        with open(DB_FILE, "w") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

        # 下载海报
        if best["poster"]:
            poster_path = POSTER_DIR / f"{media_id}.jpg"
            if not poster_path.exists():
                try:
                    preq = urllib.request.Request(best["poster"], headers={
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": "https://www.themoviedb.org/",
                    })
                    with urllib.request.urlopen(preq, timeout=15) as pr:
                        with open(poster_path, "wb") as pf:
                            pf.write(pr.read())
                    print(f"✅ {title} ({rating}分) 🖼️ 海报OK")
                except:
                    print(f"✅ {title} ({rating}分) ⚠️ 海报下载失败")
            else:
                print(f"✅ {title} ({rating}分) 🖼️ 海报已存在")
        else:
            print(f"✅ {title} ({rating}分) ⚠️ 无海报")

        added += 1

    print(f"\n{'='*55}")
    print(f"📊 种子填充完成!")
    print(f"   搜索总数: {total}")
    print(f"   新增入库: {added}")
    print(f"   已存在跳过: {skipped}")
    print(f"   搜索无结果: {no_match}")
    print(f"   搜索失败: {failed}")
    print(f"   数据库: {DB_FILE}")
    print(f"{'='*55}")

    if DB_FILE.exists():
        with open(DB_FILE) as f:
            db = json.load(f)
        movies = [m for m in db.get("movies", []) if m.get("type") == "电影"]
        tv = [m for m in db.get("movies", []) if m.get("type") == "电视剧"]
        print(f"\n📊 数据库统计: 电影 {len(movies)} 部 / 电视剧 {len(tv)} 部")


if __name__ == "__main__":
    main()