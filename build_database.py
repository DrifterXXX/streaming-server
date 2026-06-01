#!/usr/bin/env python3
"""Build the full database with international movies and TV shows."""
import json, os

DB_FILE = os.path.expanduser("~/streaming-server/database.json")

# Existing entries
existing = json.load(open(DB_FILE))
existing_movies = existing["movies"]

# ---- INTERNATIONAL MOVIES (50, all Douban 7+) ----
new_movies = [
    {"id": "movie_016", "title": "肖申克的救赎", "year": "1994", "rating": 9.7, "type": "电影", "genre": "剧情/犯罪", "poster": "", "douban_url": "https://movie.douban.com/subject/1292052/", "download_urls": []},
    {"id": "movie_017", "title": "教父", "year": "1972", "rating": 9.3, "type": "电影", "genre": "剧情/犯罪", "poster": "", "douban_url": "https://movie.douban.com/subject/1291841/", "download_urls": []},
    {"id": "movie_018", "title": "教父2", "year": "1974", "rating": 9.2, "type": "电影", "genre": "剧情/犯罪", "poster": "", "douban_url": "https://movie.douban.com/subject/1299131/", "download_urls": []},
    {"id": "movie_019", "title": "蝙蝠侠：黑暗骑士", "year": "2008", "rating": 9.2, "type": "电影", "genre": "动作/科幻/犯罪", "poster": "", "douban_url": "https://movie.douban.com/subject/1851857/", "download_urls": []},
    {"id": "movie_020", "title": "辛德勒的名单", "year": "1993", "rating": 9.5, "type": "电影", "genre": "剧情/历史/战争", "poster": "", "douban_url": "https://movie.douban.com/subject/1295124/", "download_urls": []},
    {"id": "movie_021", "title": "泰坦尼克号", "year": "1997", "rating": 9.4, "type": "电影", "genre": "剧情/爱情/灾难", "poster": "", "douban_url": "https://movie.douban.com/subject/1292722/", "download_urls": []},
    {"id": "movie_022", "title": "阿甘正传", "year": "1994", "rating": 9.5, "type": "电影", "genre": "剧情/爱情", "poster": "", "douban_url": "https://movie.douban.com/subject/1102674/", "download_urls": []},
    {"id": "movie_023", "title": "盗梦空间", "year": "2010", "rating": 9.3, "type": "电影", "genre": "剧情/科幻/悬疑", "poster": "", "douban_url": "https://movie.douban.com/subject/3541415/", "download_urls": []},
    {"id": "movie_024", "title": "星际穿越", "year": "2014", "rating": 9.4, "type": "电影", "genre": "剧情/科幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/1889243/", "download_urls": []},
    {"id": "movie_025", "title": "楚门的世界", "year": "1998", "rating": 9.3, "type": "电影", "genre": "剧情/科幻/喜剧", "poster": "", "douban_url": "https://movie.douban.com/subject/1292064/", "download_urls": []},
    {"id": "movie_026", "title": "这个杀手不太冷", "year": "1994", "rating": 9.4, "type": "电影", "genre": "剧情/动作/犯罪", "poster": "", "douban_url": "https://movie.douban.com/subject/1295644/", "download_urls": []},
    {"id": "movie_027", "title": "美丽人生", "year": "1997", "rating": 9.5, "type": "电影", "genre": "剧情/喜剧/战争", "poster": "", "douban_url": "https://movie.douban.com/subject/1292063/", "download_urls": []},
    {"id": "movie_028", "title": "海上钢琴师", "year": "1998", "rating": 9.3, "type": "电影", "genre": "剧情/音乐", "poster": "", "douban_url": "https://movie.douban.com/subject/6874358/", "download_urls": []},
    {"id": "movie_029", "title": "千与千寻", "year": "2001", "rating": 9.4, "type": "电影", "genre": "动画/奇幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/1291561/", "download_urls": []},
    {"id": "movie_030", "title": "疯狂动物城", "year": "2016", "rating": 9.2, "type": "电影", "genre": "动画/冒险/喜剧", "poster": "", "douban_url": "https://movie.douban.com/subject/25662329/", "download_urls": []},
    {"id": "movie_031", "title": "机器人总动员", "year": "2008", "rating": 9.3, "type": "电影", "genre": "动画/科幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/2131459/", "download_urls": []},
    {"id": "movie_032", "title": "寻梦环游记", "year": "2017", "rating": 9.1, "type": "电影", "genre": "动画/奇幻/音乐", "poster": "", "douban_url": "https://movie.douban.com/subject/20495023/", "download_urls": []},
    {"id": "movie_033", "title": "飞屋环游记", "year": "2009", "rating": 9.1, "type": "电影", "genre": "动画/冒险/喜剧", "poster": "", "douban_url": "https://movie.douban.com/subject/2129039/", "download_urls": []},
    {"id": "movie_034", "title": "龙猫", "year": "1988", "rating": 9.2, "type": "电影", "genre": "动画/奇幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/1291560/", "download_urls": []},
    {"id": "movie_035", "title": "哈尔的移动城堡", "year": "2004", "rating": 9.1, "type": "电影", "genre": "动画/奇幻/爱情", "poster": "", "douban_url": "https://movie.douban.com/subject/1308807/", "download_urls": []},
    {"id": "movie_036", "title": "怦然心动", "year": "2010", "rating": 9.1, "type": "电影", "genre": "剧情/喜剧/爱情", "poster": "", "douban_url": "https://movie.douban.com/subject/3319755/", "download_urls": []},
    {"id": "movie_037", "title": "搏击俱乐部", "year": "1999", "rating": 9.0, "type": "电影", "genre": "剧情/悬疑/惊悚", "poster": "", "douban_url": "https://movie.douban.com/subject/1292000/", "download_urls": []},
    {"id": "movie_038", "title": "指环王：护戒使者", "year": "2001", "rating": 9.1, "type": "电影", "genre": "奇幻/冒险/动作", "poster": "", "douban_url": "https://movie.douban.com/subject/1291568/", "download_urls": []},
    {"id": "movie_039", "title": "指环王：双塔奇兵", "year": "2002", "rating": 9.0, "type": "电影", "genre": "奇幻/冒险/动作", "poster": "", "douban_url": "https://movie.douban.com/subject/1291571/", "download_urls": []},
    {"id": "movie_040", "title": "指环王：王者无敌", "year": "2003", "rating": 9.2, "type": "电影", "genre": "奇幻/冒险/动作", "poster": "", "douban_url": "https://movie.douban.com/subject/1291552/", "download_urls": []},
    {"id": "movie_041", "title": "当幸福来敲门", "year": "2006", "rating": 9.2, "type": "电影", "genre": "剧情/传记", "poster": "", "douban_url": "https://movie.douban.com/subject/1849031/", "download_urls": []},
    {"id": "movie_042", "title": "放牛班的春天", "year": "2004", "rating": 9.3, "type": "电影", "genre": "剧情/音乐", "poster": "", "douban_url": "https://movie.douban.com/subject/1291549/", "download_urls": []},
    {"id": "movie_043", "title": "天堂电影院", "year": "1988", "rating": 9.2, "type": "电影", "genre": "剧情/爱情", "poster": "", "douban_url": "https://movie.douban.com/subject/1291828/", "download_urls": []},
    {"id": "movie_044", "title": "致命魔术", "year": "2006", "rating": 8.9, "type": "电影", "genre": "剧情/悬疑/科幻", "poster": "", "douban_url": "https://movie.douban.com/subject/1780330/", "download_urls": []},
    {"id": "movie_045", "title": "黑客帝国", "year": "1999", "rating": 9.1, "type": "电影", "genre": "动作/科幻", "poster": "", "douban_url": "https://movie.douban.com/subject/1291843/", "download_urls": []},
    {"id": "movie_046", "title": "终结者2：审判日", "year": "1991", "rating": 8.8, "type": "电影", "genre": "动作/科幻/惊悚", "poster": "", "douban_url": "https://movie.douban.com/subject/1291844/", "download_urls": []},
    {"id": "movie_047", "title": "侏罗纪公园", "year": "1993", "rating": 8.2, "type": "电影", "genre": "科幻/冒险/惊悚", "poster": "", "douban_url": "https://movie.douban.com/subject/1292526/", "download_urls": []},
    {"id": "movie_048", "title": "阿凡达", "year": "2009", "rating": 8.8, "type": "电影", "genre": "动作/科幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/1652587/", "download_urls": []},
    {"id": "movie_049", "title": "沙丘", "year": "2021", "rating": 7.8, "type": "电影", "genre": "科幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/3001114/", "download_urls": []},
    {"id": "movie_050", "title": "银翼杀手2049", "year": "2017", "rating": 8.3, "type": "电影", "genre": "科幻/剧情/惊悚", "poster": "", "douban_url": "https://movie.douban.com/subject/26426184/", "download_urls": []},
    {"id": "movie_051", "title": "疯狂的麦克斯4：狂暴之路", "year": "2015", "rating": 8.6, "type": "电影", "genre": "动作/科幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/6702433/", "download_urls": []},
    {"id": "movie_052", "title": "三傻大闹宝莱坞", "year": "2009", "rating": 9.2, "type": "电影", "genre": "喜剧/剧情/爱情", "poster": "", "douban_url": "https://movie.douban.com/subject/3793023/", "download_urls": []},
    {"id": "movie_053", "title": "摔跤吧！爸爸", "year": "2016", "rating": 9.0, "type": "电影", "genre": "剧情/运动/传记", "poster": "", "douban_url": "https://movie.douban.com/subject/26387939/", "download_urls": []},
    {"id": "movie_054", "title": "飞越疯人院", "year": "1975", "rating": 9.1, "type": "电影", "genre": "剧情", "poster": "", "douban_url": "https://movie.douban.com/subject/1293460/", "download_urls": []},
    {"id": "movie_055", "title": "控方证人", "year": "1957", "rating": 9.6, "type": "电影", "genre": "剧情/悬疑/犯罪", "poster": "", "douban_url": "https://movie.douban.com/subject/1296141/", "download_urls": []},
    {"id": "movie_056", "title": "十二怒汉", "year": "1957", "rating": 9.4, "type": "电影", "genre": "剧情", "poster": "", "douban_url": "https://movie.douban.com/subject/1293182/", "download_urls": []},
    {"id": "movie_057", "title": "头脑特工队", "year": "2015", "rating": 8.7, "type": "电影", "genre": "动画/冒险/喜剧", "poster": "", "douban_url": "https://movie.douban.com/subject/25849049/", "download_urls": []},
    {"id": "movie_058", "title": "怪兽电力公司", "year": "2001", "rating": 8.7, "type": "电影", "genre": "动画/冒险/喜剧", "poster": "", "douban_url": "https://movie.douban.com/subject/1291579/", "download_urls": []},
    {"id": "movie_059", "title": "玩具总动员", "year": "1995", "rating": 8.6, "type": "电影", "genre": "动画/冒险/喜剧", "poster": "", "douban_url": "https://movie.douban.com/subject/1291578/", "download_urls": []},
    {"id": "movie_060", "title": "冰雪奇缘", "year": "2013", "rating": 8.4, "type": "电影", "genre": "动画/冒险/喜剧", "poster": "", "douban_url": "https://movie.douban.com/subject/4202982/", "download_urls": []},
    {"id": "movie_061", "title": "狮子王", "year": "1994", "rating": 9.1, "type": "电影", "genre": "动画/冒险/剧情", "poster": "", "douban_url": "https://movie.douban.com/subject/1291572/", "download_urls": []},
    {"id": "movie_062", "title": "幽灵公主", "year": "1997", "rating": 8.9, "type": "电影", "genre": "动画/奇幻/冒险", "poster": "", "douban_url": "https://movie.douban.com/subject/1291585/", "download_urls": []},
    {"id": "movie_063", "title": "你的名字。", "year": "2016", "rating": 8.4, "type": "电影", "genre": "动画/爱情/奇幻", "poster": "", "douban_url": "https://movie.douban.com/subject/26683290/", "download_urls": []},
    {"id": "movie_064", "title": "蝴蝶效应", "year": "2004", "rating": 8.8, "type": "电影", "genre": "剧情/科幻/悬疑", "poster": "", "douban_url": "https://movie.douban.com/subject/1292343/", "download_urls": []},
    {"id": "movie_065", "title": "致命ID", "year": "2003", "rating": 8.9, "type": "电影", "genre": "剧情/悬疑/惊悚", "poster": "", "douban_url": "https://movie.douban.com/subject/1297192/", "download_urls": []},
]

