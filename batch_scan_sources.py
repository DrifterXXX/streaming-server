"""批量自动搜索所有电影的下载源"""
import json, os, time, sys, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = os.path.expanduser("~/services/streaming-server/database.json")
API_BASE = "http://127.0.0.1:9878"

def auto_find(movie):
    mid = movie["id"]
    title = movie.get("title", "?")
    url = f"{API_BASE}/api/auto-find?id={urllib.parse.quote(mid)}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            count = data.get("count", 0)
            return (title, count, None)
    except Exception as e:
        return (title, 0, str(e)[:60])

# 读取数据库
with open(DB_PATH) as f:
    db = json.load(f)
movies = db if isinstance(db, list) else db.get("movies", [])

# 滤出还没有源的
to_scan = [m for m in movies if not m.get("resource_sources") and not m.get("download_urls")]
total = len(to_scan)
print(f"共 {len(movies)} 部，需要扫描 {total} 部")
print(f"开始批量扫描（每批3个并发，预计 ~{total * 15 // 60} 分钟）")
print("=" * 60)

results = []
done = 0
batch_size = 3
start_time = time.time()

for i in range(0, total, batch_size):
    batch = to_scan[i:i+batch_size]
    with ThreadPoolExecutor(max_workers=batch_size) as pool:
        futures = {pool.submit(auto_find, m): m for m in batch}
        for f in as_completed(futures):
            title, count, err = f.result()
            done += 1
            elapsed = int(time.time() - start_time)
            status = f"✅ {count}个源" if count > 0 else ("⚠️ 失败" if err else "❌ 0个源")
            err_suffix = f" - {err}" if err else ""
            print(f"[{done}/{total} {elapsed}s] {title[:20]:20s} → {status}{err_suffix}")
            results.append((title, count, err))

    # 小停顿避免请求太密集
    time.sleep(0.5)

# 汇总
elapsed = int(time.time() - start_time)
found = sum(1 for _, c, _ in results if c > 0)
failed = sum(1 for _, _, e in results if e is not None)
zero = sum(1 for _, c, e in results if c == 0 and e is None)
print("\n" + "=" * 60)
print(f"扫描完成！耗时 {elapsed}s")
print(f"找到源的: {found} 部")
print(f"0个源的: {zero} 部")
print(f"请求失败的: {failed} 部")

# 输出0个源的作品
if zero > 0:
    print("\n--- 0个源的作品 ---")
    for title, count, err in results:
        if count == 0 and err is None:
            print(f"  {title}")