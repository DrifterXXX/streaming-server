"""批量补充全网盘源（公众号/微信搜法）"""
import json, os, re, time, subprocess as sp
from urllib.parse import quote, unquote
from datetime import datetime

DB_PATH = os.path.expanduser("~/services/streaming-server/database.json")
PROXY = "http://127.0.0.1:7897"

PAN_PATTERNS = [
    (r'pan\.baidu\.com/s/[a-zA-Z0-9_-]+', "百度网盘"),
    (r'(?:www\.)?aliyundrive\.com/s/[a-zA-Z0-9_-]+', "阿里云盘"),
    (r'(?:www\.)?alipan\.com/s/[a-zA-Z0-9_-]+', "阿里云盘"),
    (r'(?:pan\.)?quark\.cn/s/[a-zA-Z0-9_-]+', "夸克网盘"),
    (r'cloud\.189\.cn/s/[a-zA-Z0-9_-]+', "天翼云盘"),
    (r'pan\.xunlei\.com/s/[a-zA-Z0-9_-]+', "迅雷云盘"),
    (r'115\.com/s/[a-zA-Z0-9_-]+', "115网盘"),
    (r'(?:www\.)?123pan\.com/s/[a-zA-Z0-9_-]+', "123云盘"),
    (r'(?:www\.)?lanzou[a-z]\.(?:com|cn)/[a-zA-Z0-9/]+', "蓝奏云"),
]

def curl_fetch(url, timeout=10):
    try:
        proc = sp.run(
            ["curl", "-sL", "--max-time", str(timeout), "-x", PROXY,
             "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
             "-H", "Accept-Language: zh-CN,zh;q=0.9",
             "--connect-timeout", "6", url],
            capture_output=True, text=True, timeout=timeout+5,
        )
        return proc.stdout if proc.returncode == 0 and proc.stdout else ""
    except:
        return ""

def ddg_lite_search(query):
    url = f"https://lite.duckduckgo.com/lite/?q={quote(query)}"
    html = curl_fetch(url, timeout=12)
    if not html or len(html) < 2000:
        return [], html
    links = []
    for m in re.finditer(r'uddg=([^&"]+)', html):
        decoded = unquote(m.group(1))
        if decoded.startswith('http') and not any(x in decoded for x in ['duckduckgo.com', 'google.']):
            links.append(decoded)
    return links, html

def extract_pans(text, seen_set):
    pans = []
    for pattern, pan_name in PAN_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            url = m.group(0)
            if not url.startswith('http'):
                url = 'https://' + url
            if url in seen_set:
                continue
            seen_set.add(url)
            ctx = text[max(0, m.start()-150):min(len(text), m.end()+250)]
            pwd_m = re.search(r'(?:pwd|密码|提取码|访问码)[=：:\s]*([a-zA-Z0-9]{4,6})', ctx)
            pwd = pwd_m.group(1).strip() if pwd_m else None
            url_pwd = re.search(r'[?&]pwd=([a-zA-Z0-9]+)', url)
            if url_pwd:
                pwd = url_pwd.group(1)
            if pwd and '?pwd=' not in url and '&pwd=' not in url:
                url += f'?pwd={pwd}'
            pans.append({"url": url, "source": pan_name, "pwd": pwd})
    return pans

def search_movie(title, seen_set):
    all_pans = []
    visited_urls = set()

    queries = [
        f"{title} 百度网盘 下载",
        f"{title} 百度云 资源",
        f"{title} 夸克网盘",
        f"{title} 阿里云盘",
        f"{title} 网盘 下载 资源",
        f"{title} 百度云 公众号",
        f"{title} 微信 网盘 资源",
        f"{title} 公众号 资源 下载",
        f"{title} pan.baidu 下载",
    ]

    for q in queries:
        links, html = ddg_lite_search(q)
        if not links:
            continue

        # 从搜索结果页直接提取盘链
        pans = extract_pans(html, seen_set)
        all_pans.extend(pans)

        # 访问结果页面提取
        for link in links[:6]:
            if link in visited_urls:
                continue
            visited_urls.add(link)
            if any(x in link for x in ['bilibili', 'douban', 'zhihu', 'youku', 'iqiyi',
                                         'tencent', 'ixigua', 'sohu', 'sina', 'news.',
                                         'game.', '163.com', 'facebook', 'twitter']):
                continue
            html2 = curl_fetch(link, timeout=10)
            if html2 and len(html2) > 1000:
                pans2 = extract_pans(html2, seen_set)
                all_pans.extend(pans2)

        if len(all_pans) >= 5:
            break

    return all_pans

# 读库
with open(DB_PATH) as f:
    db = json.load(f)
movies = db if isinstance(db, list) else db.get("movies", [])

# 只补源数<=2的 + 标记过manual_resource的
targets = [m for m in movies if (len(m.get("resource_sources") or []) <= 2) or m.get("manual_resource")]
# 去重
seen_ids = set()
targets_uniq = []
for m in targets:
    if m.get("id") not in seen_ids:
        seen_ids.add(m.get("id"))
        targets_uniq.append(m)

print(f"需要补充的: {len(targets_uniq)} 部")
print("=" * 50)

total_new = 0
for i, m in enumerate(targets_uniq, 1):
    title = m["title"]
    existing = len(m.get("resource_sources") or [])
    print(f"[{i}/{len(targets_uniq)}] {title} (现有{existing}个)...", end=" ", flush=True)

    seen = set(s["url"] for s in (m.get("resource_sources") or []))
    for u in (m.get("download_urls") or []):
        seen.add(u)

    pans = search_movie(title, seen)

    if pans:
        rs = m.get("resource_sources") or []
        dl = m.get("download_urls") or []
        for p in pans:
            rs.append({
                "url": p["url"],
                "title": f"{p['source']}: {title}",
                "source": p["source"],
                "status": "available",
                "added_at": datetime.now().isoformat(),
            })
            dl.append(p["url"])
        m["resource_sources"] = rs
        m["download_urls"] = dl
        m.pop("manual_resource", None)
        total_new += len(pans)
        print(f"✅ 新增{len(pans)}个")
    else:
        print("⚪ 无新增")

with open(DB_PATH, 'w') as f:
    json.dump(db, f, ensure_ascii=False, indent=2)

# 统计
movies = db if isinstance(db, list) else db.get("movies", [])
src_counts = {}
for m in movies:
    n = len(m.get("resource_sources") or [])
    src_counts[n] = src_counts.get(n, 0) + 1
print(f"\n总计新增 {total_new} 个源")
for k in sorted(src_counts):
    print(f"  {k}个源: {src_counts[k]}部")
print(f"有源总数: {len(movies)}部")
weak = [m for m in movies if len(m.get("resource_sources") or []) <= 1]
if weak:
    print(f"\n仅1个源的:")
    for m in weak:
        print(f"  {m['title']} ({m.get('year','')}) - {len(m.get('resource_sources') or [])}个")