# ---- INTERNATIONAL TV SHOWS (21, all Douban 7+) ----
new_tv = [
    {"id": "tv_015", "title": "权力的游戏", "year": "2011", "rating": 9.5, "type": "电视剧", "genre": "奇幻/剧情/战争", "episodes": 73, "poster": "", "douban_url": "https://movie.douban.com/subject/3016187/", "download_urls": []},
    {"id": "tv_016", "title": "绝命毒师", "year": "2008", "rating": 9.4, "type": "电视剧", "genre": "剧情/犯罪", "episodes": 62, "poster": "", "douban_url": "https://movie.douban.com/subject/3003335/", "download_urls": []},
    {"id": "tv_017", "title": "老友记", "year": "1994", "rating": 9.4, "type": "电视剧", "genre": "喜剧/爱情", "episodes": 236, "poster": "", "douban_url": "https://movie.douban.com/subject/3286532/", "download_urls": []},
    {"id": "tv_018", "title": "生活大爆炸", "year": "2007", "rating": 9.1, "type": "电视剧", "genre": "喜剧", "episodes": 279, "poster": "", "douban_url": "https://movie.douban.com/subject/3016301/", "download_urls": []},
    {"id": "tv_019", "title": "西部世界", "year": "2016", "rating": 8.9, "type": "电视剧", "genre": "科幻/西部/剧情", "episodes": 36, "poster": "", "douban_url": "https://movie.douban.com/subject/25976967/", "download_urls": []},
    {"id": "tv_020", "title": "黑镜", "year": "2011", "rating": 9.4, "type": "电视剧", "genre": "科幻/剧情/悬疑", "episodes": 27, "poster": "", "douban_url": "https://movie.douban.com/subject/6186304/", "download_urls": []},
    {"id": "tv_021", "title": "神探夏洛克", "year": "2010", "rating": 9.5, "type": "电视剧", "genre": "悬疑/犯罪/剧情", "episodes": 15, "poster": "", "douban_url": "https://movie.douban.com/subject/3541416/", "download_urls": []},
    {"id": "tv_022", "title": "纸牌屋", "year": "2013", "rating": 9.2, "type": "电视剧", "genre": "剧情/政治", "episodes": 73, "poster": "", "douban_url": "https://movie.douban.com/subject/10551066/", "download_urls": []},
    {"id": "tv_023", "title": "越狱", "year": "2005", "rating": 9.3, "type": "电视剧", "genre": "剧情/动作/犯罪", "episodes": 90, "poster": "", "douban_url": "https://movie.douban.com/subject/1419290/", "download_urls": []},
    {"id": "tv_024", "title": "我们这一天", "year": "2016", "rating": 9.4, "type": "电视剧", "genre": "剧情/喜剧/家庭", "episodes": 106, "poster": "", "douban_url": "https://movie.douban.com/subject/26661448/", "download_urls": []},
    {"id": "tv_025", "title": "怪奇物语", "year": "2016", "rating": 8.9, "type": "电视剧", "genre": "科幻/悬疑/剧情", "episodes": 42, "poster": "", "douban_url": "https://movie.douban.com/subject/26647087/", "download_urls": []},
    {"id": "tv_026", "title": "王冠", "year": "2016", "rating": 9.2, "type": "电视剧", "genre": "剧情/历史/传记", "episodes": 60, "poster": "", "douban_url": "https://movie.douban.com/subject/26670894/", "download_urls": []},
    {"id": "tv_027", "title": "致命女人", "year": "2019", "rating": 9.3, "type": "电视剧", "genre": "剧情/喜剧/犯罪", "episodes": 20, "poster": "", "douban_url": "https://movie.douban.com/subject/26881511/", "download_urls": []},
    {"id": "tv_028", "title": "后翼弃兵", "year": "2020", "rating": 9.1, "type": "电视剧", "genre": "剧情", "episodes": 7, "poster": "", "douban_url": "https://movie.douban.com/subject/27044186/", "download_urls": []},
    {"id": "tv_029", "title": "切尔诺贝利", "year": "2019", "rating": 9.6, "type": "电视剧", "genre": "剧情/历史/灾难", "episodes": 5, "poster": "", "douban_url": "https://movie.douban.com/subject/27083037/", "download_urls": []},
    {"id": "tv_030", "title": "风骚律师", "year": "2015", "rating": 9.6, "type": "电视剧", "genre": "剧情/犯罪", "episodes": 63, "poster": "", "douban_url": "https://movie.douban.com/subject/25899395/", "download_urls": []},
    {"id": "tv_031", "title": "毒枭", "year": "2015", "rating": 9.3, "type": "电视剧", "genre": "剧情/犯罪/传记", "episodes": 30, "poster": "", "douban_url": "https://movie.douban.com/subject/25845633/", "download_urls": []},
    {"id": "tv_032", "title": "继承之战", "year": "2018", "rating": 9.3, "type": "电视剧", "genre": "剧情", "episodes": 39, "poster": "", "douban_url": "https://movie.douban.com/subject/27044130/", "download_urls": []},
    {"id": "tv_033", "title": "暗黑", "year": "2017", "rating": 9.3, "type": "电视剧", "genre": "科幻/悬疑/剧情", "episodes": 26, "poster": "", "douban_url": "https://movie.douban.com/subject/26930556/", "download_urls": []},
    {"id": "tv_034", "title": "最后生还者", "year": "2023", "rating": 9.0, "type": "电视剧", "genre": "剧情/科幻/冒险", "episodes": 9, "poster": "", "douban_url": "https://movie.douban.com/subject/30266352/", "download_urls": []},
    {"id": "tv_035", "title": "瑞克和莫蒂", "year": "2013", "rating": 9.7, "type": "电视剧", "genre": "动画/科幻/喜剧", "episodes": 71, "poster": "", "douban_url": "https://movie.douban.com/subject/25815034/", "download_urls": []},
]

