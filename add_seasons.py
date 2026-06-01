#!/usr/bin/env python3
"""Add season/episode structure to all TV shows in database."""
import json, os

DB_FILE = os.path.expanduser("~/streaming-server/database.json")

with open(DB_FILE) as f:
    db = json.load(f)

# Season structures for each TV show
# Format: [episode_count_per_season, ...]
TV_SEASONS = {
    # Original Chinese shows
    "漫长的季节": [12],
    "繁花": [30],
    "三体": [30],
    "狂飙": [39],
    "去有风的地方": [40],
    "山海情": [23],
    "沉默的真相": [12],
    "隐秘的角落": [12],
    "觉醒年代": [43],
    "大江大河2": [39],
    "大山的女儿": [30],
    "人世间": [58],
    "警察荣誉": [38],
    "风犬少年的天空": [16],

    # International shows
    "绝命毒师": [7, 13, 13, 13, 16],
    "权力的游戏": [10, 10, 10, 10, 10, 10, 7, 6],
    "老友记": [24, 24, 25, 24, 24, 25, 24, 24, 24, 18],
    "生活大爆炸": [17, 23, 23, 24, 24, 24, 24, 24, 24, 24, 24, 24],
    "西部世界": [10, 10, 8, 8],
    "黑镜": [3, 3, 3, 6, 3, 5],
    "神探夏洛克": [3, 3, 3, 3],
    "纸牌屋": [13, 13, 13, 13, 13, 8],
    "越狱": [22, 22, 13, 24, 9],
    "我们这一天": [18, 18, 18, 18, 16, 18],
    "怪奇物语": [8, 9, 8, 9],
    "王冠": [10, 10, 10, 10, 10, 10],
    "致命女人": [10, 10],
    "后翼弃兵": [7],
    "切尔诺贝利": [5],
    "风骚律师": [10, 10, 10, 10, 10, 13],
    "毒枭": [10, 10, 10],
    "继承之战": [10, 10, 10, 10],
    "暗黑": [10, 8, 8],
    "最后生还者": [9],
    "瑞克和莫蒂": [11, 10, 10, 10, 10, 10, 10],
}

updated = 0
for m in db["movies"]:
    if m["type"] == "电视剧" and m["title"] in TV_SEASONS:
        season_counts = TV_SEASONS[m["title"]]
        # Build episodes list
        episode_list = []
        for si, ep_count in enumerate(season_counts, 1):
            episode_list.append({
                "season": si,
                "episode_count": ep_count
            })
        
        m["seasons"] = episode_list
        # Keep total episodes count for backward compat
        m["episodes"] = sum(season_counts)
        updated += 1
        print(f"  {m['title']}: {len(season_counts)}季 {m['episodes']}集")
    elif m["type"] == "电视剧":
        print(f"  WARNING: No season data for {m['title']}")
        # Default to 1 season
        m["seasons"] = [{"season": 1, "episode_count": m.get("episodes", 12)}]

# Verify all TV shows have seasons
tv_count = sum(1 for m in db["movies"] if m["type"] == "电视剧")
print(f"\nTV shows: {tv_count}, Updated: {updated}")

with open(DB_FILE, "w") as f:
    json.dump(db, f, ensure_ascii=False, indent=2)

print("Done!")