# Merge
all_entries = existing_movies + new_movies + new_tv
print(f"Total: {len(all_entries)} entries")
print(f"  Existing: {len(existing_movies)}")
print(f"  New movies: {len(new_movies)}")
print(f"  New TV: {len(new_tv)}")

# Verify no duplicate IDs
ids = [e["id"] for e in all_entries]
if len(ids) != len(set(ids)):
    from collections import Counter
    dupes = {k: v for k, v in Counter(ids).items() if v > 1}
    print(f"WARNING: Duplicate IDs: {dupes}")
else:
    print("No duplicate IDs - OK")

# Write
db = {"movies": all_entries, "last_updated": "2026-05-19"}
with open(DB_FILE, "w") as f:
    json.dump(db, f, ensure_ascii=False, indent=2)
print(f"\nWritten to {DB_FILE}")

# Generate TMDB search query map for new entries only
search_queries = {}
for m in new_movies + new_tv:
    title_en_map = {
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
    search_queries[m["title"]] = title_en_map.get(m["title"], m["title"])

# Write SEARCH_QUERIES dict for poster download script
print("\n\n# SEARCH_QUERIES for poster script:")
for title, query in sorted(search_queries.items()):
    print(f'    "{title}": "{query}",')

print(f"\n\nTotal new entries needing posters: {len(new_movies) + len(new_tv)}")