#!/usr/bin/env python3
"""
流媒体服务器 v3 - 带海报刮削、豆瓣评分筛选、边下边播
- 本地数据库 + 豆瓣7分筛选
- 后台下载队列
- HTTP Range请求（边下边播）
- 精美的Netflix风格UI
"""

import http.server
import socketserver
import os
import sys
import json
import threading
import time
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path
from datetime import datetime
from html import escape
import re
import hashlib

# 下载增强模块
import stream_server_v3_dl as dl_enhancer

# 配置
VIDEO_DIR = Path.home() / "services/streaming-server" / "videos"
DB_FILE = Path.home() / "services/streaming-server" / "database.json"
QUEUE_FILE = Path.home() / "services/streaming-server" / "download_queue.json"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9878

# Phase 1: 并发下载控制 — 最多3个同时进行
MAX_CONCURRENT_DOWNLOADS = 3
_download_semaphore = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# 豆瓣评分阈值
MIN_RATING = 7.0

# 确保目录存在
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


class DownloadManager:
    """后台下载管理器 - 支持边下边播"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.queue = self._load_queue()
        self.active_downloads = {}
        self._start_worker()
    
    def _load_queue(self):
        if QUEUE_FILE.exists():
            try:
                with open(QUEUE_FILE) as f:
                    return json.load(f)
            except:
                pass
        return []
    
    def _save_queue(self):
        with open(QUEUE_FILE, "w") as f:
            json.dump(self.queue, f, ensure_ascii=False, indent=2)
    
    def add(self, title, url, episode=None, output_name=None):
        """添加下载任务"""
        if episode:
            filename = output_name or f"{title}.ep{episode:02d}.mp4"
        else:
            filename = output_name or f"{title}.mp4"
        
        item = {
            "id": f"{title}_{episode or 'full'}_{int(time.time())}",
            "title": title,
            "url": url,
            "episode": episode,
            "filename": filename,
            "status": "pending",
            "progress": 0,
            "speed": "0 MB/s",
            "added_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None
        }
        self.queue.append(item)
        self._save_queue()
        return item["id"]
    
    def _start_worker(self):
        """启动后台下载线程"""
        def worker():
            while True:
                # 处理pending任务
                pending = [i for i in self.queue if i["status"] == "pending"]
                for item in pending:
                    threading.Thread(target=self._download, args=(item,), daemon=True).start()
                time.sleep(3)
        
        t = threading.Thread(target=worker, daemon=True)
        t.start()
    
    def _download(self, item):
        """执行下载 — 带并发控制（最多3个）"""
        _download_semaphore.acquire()
        try:
            self._do_download(item)
        except Exception:
            pass
        finally:
            _download_semaphore.release()

    def _do_download(self, item):
        """下载核心逻辑"""
        item["status"] = "downloading"
        item["started_at"] = datetime.now().isoformat()
        self._save_queue()
        
        output_path = VIDEO_DIR / item["filename"]
        
        try:
            start_time = time.time()
            
            def report_hook(block_num, block_size, total_size):
                downloaded = block_num * block_size
                if total_size > 0:
                    item["progress"] = min(100, int(downloaded * 100 / total_size))
                elapsed = time.time() - start_time
                if elapsed > 0 and downloaded > 0:
                    speed = downloaded / elapsed / 1024 / 1024
                    item["speed"] = f"{speed:.1f} MB/s"
            
            urllib.request.urlretrieve(item["url"], str(output_path), report_hook)
            
            if output_path.exists() and output_path.stat().st_size > 1000000:
                item["status"] = "completed"
                item["completed_at"] = datetime.now().isoformat()
                item["size"] = output_path.stat().st_size
                item["size_mb"] = round(output_path.stat().st_size / 1024 / 1024, 1)
            else:
                item["status"] = "failed"
                item["error"] = "文件过小或损坏"
                if output_path.exists():
                    output_path.unlink()
        
        except Exception as e:
            item["status"] = "failed"
            item["error"] = str(e)
            if output_path.exists():
                output_path.unlink()
        
        self._save_queue()
    
    def get_status(self):
        return {"queue": self.queue[-20:]}  # 最近20个任务
    
    def cancel(self, item_id):
        """取消下载 - 同时杀后台进程"""
        proc = self.active_downloads.pop(item_id, None)
        if proc:
            try:
                proc.kill()
            except:
                pass
        
        for item in self.queue:
            if item["id"] == item_id and item["status"] in ["pending", "downloading"]:
                item["status"] = "cancelled"
                self._save_queue()
                return True
        return False

    def remove(self, item_id):
        """从队列中删除任务（支持所有状态）- 同时杀后台进程"""
        proc = self.active_downloads.pop(item_id, None)
        if proc:
            try:
                proc.kill()
            except:
                pass
        
        for i, item in enumerate(self.queue):
            if item["id"] == item_id:
                self.queue.pop(i)
                self._save_queue()
                return True
        return False


class StreamHandler(http.server.SimpleHTTPRequestHandler):
    """流媒体请求处理器"""
    
    download_manager = DownloadManager()
    db = None
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIDEO_DIR), **kwargs)
    
    @classmethod
    def get_db(cls):
        if cls.db is None:
            if DB_FILE.exists():
                try:
                    with open(DB_FILE) as f:
                        cls.db = json.load(f)
                except:
                    cls.db = {"movies": []}
            else:
                cls.db = {"movies": []}
        return cls.db
    
    def do_GET(self):
        # Parse path without query string for routing
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        # 对于搜索API，使用POST避免URL编码问题
        if path == "/api/search" or path == "/api/search-debug":
            self.handle_search_post()
            return
        
        query = {}
        if parsed.query:
            try:
                query = dict(urllib.parse.parse_qsl(parsed.query))
            except:
                pass
        
        if path == "/" or path == "/index.html":
            self.send_index()
        elif path == "/api/database":
            self.send_database()
        elif path == "/api/search":
            self.send_search(query)
        elif path == "/api/search-external":
            self.send_search_external(query)
        elif path == "/api/download":
            self.send_download(query)
        elif path == "/api/status":
            self.send_status()
        elif path == "/api/health" or path == "/health":
            self.send_json({"status": "ok", "time": datetime.now().isoformat()})
        elif path == "/api/test":
            # Test endpoint to debug
            db = {"movies": []}
            if DB_FILE.exists():
                try:
                    with open(DB_FILE) as f:
                        db = json.load(f)
                except:
                    pass
            self.send_json({"db_loaded": True, "movies": [m["title"] for m in db.get("movies", [])]})
        elif path == "/api/search-debug":
            # Debug search endpoint
            q = query.get("q", "")
            db = {"movies": []}
            if DB_FILE.exists():
                try:
                    with open(DB_FILE) as f:
                        db = json.load(f)
                except:
                    pass
            results = []
            details = []
            for m in db.get("movies", []):
                rating = m.get("rating", 0)
                title = m.get("title", "")
                matches_rating = rating >= MIN_RATING
                matches_query = q.lower() in title.lower()
                details.append({
                    "title": title,
                    "rating": rating,
                    "rating_ok": matches_rating,
                    "query_match": matches_query,
                    "included": matches_rating and matches_query
                })
                if matches_rating and matches_query:
                    results.append(m)
            self.send_json({"query": q, "results": results, "details": details})
        elif path == "/api/cancel":
            self.send_cancel(query)
        elif path == "/api/remove":
            self.send_remove(query)
        elif path == "/api/local-files":
            dl_enhancer.handle_local_files(self)
        elif path == "/api/dl-url":
            dl_enhancer.handle_dl_url(self, query, self.download_manager)
        elif path == "/api/dl-cloud":
            self.send_dl_cloud(query)
        elif path == "/api/dl-batch":
            dl_enhancer.handle_dl_batch(self, query, self.download_manager)
        elif path == "/api/auto-find":
            dl_enhancer.handle_auto_find(self, query, self.download_manager)
        elif path == "/api/search-sources":
            dl_enhancer.handle_search_sources(self, query, self.download_manager)
        elif path == "/api/dl-urls":
            # 为媒体更新下载链接
            self.send_dl_urls(query)
        elif path == "/api/open-file":
            self.send_open_file()
        elif path == "/api/space":
            dl_enhancer.handle_space(self, query)
        elif path == "/api/reload-db":
            self.send_reload_db()
        elif path == "/api/db-meta":
            self.send_db_meta()
        elif path == "/api/pan-status":
            self.send_pan_status()
        elif path.startswith("/posters/"):
            # 从本地海报目录静态服务
            self.serve_local_poster(path)
        elif path.startswith("/api/poster/"):
            # 海报代理：从路径中提取Douban海报URL，带Referer头取图
            self.serve_poster(path)
        else:
            # 处理视频文件的Range请求（边下边播）
            super().do_GET()

    def serve_poster(self, path):
        """代理Douban海报图片"""
        import urllib.request
        # /api/poster/{base64_url} 或 /api/poster/https:/...
        poster_path = path[len("/api/poster/"):]
        if not poster_path:
            self.send_error(404)
            return

        # 如果base64编码，解码
        import base64
        try:
            # Fix base64 padding
            padding = 4 - len(poster_path) % 4
            if padding != 4:
                poster_path += '=' * padding
            decoded = base64.urlsafe_b64decode(poster_path).decode('utf-8')
            img_url = decoded
        except:
            # Otherwise treat as direct URL path (for /api/poster/https:/... pattern)
            img_url = poster_path.replace("https:/", "https://").replace("http:/", "http://")
        
        if not img_url.startswith("http"):
            self.send_error(400, "Bad Request: invalid poster URL")
            return
        
        try:
            req = urllib.request.Request(img_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://movie.douban.com/"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            self.send_error(502, f"Proxy error: {str(e)}")

    def serve_local_poster(self, path):
        """从本地posters目录提供海报图片"""
        # /posters/{filename} or /posters/{id}.jpg
        filename = path[len("/posters/"):]
        poster_dir = Path.home() / "services/streaming-server" / "posters"
        poster_path = poster_dir / filename

        # 安全校验：只允许访问posters目录下的文件
        try:
            poster_path = poster_path.resolve()
            if not str(poster_path).startswith(str(poster_dir)):
                self.send_error(403)
                return
        except:
            self.send_error(400)
            return

        if poster_path.exists() and poster_path.is_file():
            self.send_response(200)
            # 根据扩展名设置Content-Type
            ext = poster_path.suffix.lower()
            content_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(ext, "application/octet-stream")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(poster_path.stat().st_size))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(poster_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            # Fallback: 如果本地没有，尝试豆瓣代理
            img_url = f"https://via.placeholder.com/400x600/16213e/e94560?text=No+Poster"
            self.send_response(302)
            self.send_header("Location", img_url)
            self.end_headers()

    def do_POST(self):
        """处理POST请求（用于搜索等需要中文参数的API）"""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/api/search" or path == "/api/search-debug":
            self.handle_search_post()
            return
        
        if path == "/api/add-to-library":
            self.handle_add_to_library()
            return
        
        self.send_response(405)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b"Method Not Allowed")
    
    def handle_search_post(self):
        """处理POST搜索请求"""
        # 读取请求体
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            body = self.rfile.read(content_length)
            # 尝试UTF-8解码
            try:
                body_str = body.decode('utf-8')
            except:
                body_str = body.decode('latin-1')
            
            # 解析表单数据
            query = urllib.parse.parse_qs(body_str)
            q = query.get("q", [""])[0]
        else:
            q = ""
        
        # 如果是debug端点
        if self.path.startswith("/api/search-debug"):
            self.send_search_debug(q)
        else:
            self.send_search({"q": q})
    
    def send_search_json(self, q):
        """发送搜索结果JSON"""
        db = {"movies": []}
        if DB_FILE.exists():
            try:
                with open(DB_FILE) as f:
                    db = json.load(f)
            except:
                pass
        
        results = []
        for m in db.get("movies", []):
            rating = m.get("rating", 0)
            title = m.get("title", "")
            if rating < MIN_RATING:
                continue
            if not q or q.lower() in title.lower():
                results.append(m)
        
        self.send_json(results)
    
    def send_search_debug(self, q):
        """发送搜索调试信息"""
        db = {"movies": []}
        if DB_FILE.exists():
            try:
                with open(DB_FILE) as f:
                    db = json.load(f)
            except:
                pass
        
        results = []
        details = []
        for m in db.get("movies", []):
            rating = m.get("rating", 0)
            title = m.get("title", "")
            matches_rating = rating >= MIN_RATING
            matches_query = not q or q.lower() in title.lower()
            details.append({
                "title": title,
                "rating": rating,
                "rating_ok": matches_rating,
                "query_match": matches_query,
                "included": matches_rating and matches_query
            })
            if matches_rating and matches_query:
                results.append(m)
        
        self.send_json({"query": q, "results": results, "details": details})
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    
    def send_index(self):
        html = self._build_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))
    
    def send_videos_list(self):
        """获取本地视频列表"""
        videos = []
        for f in VIDEO_DIR.glob("*.mp4"):
            try:
                stat = f.stat()
                videos.append({
                    "name": f.name,
                    "size": stat.st_size,
                    "size_mb": round(stat.st_size / 1024 / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "url": f"/{f.name}"
                })
            except:
                pass
        videos.sort(key=lambda x: x["modified"], reverse=True)
        self.send_json(videos)
    
    def send_database(self):
        """获取影视数据库"""
        db = self.get_db()
        # 筛选7分以上
        filtered = [m for m in db.get("movies", []) if m.get("rating", 0) >= MIN_RATING]
        self.send_json(filtered)
    
    def send_search(self, query):
        """搜索本地数据库（不限评分）"""
        q = query.get("q", "")
        if not q:
            self.send_json([])
            return
        
        db = self.get_db()
        
        results = []
        for m in db.get("movies", []):
            title = m.get("title", "")
            if q.lower() in title.lower():
                results.append(m)
        
        # Mark as local items
        for r in results:
            r["_in_library"] = True
        
        self.send_json(results)
    
    def send_search_external(self, query):
        """搜索外部源（豆瓣+TMDB），返回未在本地库的作品"""
        q = query.get("q", "")
        if not q:
            self.send_json([])
            return
        
        # Get existing titles in library
        db = self.get_db()
        existing_titles = {m.get("title", "").lower() for m in db.get("movies", [])}
        
        results = []
        
        # Search Douban suggest API
        try:
            douban_url = f"https://movie.douban.com/j/subject_suggest?q={urllib.parse.quote(q)}"
            req = urllib.request.Request(douban_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://movie.douban.com/"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                douban_data = json.loads(resp.read())
                for i, item in enumerate(douban_data[:8]):  # 最多8个外部结果
                    title = item.get("title", "")
                    title_lower = title.lower()
                    
                    rating = item.get("rate", "0")
                    try:
                        rating = float(rating)
                    except:
                        rating = 0
                    
                    # Try TMDB search for this item to get proper rating
                    if rating == 0:
                        try:
                            tmdb_search_url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(title)}"
                            tmdb_req = urllib.request.Request(tmdb_search_url, headers={
                                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                                "Accept-Language": "zh-CN,zh;q=0.9",
                            })
                            with urllib.request.urlopen(tmdb_req, timeout=8) as tmdb_resp:
                                tmdb_html_block = tmdb_resp.read().decode("utf-8", errors="replace")
                                # Extract TMDB rating from search results
                                rt_tmdb = re.search(r'data-rating="([\d.]+)"', tmdb_html_block)
                                if rt_tmdb:
                                    tmdb_rating = float(rt_tmdb.group(1))
                                    if tmdb_rating > 0:
                                        rating = tmdb_rating / 2  # TMDB is /10, Douban is /5
                        except:
                            pass
                    
                    # Determine type from subtype
                    sub_type = item.get("sub_type", "")
                    media_type = "电视剧" if sub_type == "tv" else "电影"
                    
                    # Get year from year field
                    year = item.get("year", "")
                    
                    # Count seasons/episodes for TV
                    episodes = 0
                    seasons_count = 1
                    if media_type == "电视剧":
                        # Try TMDB for episode count
                        try:
                            tmdb_search_url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(title)}"
                            tmdb_req = urllib.request.Request(tmdb_search_url, headers={
                                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                                "Accept-Language": "zh-CN,zh;q=0.9",
                            })
                            with urllib.request.urlopen(tmdb_req, timeout=8) as tmdb_resp:
                                tmdb_html_block = tmdb_resp.read().decode("utf-8", errors="replace")
                                # Check if it's a TV show in TMDB
                                tv_match = re.search(r'class="media"[^>]*>(电视剧|剧集|TV)', tmdb_html_block, re.I)
                                if tv_match:
                                    # Try to extract episode count
                                    ep_tmdb = re.search(r'(\d+) 集', tmdb_html_block)
                                    if ep_tmdb:
                                        episodes = int(ep_tmdb.group(1))
                                    season_tmdb = re.search(r'(\d+) 季', tmdb_html_block)
                                    if season_tmdb:
                                        seasons_count = int(season_tmdb.group(1))
                        except:
                            pass
                    
                    result = {
                        "title": title,
                        "year": year,
                        "rating": rating,
                        "type": media_type,
                        "genre": item.get("genres", "").replace("/", "/"),
                        "poster": item.get("img", ""),
                        "douban_id": item.get("id", ""),
                        "douban_url": f"https://movie.douban.com/subject/{item.get('id', '')}/",
                        "_source": "douban",
                        "_in_library": title_lower in existing_titles,
                        "episodes": episodes or (12 if media_type == "电视剧" else 0),
                        "seasons": []
                    }
                    
                    # Build seasons if TV show
                    if media_type == "电视剧" and episodes > 0:
                        eps_per_season = episodes // max(seasons_count, 1)
                        result["seasons"] = [
                            {"season": i+1, "episode_count": eps_per_season if i+1 < seasons_count else episodes - eps_per_season * (seasons_count - 1)}
                            for i in range(seasons_count)
                        ]
                    
                    results.append(result)
        except Exception as e:
            pass  # Douban search failed, continue to TMDB
        
        # Also try TMDB if Douban returned nothing or very few
        if len(results) < 3:
            try:
                tmdb_url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(q)}"
                tmdb_req = urllib.request.Request(tmdb_url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                })
                with urllib.request.urlopen(tmdb_req, timeout=15) as resp:
                    tmdb_html = resp.read().decode("utf-8", errors="replace")
                    blocks = tmdb_html.split("data-object-id=")
                    for block in blocks[1:6]:
                        title_match = re.search(r'alt="([^"]+)"', block)
                        img_match = re.search(r'src="(https://media\.themoviedb\.org/t/p/[^"]+\.jpg)"', block)
                        type_match = re.search(r'class="media"[^>]*>(电影|剧集|电视剧|Movie|TV)', block, re.I)
                        
                        if not (title_match and img_match):
                            continue
                        
                        tmdb_title = title_match.group(1)
                        tmdb_title_lower = tmdb_title.lower()
                        
                        # Skip if already in results
                        if any(r["title"].lower() == tmdb_title_lower for r in results):
                            continue
                        
                        # Skip if already in library
                        if tmdb_title_lower in existing_titles:
                            continue
                        
                        img_url = img_match.group(1).replace(
                            "/t/p/w94_and_h141_face/", "/t/p/original/"
                        ).replace("media.themoviedb.org", "image.tmdb.org")
                        
                        results.append({
                            "title": tmdb_title,
                            "year": "",
                            "rating": 0,
                            "type": "电影",
                            "genre": "",
                            "poster": img_url,
                            "douban_id": "",
                            "douban_url": "",
                            "_source": "tmdb",
                            "_in_library": False,
                            "episodes": 0,
                            "seasons": []
                        })
            except:
                pass
        
        self.send_json(results)
    
    def handle_add_to_library(self):
        """将外部搜索结果添加到本地媒体库"""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length <= 0:
            self.send_json({"success": False, "error": "空请求"}, 400)
            return
        
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except:
            try:
                body_str = body.decode('utf-8')
                data = json.loads(body_str)
            except:
                self.send_json({"success": False, "error": "JSON解析失败"}, 400)
                return
        
        title = data.get("title", "")
        if not title:
            self.send_json({"success": False, "error": "缺少标题"}, 400)
            return
        
        # Generate a unique ID
        id_hash = hashlib.md5(title.encode()).hexdigest()[:8]
        media_id = f"ext_{id_hash}_{int(time.time())}"
        
        # Build media entry
        entry = {
            "id": media_id,
            "title": title,
            "year": data.get("year", ""),
            "rating": data.get("rating", 0),
            "type": data.get("type", "电影"),
            "genre": data.get("genre", ""),
            "poster": "",
            "douban_url": data.get("douban_url", ""),
            "douban_id": data.get("douban_id", ""),
            "download_urls": [],
            "seasons": data.get("seasons", []),
            "episodes": data.get("episodes", 0),
            "added_at": datetime.now().isoformat()
        }
        
        # Add to database
        db = self.get_db()
        db.setdefault("movies", []).append(entry)
        with open(DB_FILE, "w") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        
        # Download poster from douban or TMDB
        poster_url = data.get("poster", "")
        if poster_url:
            poster_dir = Path.home() / "services/streaming-server" / "posters"
            poster_dir.mkdir(exist_ok=True)
            poster_path = poster_dir / f"{media_id}.jpg"
            
            try:
                req = urllib.request.Request(poster_url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": "https://movie.douban.com/"
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    poster_data = resp.read()
                    with open(poster_path, "wb") as f:
                        f.write(poster_data)
            except:
                pass
        
        self.send_json({"success": True, "id": media_id, "entry": entry})
    
    def send_download(self, query):
        """添加下载任务"""
        title = query.get("title", "")
        url = query.get("url", "")
        episode = query.get("episode", "")
        filename = query.get("filename", "")
        
        if not title or not url:
            self.send_json({"success": False, "error": "缺少参数"}, 400)
            return
        
        try:
            ep = int(episode) if episode else None
            item_id = self.download_manager.add(title, url, episode=ep, output_name=filename)
            self.send_json({"success": True, "id": item_id})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, 500)
    
    def send_dl_cloud(self, query):
        """网盘自动下载 — 保存分享 + 下载到本地"""
        url = query.get("url", "")
        title = query.get("title", "")
        
        if not url:
            self.send_json({"success": False, "error": "缺少URL"}, 400)
            return
        
        # 延迟导入CloudDiskDL
        try:
            from cloud_disk_dl import CloudDiskDL
            cdd = CloudDiskDL()
        except ImportError as e:
            self.send_json({"success": False, "error": f"网盘引擎加载失败: {str(e)}"}, 500)
            return
        
        # 检测网盘类型
        pan_info = cdd.detect(url)
        if not pan_info:
            self.send_json({"success": False, "error": "无法识别的网盘链接"}, 400)
            return
        
        # 检查登录
        pan_key = pan_info['key']
        engine = cdd.engines.get(pan_key)
        if engine and hasattr(engine, 'is_logged_in') and not engine.is_logged_in():
            login_prompt = cdd.login_prompt(pan_key)
            self.send_json({
                "success": False,
                "error": f"{pan_info['name']} 未登录",
                "need_login": True,
                "pan_key": pan_key,
                "login_prompt": login_prompt,
            }, 401)
            return
        
        # 添加到下载队列（先占位）
        item_id = self.download_manager.add(
            title or url, url,
            output_name=f"[网盘] {title or '下载'}.mp4"
        )
        
        # 后台启动下载
        import threading
        t = threading.Thread(
            target=self._cloud_dl_worker,
            args=(url, title or pan_info['name']),
            daemon=True,
        )
        t.start()
        
        self.send_json({
            "success": True,
            "id": item_id,
            "message": f"已开始从{pan_info['name']}自动下载",
        })
    
    def _cloud_dl_worker(self, url, title):
        """后台网盘下载工作线程"""
        from cloud_disk_dl import CloudDiskDL
        cdd = CloudDiskDL()
        
        # 更新队列状态
        item_id = None
        for item in self.download_manager.queue:
            if item["url"] == url and item["status"] == "pending":
                item_id = item["id"]
                item["status"] = "downloading"
                self.download_manager._save_queue()
                break
        
        def progress(pct, msg):
            for item in self.download_manager.queue:
                if item.get("id") == item_id:
                    item["progress"] = pct
                    item["speed"] = msg
                    self.download_manager._save_queue()
                    break
        
        result = cdd.download(url, title, progress_callback=progress)
        
        # 更新最终状态
        for item in self.download_manager.queue:
            if item.get("id") == item_id:
                if result.get("success"):
                    item["status"] = "completed"
                    item["progress"] = 100
                    item["filename"] = result.get("filename", "")
                    item["size"] = result.get("size", 0)
                    item["size_mb"] = result.get("size_mb", 0)
                    item["completed_at"] = datetime.now().isoformat()
                else:
                    item["status"] = "failed"
                    item["error"] = result.get("error", "下载失败")
                self.download_manager._save_queue()
                break
    
    def send_status(self):
        self.send_json(self.download_manager.get_status())
    
    def send_open_file(self):
        """打开文件路径（在Finder中显示）"""
        filename = self.path.split("filename=")[-1] if "filename=" in self.path else ""
        if not filename:
            self.send_json({"success": False, "error": "缺少文件名"}, 400)
            return

        # Find the file in VIDEO_DIR, then open Finder
        import os, subprocess
        video_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos")
        filepath = os.path.join(video_dir, filename)

        found = None
        if os.path.exists(filepath):
            found = filepath
        else:
            for f in os.listdir(video_dir):
                if filename.lower() in f.lower():
                    found = os.path.join(video_dir, f)
                    break

        if found:
            subprocess.Popen(["open", "-R", found])
            self.send_json({"success": True, "path": found, "filename": os.path.basename(found)})
        else:
            self.send_json({"success": False, "error": "文件未找到"}, 404)

    def send_reload_db(self):
        """手动重新加载数据库"""
        try:
            StreamHandler.db = None  # 清除缓存，下次get_db()会重新读取
            db = self.get_db()
            self.send_json({"success": True, "message": "数据库已重新加载", "count": len(db.get("movies", []))})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, 500)

    def send_db_meta(self):
        """返回数据库元信息（修改时间、条目数），用于前端热更新检测"""
        try:
            import os
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.json")
            if os.path.exists(db_path):
                mtime = os.path.getmtime(db_path)
                db = self.get_db()
                self.send_json({
                    "mtime": mtime,
                    "count": len(db.get("movies", [])),
                    "size": os.path.getsize(db_path)
                })
            else:
                self.send_json({"mtime": 0, "count": 0, "size": 0})
        except Exception as e:
            self.send_json({"mtime": 0, "count": 0, "error": str(e)})

    def send_cancel(self, query):
        item_id = query.get("id", "")
        if item_id and self.download_manager.cancel(item_id):
            self.send_json({"success": True})
        else:
            self.send_json({"success": False, "error": "未找到任务"}, 404)

    def send_remove(self, query):
        item_id = query.get("id", "")
        if item_id and self.download_manager.remove(item_id):
            self.send_json({"success": True})
        else:
            self.send_json({"success": False, "error": "未找到任务"}, 404)

    def send_pan_status(self):
        """返回所有网盘的登录状态"""
        try:
            from cloud_disk_dl import CloudDiskDL
            cdd = CloudDiskDL()
            self.send_json({"success": True, "status": cdd.login_status_all()})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)})
    
    def send_dl_urls(self, query):
        """更新媒体下载链接"""
        media_id = query.get("id", "")
        urls_str = query.get("urls", "")
        if not media_id:
            self.send_json({"success": False, "error": "缺少媒体ID"}, 400)
            return
        urls = [u.strip() for u in urls_str.split(",") if u.strip()]
        dl_enhancer.update_download_urls(media_id, urls)
        self.send_json({"success": True, "count": len(urls)})
    
    def _build_html(self):
        return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>流媒体中心 - 豆瓣7分精选</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg-primary: #0f0f1a;
            --bg-secondary: #1a1a2e;
            --bg-card: #16213e;
            --accent: #e94560;
            --accent-hover: #ff6b6b;
            --text-primary: #ffffff;
            --text-secondary: #a0a0b0;
            --rating-high: #00d26a;
            --rating-mid: #ffc107;
            --border-radius: 12px;
            --shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }
        /* Header */
        .header {
            background: linear-gradient(135deg, var(--bg-secondary) 0%, #0f0f1a 100%);
            padding: 20px 40px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(20px);
        }
        .logo { display: flex; align-items: center; gap: 12px; }
        .logo i { font-size: 2rem; color: var(--accent); }
        .logo h1 { font-size: 1.5rem; font-weight: 700; }
        .logo .badge {
            background: var(--rating-high);
            color: #000;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        .search-bar {
            display: flex;
            gap: 10px;
            flex: 1;
            max-width: 600px;
            margin: 0 40px;
        }
        .search-input {
            flex: 1;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: var(--border-radius);
            padding: 12px 20px;
            color: #fff;
            font-size: 1rem;
            transition: all 0.3s;
        }
        .search-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 20px rgba(233,69,96,0.3);
        }
        .search-btn {
            background: var(--accent);
            border: none;
            border-radius: var(--border-radius);
            padding: 12px 24px;
            color: #fff;
            cursor: pointer;
            transition: all 0.3s;
        }
        .search-btn:hover { background: var(--accent-hover); transform: translateY(-2px); }
        /* Main */
        .main { padding: 30px 40px; max-width: 1600px; margin: 0 auto; }
        .tabs { display: flex; gap: 10px; margin-bottom: 30px; }
        .tab {
            padding: 10px 24px;
            background: var(--bg-card);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: var(--border-radius);
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.3s;
        }
        .tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
        .tab:hover:not(.active) { border-color: var(--accent); }
        .section { display: none; }
        .section.active { display: block; }
        .section-title {
            font-size: 1.25rem;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .section-title i { color: var(--accent); }
        /* Video Grid */
        .video-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 24px;
        }
        .video-card {
            background: var(--bg-card);
            border-radius: var(--border-radius);
            overflow: hidden;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            cursor: pointer;
            position: relative;
        }
        .video-card:hover {
            transform: translateY(-8px) scale(1.02);
            box-shadow: var(--shadow), 0 0 40px rgba(233,69,96,0.3);
        }
        .card-poster {
            position: relative;
            aspect-ratio: 2/3;
            overflow: hidden;
        }
        .card-poster img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.4s;
        }
        .video-card:hover .card-poster img { transform: scale(1.1); }
        .card-rating {
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(0,0,0,0.75);
            padding: 4px 10px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 700;
            color: #ffc107;
            z-index: 2;
            backdrop-filter: blur(4px);
            cursor: pointer;
            transition: background 0.2s;
        }
        .card-rating:hover { background: rgba(255,193,7,0.3); }
        .card-overlay {
            position: absolute;
            inset: 0;
            background: linear-gradient(to top, rgba(0,0,0,0.9), transparent);
            opacity: 0;
            transition: opacity 0.3s;
            display: flex;
            align-items: flex-end;
            padding: 15px;
        }
        .video-card:hover .card-overlay { opacity: 1; }
        .play-btn {
            width: 48px;
            height: 48px;
            background: var(--accent);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
            font-size: 1.25rem;
            box-shadow: 0 4px 20px rgba(233,69,96,0.5);
        }
        .card-info { padding: 15px; }
        .card-title {
            font-size: 0.95rem;
            font-weight: 600;
            margin-bottom: 8px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .card-meta {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.8rem;
            color: var(--text-secondary);
        }
        .rating-badge {
            padding: 2px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.75rem;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        .rating-badge:hover { opacity: 0.8; }
        .rating-high { background: var(--rating-high); color: #000; }
        .rating-mid { background: var(--rating-mid); color: #000; }
        .card-actions { display: flex; gap: 8px; margin-top: 12px; }
        .action-btn {
            flex: 1;
            padding: 8px;
            border: none;
            border-radius: 6px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
        }
        .btn-play { background: var(--accent); color: #fff; }
        .btn-play:hover { background: var(--accent-hover); }
        .btn-download {
            background: rgba(255,255,255,0.1);
            color: var(--text-secondary);
        }
        .btn-download:hover { background: rgba(255,255,255,0.2); color: #fff; }
        .btn-add-library { background: #28a745; color: #fff; }
        .btn-add-library:hover { background: #34ce57; }
        .btn-add-library:disabled { opacity: 0.6; cursor: not-allowed; }
        /* 收藏按钮 */
        .btn-favorite { flex: 0 0 auto; width: 36px; padding: 8px; background: rgba(255,255,255,0.05); color: var(--text-secondary); border: 1px solid rgba(255,255,255,0.1); }
        .btn-favorite:hover { background: rgba(233,69,96,0.2); color: var(--accent); border-color: var(--accent); }
        .btn-favorite.fav-active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .rating-low { background: rgba(255,255,255,0.15); color: var(--text-secondary); }
        /* Search Results */
        .search-results {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 20px;
        }
        .search-item {
            display: flex;
            gap: 15px;
            background: var(--bg-card);
            border-radius: var(--border-radius);
            padding: 15px;
            align-items: flex-start;
        }
        .search-item img {
            width: 80px;
            height: 120px;
            object-fit: cover;
            border-radius: 6px;
            flex-shrink: 0;
        }
        .search-item-info { flex: 1; }
        .search-item-title { font-weight: 600; margin-bottom: 5px; }
        .search-item-meta { font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 10px; }
        /* Queue */
        .queue-list { display: flex; flex-direction: column; gap: 12px; }
        .queue-item {
            display: flex;
            align-items: center;
            gap: 15px;
            background: var(--bg-card);
            border-radius: var(--border-radius);
            padding: 15px 20px;
        }
        .queue-icon {
            width: 40px;
            height: 40px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.25rem;
        }
        .queue-icon.pending { background: rgba(255,255,255,0.1); color: var(--text-secondary); }
        .queue-icon.downloading { background: var(--accent); color: #fff; animation: pulse 1s infinite; }
        .queue-icon.completed { background: var(--rating-high); color: #000; }
        .queue-icon.failed { background: #ff4757; color: #fff; }
        .queue-icon.cancelled { background: rgba(255,255,255,0.1); color: var(--text-secondary); }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .queue-info { flex: 1; }
        .queue-title { font-weight: 600; margin-bottom: 5px; }
        .queue-progress {
            height: 4px;
            background: rgba(255,255,255,0.1);
            border-radius: 2px;
            overflow: hidden;
            margin-top: 8px;
        }
        .queue-progress-bar {
            height: 100%;
            background: var(--accent);
            transition: width 0.3s;
        }
        .queue-status { font-size: 0.8rem; color: var(--text-secondary); }
        .btn-cancel {
            background: rgba(255,71,87,0.2);
            color: #ff4757;
            border: none;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.8rem;
        }
        .btn-delete {
            background: rgba(255,255,255,0.05);
            color: var(--text-secondary);
            border: 1px solid rgba(255,255,255,0.1);
            padding: 6px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.8rem;
            transition: all 0.2s;
        }
        .btn-delete:hover {
            background: rgba(255,71,87,0.2);
            color: #ff4757;
            border-color: rgba(255,71,87,0.3);
        }
        /* Player Modal */
        .modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.95);
            z-index: 1000;
            flex-direction: column;
        }
        .modal.active { display: flex; }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 20px;
            background: rgba(0,0,0,0.5);
        }
        .modal-title { font-size: 1.1rem; }
        .modal-close {
            background: none;
            border: none;
            color: #fff;
            font-size: 1.5rem;
            cursor: pointer;
            padding: 5px;
        }
        .modal-body {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            padding: 20px;
            overflow-y: auto;
            max-height: calc(100vh - 60px);
        }
        .modal-body video {
            max-width: 100%;
            max-height: 85vh;
            background: #000;
            border-radius: 8px;
        }
        /* Empty State */
        .empty-state {
            text-align: center;
            padding: 80px 20px;
            color: var(--text-secondary);
        }
        .empty-state i { font-size: 4rem; margin-bottom: 20px; opacity: 0.5; }
        .empty-state h2 { margin-bottom: 10px; color: var(--text-primary); }
        /* Toast */
        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 15px 25px;
            background: var(--bg-card);
            border-radius: var(--border-radius);
            box-shadow: var(--shadow);
            display: none;
            align-items: center;
            gap: 10px;
            z-index: 2000;
            animation: slideIn 0.3s;
        }
        .toast.show { display: flex; }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        .toast-success { border-left: 4px solid var(--rating-high); }
        .toast-error { border-left: 4px solid #ff4757; }
        .toast-info { border-left: 4px solid var(--accent); }
        /* === 下载增强样式 === */
        .downloaded-badge {
            position: absolute;
            top: 10px;
            left: 10px;
            background: var(--rating-high);
            color: #000;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.7rem;
            font-weight: 700;
            z-index: 2;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .downloaded-badge i { font-size: 0.65rem; }
        .queue-subtabs {
            display: flex; gap: 8px; margin-bottom: 20px;
        }
        .queue-subtab {
            padding: 8px 18px;
            background: var(--bg-card);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 0.85rem;
            transition: all 0.2s;
        }
        .queue-subtab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
        .dl-url-input {
            width: 100%;
            padding: 14px 18px;
            background: rgba(255,255,255,0.05);
            border: 2px dashed rgba(255,255,255,0.2);
            border-radius: var(--border-radius);
            color: #fff;
            font-size: 0.95rem;
            outline: none;
            transition: all 0.3s;
            margin-bottom: 12px;
        }
        .dl-url-input:focus { border-color: var(--accent); border-style: solid; }
        .batch-dl-bar {
            display: flex; gap: 10px; align-items: center; padding: 12px 20px;
            background: linear-gradient(135deg, var(--accent), #c0392b);
            border-radius: var(--border-radius);
            margin-bottom: 15px;
        }
        .batch-dl-bar i { font-size: 1.2rem; }
        .batch-dl-bar span { flex: 1; font-weight: 600; }
        .batch-dl-btn {
            background: rgba(255,255,255,0.2);
            border: 1px solid rgba(255,255,255,0.3);
            padding: 8px 20px;
            border-radius: 8px;
            color: #fff;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
        }
        .batch-dl-btn:hover { background: rgba(255,255,255,0.3); }
        .dl-count-badge {
            display: inline-flex; align-items: center; gap: 5px;
            background: var(--rating-high); color: #000;
            padding: 2px 10px; border-radius: 12px;
            font-size: 0.75rem; font-weight: 600;
            margin-top: 6px;
        }
        /* Enhanced download completed item */
        .completed-file {
            display: flex; align-items: center; gap: 12px;
            background: var(--bg-card); border-radius: var(--border-radius);
            padding: 12px 16px;
        }
        .completed-file i { color: var(--rating-high); font-size: 1.2rem; }
        .completed-file .info { flex: 1; }
        .completed-file .name { font-weight: 500; font-size: 0.9rem; }
        .completed-file .meta { font-size: 0.75rem; color: var(--text-secondary); }
        /* Responsive */
        @media (max-width: 768px) {
            .header { padding: 15px 20px; flex-wrap: wrap; gap: 15px; }
            .search-bar { margin: 0; order: 3; width: 100%; }
            .main { padding: 20px; }
            .video-grid { grid-template-columns: repeat(2, 1fr); gap: 15px; }
        }
        /* Category Filters */
        .category-filters {
            display: flex;
            gap: 12px;
            padding: 20px 40px;
            max-width: 1600px;
            margin: 0 auto;
        }
        .cat-filter {
            padding: 8px 24px;
            background: var(--bg-card);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 24px;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.3s;
            font-size: 0.9rem;
            user-select: none;
        }
        .cat-filter:hover { border-color: var(--accent); }
        .cat-filter.active { background: var(--accent); border-color: var(--accent); color: #fff; }
        .count-badge {
            background: rgba(255,255,255,0.08);
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-left: 8px;
        }
        .cat-section { margin-bottom: 40px; }
        .cat-section:last-child { margin-bottom: 0; }
        .cat-section.hidden { display: none; }
        /* Episode Modal */
        .episode-modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.9);
            z-index: 900;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start;
            padding: 40px 20px;
            overflow-y: auto;
        }
        .episode-modal.active { display: flex; }
        .episode-panel {
            background: var(--bg-secondary);
            border-radius: var(--border-radius);
            max-width: 800px;
            width: 100%;
            margin-top: 20px;
            max-height: 85vh;
            overflow-y: auto;
            box-shadow: var(--shadow);
        }
        .episode-header {
            display: flex;
            align-items: center;
            gap: 15px;
            padding: 20px 25px;
            background: var(--bg-card);
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .episode-header img {
            width: 60px;
            height: 90px;
            object-fit: cover;
            border-radius: 6px;
        }
        .episode-header-info { flex: 1; }
        .episode-header-title { font-size: 1.2rem; font-weight: 700; margin-bottom: 4px; }
        .episode-header-meta { font-size: 0.85rem; color: var(--text-secondary); }
        .episode-close {
            background: none;
            border: none;
            color: #fff;
            font-size: 1.5rem;
            cursor: pointer;
            padding: 5px;
            opacity: 0.6;
            transition: opacity 0.2s;
        }
        .episode-close:hover { opacity: 1; }
        .season-tabs {
            display: flex;
            gap: 8px;
            padding: 15px 25px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            flex-wrap: wrap;
        }
        .season-tab {
            padding: 6px 16px;
            border-radius: 16px;
            background: rgba(255,255,255,0.05);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 0.85rem;
            border: 1px solid transparent;
            transition: all 0.2s;
        }
        .season-tab:hover { border-color: var(--accent); }
        .season-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .episode-grid {
            display: none;
            padding: 15px 25px 25px;
            animation: episodeFadeIn 0.3s ease-out;
        }
        .episode-grid.active { display: block; }
        @keyframes episodeFadeIn {
            from { opacity: 0; transform: translateY(12px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        .episode-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 15px;
            border-radius: 8px;
            transition: background 0.2s;
            cursor: pointer;
        }
        .episode-item:hover { background: rgba(255,255,255,0.05); }
        .episode-num {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: var(--bg-card);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.85rem;
            font-weight: 600;
            flex-shrink: 0;
        }
        .episode-label {
            flex: 1;
            margin-left: 12px;
            font-size: 0.9rem;
        }
        .episode-dl-btn {
            padding: 6px 14px;
            background: rgba(233,69,96,0.15);
            border: 1px solid rgba(233,69,96,0.3);
            border-radius: 6px;
            color: var(--accent);
            cursor: pointer;
            font-size: 0.8rem;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .episode-dl-btn:hover { background: var(--accent); color: #fff; }
        .episode-url-input {
            display: none;
            gap: 8px;
            align-items: center;
        }
        .episode-url-input.show { display: flex; }
        .episode-url-input input {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 6px;
            padding: 6px 12px;
            color: #fff;
            font-size: 0.8rem;
            width: 280px;
        }
        .episode-url-input input:focus { outline: none; border-color: var(--accent); }
        .episode-confirm-btn {
            padding: 6px 12px;
            background: var(--accent);
            border: none;
            border-radius: 6px;
            color: #fff;
            cursor: pointer;
            font-size: 0.8rem;
        }
        .btn-episodes {
            background: rgba(0,210,106,0.15);
            color: var(--rating-high);
            border: 1px solid rgba(0,210,106,0.3);
            flex: 1;
            padding: 8px;
            border-radius: 6px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
        }
        .btn-episodes:hover { background: var(--rating-high); color: #000; }
    </style>
</head>
<body>
    <!-- Header -->
    <header class="header">
        <div class="logo">
            <i class="fas fa-play-circle"></i>
            <h1>流媒体中心</h1>
            <span class="badge">豆瓣7分+</span>
        </div>
        <div class="search-bar">
            <input type="text" class="search-input" id="searchInput" placeholder="搜索电影/电视剧...">
            <button class="search-btn" onclick="search()">
                <i class="fas fa-search"></i>
            </button>
        </div>
    </header>
    
    <!-- Main Content -->
    <main class="main">
        <div class="tabs">
            <div class="tab active" data-tab="home" onclick="switchTab('home')">
                <i class="fas fa-home"></i> 首页
            </div>
            <div class="tab" data-tab="search" onclick="switchTab('search')">
                <i class="fas fa-search"></i> 搜索结果
            </div>
            <div class="tab" data-tab="library" onclick="switchTab('library')">
                <i class="fas fa-video"></i> 我的视频
            </div>
            <div class="tab" data-tab="queue" onclick="switchTab('queue')">
                <i class="fas fa-download"></i> 下载队列
            </div>
            <div class="tab" data-tab="dlmanage" onclick="switchTab('dlmanage')">
                <i class="fas fa-cog"></i> 下载管理
            </div>
            <div class="tab" data-tab="favorites" onclick="switchTab('favorites')">
                <i class="fas fa-heart"></i> 收藏
            </div>
        </div>
        
        <!-- Category Filter -->
        <div class="category-filters" id="categoryFilters">
            <span class="cat-filter active" data-cat="all" onclick="filterCategory('all')">全部</span>
            <span class="cat-filter" data-cat="电影" onclick="filterCategory('电影')">电影</span>
            <span class="cat-filter" data-cat="电视剧" onclick="filterCategory('电视剧')">电视剧</span>
        </div>

        <!-- Sort Bar -->
        <div class="category-filters" id="sortBar">
            <span style="color:var(--text-secondary);font-size:0.85rem;margin-right:8px;"><i class="fas fa-sort"></i> 排序:</span>
            <span class="cat-filter active" data-sort="default" onclick="sortItems('default', this)">默认</span>
            <span class="cat-filter" data-sort="rating" onclick="sortItems('rating', this)">评分高→低</span>
            <span class="cat-filter" data-sort="rating_asc" onclick="sortItems('rating_asc', this)">评分低→高</span>
            <span class="cat-filter" data-sort="year" onclick="sortItems('year', this)">年份新→旧</span>
            <span class="cat-filter" data-sort="name" onclick="sortItems('name', this)">名称A→Z</span>
        </div>

        <!-- Home Section -->
        <section id="home" class="section active">
            <!-- Movie Section -->
            <div class="cat-section" data-cat="电影">
                <h2 class="section-title"><i class="fas fa-film"></i> 电影 <span class="count-badge" id="movieCount"></span></h2>
                <div class="video-grid" id="movieGrid"></div>
            </div>
            <!-- TV Section -->
            <div class="cat-section" data-cat="电视剧">
                <h2 class="section-title"><i class="fas fa-tv"></i> 电视剧 <span class="count-badge" id="tvCount"></span></h2>
                <div class="video-grid" id="tvGrid"></div>
            </div>
        </section>
        
        <!-- Search Section -->
        <section id="search" class="section">
            <h2 class="section-title"><i class="fas fa-search"></i> 搜索结果</h2>
            <div class="search-results" id="searchResults"></div>
        </section>
        
        <!-- Library Section -->
        <section id="library" class="section">
            <h2 class="section-title"><i class="fas fa-video"></i> 我的视频 (<span id="videoCount">0</span>)</h2>
            <div class="video-grid" id="libraryGrid"></div>
        </section>
        
        <!-- Queue Section -->
        <section id="queue" class="section">
            <h2 class="section-title"><i class="fas fa-download"></i> 下载队列</h2>
            <div class="queue-list" id="queueList"></div>
        </section>
        
        <!-- Download Management Section -->
        <section id="dlmanage" class="section">
            <h2 class="section-title"><i class="fas fa-cog"></i> 下载管理</h2>
            
            <div class="queue-subtabs">
                <span class="queue-subtab active" onclick="switchDlSubTab('progress', this)"><i class="fas fa-spinner"></i> 进行中</span>
                <span class="queue-subtab" onclick="switchDlSubTab('completed', this)"><i class="fas fa-check-circle"></i> 已完成</span>
                <span class="queue-subtab" onclick="switchDlSubTab('url', this)"><i class="fas fa-link"></i> URL下载</span>
            </div>
            
            <!-- 进行中 -->
            <div id="dlProgressContent">
                <div class="queue-list" id="dlProgressList"></div>
            </div>
            
            <!-- 已完成 -->
            <div id="dlCompletedContent" style="display:none;">
                <div class="queue-list" id="dlCompletedList"></div>
            </div>
            
            <!-- URL下载 -->
            <div id="dlUrlContent" style="display:none;">
                <div style="background:var(--bg-card);border-radius:var(--border-radius);padding:25px;max-width:600px;">
                    <h3 style="margin-bottom:15px;"><i class="fas fa-youtube"></i> 从外部URL下载</h3>
                    <p style="color:var(--text-secondary);margin-bottom:15px;font-size:0.9rem;">
                        支持B站、YouTube、抖音等平台的视频链接。粘贴URL后自动解析下载。
                    </p>
                    <input type="text" id="dlUrlInput" class="dl-url-input" placeholder="粘贴视频URL（B站/YouTube等）...">
                    <input type="text" id="dlUrlTitle" class="search-input" placeholder="视频标题（可选）" style="width:100%;margin-bottom:12px;">
                    <button class="action-btn btn-play" onclick="startUrlDownload()" style="padding:12px 30px;width:100%;">
                        <i class="fas fa-download"></i> 开始下载
                    </button>
                    <div id="urlDlStatus" style="margin-top:12px;color:var(--text-secondary);font-size:0.85rem;"></div>
                </div>
            </div>
        </section>

        <!-- Favorites Section -->
        <section id="favorites" class="section">
            <h2 class="section-title"><i class="fas fa-heart"></i> 我的收藏 (<span id="favCount">0</span>)</h2>
            <div class="video-grid" id="favoritesGrid"></div>
        </section>
    </main>
    
    <!-- Player Modal -->
    <div class="modal" id="playerModal">
        <div class="modal-header">
            <span class="modal-title" id="playerTitle">正在播放</span>
            <button class="modal-close" onclick="closePlayer()">
                <i class="fas fa-times"></i>
            </button>
        </div>
        <div class="modal-body">
            <video id="player" controls autoplay playsinline preload="metadata">
                <source src="" type="video/mp4">
                您的浏览器不支持视频播放
            </video>
        </div>
    </div>
    
    <!-- Download Modal -->
    <div class="modal" id="downloadModal">
        <div class="modal-header">
            <span class="modal-title">添加下载</span>
            <button class="modal-close" onclick="closeDownloadModal()">
                <i class="fas fa-times"></i>
            </button>
        </div>
        <div class="modal-body" style="align-items: flex-start; padding: 20px;">
            <div style="background: var(--bg-card); border-radius: var(--border-radius); padding: 25px; max-width: 620px; width: 100%;">
                <h3 style="margin-bottom: 20px;" id="downloadTitle">下载</h3>
                <div style="display: flex; flex-direction: column; gap: 15px;">
                    <div>
                        <label style="display: block; margin-bottom: 5px; color: var(--text-secondary);">视频URL</label>
                        <input type="text" id="downloadUrl" class="search-input" placeholder="https://.../video.mp4" style="width: 100%;">
                    </div>
                    <div>
                        <label style="display: block; margin-bottom: 5px; color: var(--text-secondary);">文件名</label>
                        <input type="text" id="downloadFilename" class="search-input" placeholder="自定义文件名.mp4">
                    </div>
                    <div style="display: flex; gap: 10px; margin-top: 10px;">
                        <button class="action-btn btn-play" onclick="confirmDownload()" style="flex: 1;">
                            <i class="fas fa-download"></i> 开始下载
                        </button>
                        <button class="action-btn btn-download" onclick="closeDownloadModal()" style="flex: 1;">
                            取消
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Episode Modal (for TV shows) -->
    <div class="episode-modal" id="episodeModal">
        <div class="episode-panel" id="episodePanel">
            <div class="episode-header">
                <img id="episodePoster" src="" alt="">
                <div class="episode-header-info">
                    <div class="episode-header-title" id="episodeTitle"></div>
                    <div class="episode-header-meta" id="episodeMeta"></div>
                </div>
                <button class="episode-close" onclick="closeEpisodes()"><i class="fas fa-times"></i></button>
            </div>
            <div class="season-tabs" id="seasonTabs"></div>
            <!-- Batch download bar -->
            <div class="batch-dl-bar">
                <i class="fas fa-cloud-download-alt"></i>
                <span id="batchDlLabel">一键下载全部剧集</span>
                <button class="batch-dl-btn" onclick="batchDownloadAll()">
                    <i class="fas fa-download"></i> 下载全部
                </button>
            </div>
            <div id="episodeContent"></div>
        </div>
    </div>
    
    <!-- Toast -->
    <div class="toast" id="toast"></div>
    
    <script>
        // State
        let currentTab = 'home';
        let currentDownload = null;
        let allItems = {}; // id -> item lookup
        let favorites = new Set(JSON.parse(localStorage.getItem('streaming_favorites') || '[]'));
        let currentSort = 'default';
        let currentCategory = 'all';
        let allDatabaseItems = []; // 存储全部数据供排序/筛选使用
        let lastDbMeta = null; // 数据库元信息，用于热更新检测

        // === 数据库热更新检测 ===
        function pollDbMeta() {
            fetch('/api/db-meta')
                .then(r => r.json())
                .then(meta => {
                    if (lastDbMeta && meta.mtime !== lastDbMeta.mtime) {
                        showToast('数据库已更新，自动重新加载...', 'success');
                        loadDatabase();
                        loadLibrary();
                    }
                    lastDbMeta = meta;
                })
                .catch(() => {});
        }

        // === 收藏功能 ===
        function toggleFavorite(id, title, event) {
            event.stopPropagation();
            if (favorites.has(id)) {
                favorites.delete(id);
                showToast(`已取消收藏: ${title}`, 'info');
            } else {
                favorites.add(id);
                showToast(`已收藏: ${title}`, 'success');
            }
            localStorage.setItem('streaming_favorites', JSON.stringify([...favorites]));
            // 刷新当前视图的收藏按钮状态
            refreshFavoriteButtons();
            if (currentTab === 'favorites') renderFavoritesGrid();
        }

        function refreshFavoriteButtons() {
            document.querySelectorAll('[data-fav-id]').forEach(btn => {
                const id = btn.getAttribute('data-fav-id');
                btn.classList.toggle('fav-active', favorites.has(id));
            });
        }

        function renderFavoritesGrid() {
            const container = document.getElementById('favoritesGrid');
            const favItems = Object.values(allItems).filter(item => favorites.has(item.id));
            document.getElementById('favCount').textContent = favItems.length;

            if (favItems.length === 0) {
                container.innerHTML = '<div class="empty-state"><i class="fas fa-heart"></i><h2>还没有收藏</h2><p style="color:var(--text-secondary)">点击卡片上的爱心图标收藏你喜欢的作品</p></div>';
                return;
            }
            // 复用renderMovieGrid的逻辑
            renderMovieGrid('favoritesGrid', favItems);
        }

        // === 排序/筛选功能 ===
        function sortItems(sortType, el) {
            currentSort = sortType;
            // 更新sort bar激活状态
            document.querySelectorAll('#sortBar .cat-filter').forEach(t => t.classList.remove('active'));
            if (el) el.classList.add('active');
            reRenderHome();
        }

        function filterCategory(cat) {
            currentCategory = cat;
            document.querySelectorAll('#categoryFilters .cat-filter').forEach(t => t.classList.remove('active'));
            document.querySelector(`#categoryFilters [data-cat="${cat}"]`).classList.add('active');
            reRenderHome();
        }

        function reRenderHome() {
            const movies = allDatabaseItems.filter(m => m.type === '电影');
            const tvShows = allDatabaseItems.filter(m => m.type === '电视剧');

            // 排序
            const sorted = applySort([...movies]);
            const sortedTv = applySort([...tvShows]);

            document.getElementById('movieCount').textContent = `${sorted.length}部`;
            document.getElementById('tvCount').textContent = `${sortedTv.length}部`;

            // 显示/隐藏分类 — 只渲染当前分类，另一个grid清空
            document.querySelectorAll('.cat-section').forEach(sec => {
                const sectionCat = sec.getAttribute('data-cat');
                if (currentCategory === 'all' || currentCategory === sectionCat) {
                    sec.classList.remove('hidden');
                    sec.style.display = '';
                } else {
                    sec.classList.add('hidden');
                    sec.style.display = 'none';
                }
            });

            // 获取本地文件信息
            fetch('/api/local-files').then(r=>r.json()).then(localData => {
                const localNames = new Set((localData.files||[]).map(f => f.name.toLowerCase()));
                // 按当前分类仅渲染对应grid，非当前分类的grid清空
                if (currentCategory === 'all' || currentCategory === '电影') {
                    _renderCards('movieGrid', sorted, localNames);
                } else {
                    document.getElementById('movieGrid').innerHTML = '';
                }
                if (currentCategory === 'all' || currentCategory === '电视剧') {
                    _renderCards('tvGrid', sortedTv, localNames);
                } else {
                    document.getElementById('tvGrid').innerHTML = '';
                }
            }).catch(() => {
                if (currentCategory === 'all' || currentCategory === '电影') {
                    _renderCards('movieGrid', sorted, new Set());
                } else {
                    document.getElementById('movieGrid').innerHTML = '';
                }
                if (currentCategory === 'all' || currentCategory === '电视剧') {
                    _renderCards('tvGrid', sortedTv, new Set());
                } else {
                    document.getElementById('tvGrid').innerHTML = '';
                }
            });
        }

        function applySort(items) {
            switch (currentSort) {
                case 'rating': return items.sort((a, b) => (b.rating || 0) - (a.rating || 0));
                case 'rating_asc': return items.sort((a, b) => (a.rating || 0) - (b.rating || 0));
                case 'year': return items.sort((a, b) => (b.year || 0) - (a.year || 0));
                case 'name': return items.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'));
                default: return items; // 默认不排序
            }
        }
        
        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            loadDatabase();
            loadLibrary();
            refreshQueue();
            setInterval(refreshQueue, 5000);
            setInterval(refreshDlManage, 5000);

            // 数据库热更新检测（每30秒）
            pollDbMeta();
            setInterval(pollDbMeta, 30000);

            document.getElementById('searchInput').addEventListener('keypress', (e) => {
                if (e.key === 'Enter') search();
            });
        });
        
        // Tab switching
        function switchTab(tab) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            
            document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
            document.getElementById(tab).classList.add('active');
            currentTab = tab;
            
            if (tab === 'library') loadLibrary();
            if (tab === 'queue') refreshQueue();
            if (tab === 'dlmanage') refreshDlManage();
            if (tab === 'favorites') renderFavoritesGrid();
        }
        
        // Queue sub-tab switching
        function switchDlSubTab(tab, el) {
            document.querySelectorAll('.queue-subtab').forEach(t => t.classList.remove('active'));
            el.classList.add('active');
            ['dlProgressContent','dlCompletedContent','dlUrlContent'].forEach(id => {
                document.getElementById(id).style.display = id === `dl${tab.charAt(0).toUpperCase()+tab.slice(1)}Content` ? 'block' : 'none';
            });
            if (tab === 'progress' || tab === 'completed') refreshDlManage();
        }
        
        // Load database (home page)
        async function loadDatabase(retryCount = 3) {
            for (let attempt = 1; attempt <= retryCount; attempt++) {
                try {
                    const res = await fetch('/api/database');
                    const allMovies = await res.json();

                    // Store for sorting/filtering
                    allDatabaseItems = allMovies;

                    const movies = allMovies.filter(m => m.type === '电影');
                    const tvShows = allMovies.filter(m => m.type === '电视剧');

                    document.getElementById('movieCount').textContent = `${movies.length}部`;
                    document.getElementById('tvCount').textContent = `${tvShows.length}部`;

                    // Store all items for lookup
                    allItems = {};
                    allMovies.forEach(item => { allItems[item.id] = item; });

                    // If no movies or no TV shows, hide the empty section
                    if (movies.length === 0) {
                        document.querySelector('.cat-section[data-cat="电影"]').style.display = 'none';
                    }
                    if (tvShows.length === 0) {
                        document.querySelector('.cat-section[data-cat="电视剧"]').style.display = 'none';
                    }

                    // 统一通过reRenderHome渲染（带排序+筛选），避免竞态条件
                    reRenderHome();
                    return; // 成功就退出
                } catch (e) {
                    console.error(`loadDatabase attempt ${attempt}/${retryCount} failed:`, e);
                    if (attempt < retryCount) {
                        await new Promise(r => setTimeout(r, 1000)); // 等1秒再试
                    }
                }
            }
            // 所有重试都失败后，在grid中显示重试按钮
            document.getElementById('movieGrid').innerHTML =
                `<div class="empty-state"><i class="fas fa-exclamation-triangle"></i><h2>加载失败</h2><p>无法加载片库，请检查服务状态</p><button onclick="loadDatabase()" style="margin-top:12px;padding:8px 24px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer">重新加载</button></div>`;
        }

        // renderMovieGrid now delegates to _renderCards (for backward compat)
        function renderMovieGrid(gridId, items) {
            _renderCards(gridId, items, new Set());
        }

        // Top-level card renderer
        function _renderCards(gridId, items, localNames) {
            const container = document.getElementById(gridId);
            if (!items || items.length === 0) {
                container.innerHTML = `<div class="empty-state"><i class="fas fa-database"></i><h2>暂无内容</h2></div>`;
                return;
            }

            container.innerHTML = items.map(m => {
                const isDownloaded = localNames.has(m.title.toLowerCase() + '.mp4') ||
                                     [...localNames].some(n => n.includes(m.title.toLowerCase()) && !n.endsWith('.part'));
                const dlCount = m.type === '电视剧' && m.episodes ?
                    [...localNames].filter(n => n.includes(m.title.toLowerCase())).length : 0;

                return `
            <div class="video-card">
                <div class="card-poster">
                    <img src="/posters/${m.id}.jpg?v=3"
                         alt="${m.title}" loading="lazy">
                    ${isDownloaded ? `<div class="downloaded-badge"><i class="fas fa-check"></i> 已下载</div>` : ''}
                    <div class="card-overlay">
                        ${m.type === '电视剧' ? `
                        <div class="play-btn" onclick="event.stopPropagation(); showEpisodes('${m.id}')">
                            <i class="fas fa-list"></i>
                        </div>
                        ` : `
                        <div class="play-btn" onclick="event.stopPropagation(); downloadAndPlay('${m.id}', '${m.title}', '${m.episodes || 0}', event)">
                            <i class="fas fa-play"></i>
                        </div>
                        `}
                    </div>
                    <span class="card-rating" onclick="openDouban('${m.title}', '${m.year || ''}')">${m.rating.toFixed(1)}</span>
                </div>
                <div class="card-info">
                    <div class="card-title">${m.title}</div>
                    <div class="card-meta">
                        <span class="rating-badge ${m.rating >= 8 ? 'rating-high' : 'rating-mid'}" onclick="openDouban('${m.title}', '${m.year || ''}')">${m.rating.toFixed(1)}分</span>
                        <span>${m.year || ''}</span>
                        ${m.episodes ? `<span>${m.episodes}集</span>` : ''}
                        ${m.genre ? `<span>${m.genre.split('/')[0]}</span>` : ''}
                    </div>
                    ${dlCount > 0 ? `<div class="dl-count-badge"><i class="fas fa-check"></i> 已存${dlCount}集</div>` : ''}
                    <div class="card-actions">
                        <button class="action-btn btn-favorite ${favorites.has(m.id) ? 'fav-active' : ''}" data-fav-id="${m.id}" onclick="toggleFavorite('${m.id}', '${m.title.replace(/'/g, "\\'")}', event)">
                            <i class="fas fa-heart"></i>
                        </button>
                        ${m.type === '电视剧' ? `
                        <button class="btn-episodes" onclick="event.stopPropagation(); showEpisodes('${m.id}')">
                            <i class="fas fa-list"></i> 查看剧集
                        </button>
                        ` : `
                        <button class="action-btn btn-download" onclick="showDownloadModal('${m.id}', '${m.title}', '${m.episodes || 0}', event)">
                            <i class="fas fa-download"></i> 下载
                        </button>
                        `}
                    </div>
                </div>
            </div>
        `}).join('');
        }
        
        // Episode modal functions
        let currentEpisodeItemId = null;
        
        function showEpisodes(id) {
            const item = allItems[id];
            if (!item || !item.seasons) return;
            currentEpisodeItemId = id;
            
            // Set header
            document.getElementById('episodePoster').src = `/posters/${item.id}.jpg?v=3`;
            document.getElementById('episodeTitle').textContent = `${item.title}`;
            document.getElementById('episodeMeta').innerHTML = 
                `${item.year} · ${item.episodes}集 · ${item.genre || ''} · <span style="cursor:pointer;text-decoration:underline;text-decoration-style:dotted;" onclick="openDouban('${item.title}', '${item.year || ''}')">${item.rating.toFixed(1)}分</span>`;
            
            // Batch download label
            document.getElementById('batchDlLabel').textContent = `共${item.episodes}集 — 一键下载全部剧集`;
            
            // Build season tabs
            const seasonTabs = document.getElementById('seasonTabs');
            const epContent = document.getElementById('episodeContent');
            seasonTabs.innerHTML = '';
            epContent.innerHTML = '';
            
            item.seasons.forEach((s, idx) => {
                // Season tab
                const tab = document.createElement('div');
                tab.className = `season-tab${idx === 0 ? ' active' : ''}`;
                tab.textContent = item.seasons.length > 1 ? `第${s.season}季 (${s.episode_count}集)` : `共${s.episode_count}集`;
                tab.onclick = () => switchSeason(idx);
                seasonTabs.appendChild(tab);
                
                // Episode grid for this season
                const grid = document.createElement('div');
                grid.className = `episode-grid${idx === 0 ? ' active' : ''}`;
                grid.id = `epGrid_${idx}`;
                
                let epHtml = '';
                for (let ep = 1; ep <= s.episode_count; ep++) {
                    const epKey = `${item.id}_s${s.season}_e${ep}`;
                    epHtml += `
                        <div class="episode-item">
                            <div class="episode-num">${ep}</div>
                            <div class="episode-label">第${ep}集</div>
                            <div id="epUrl_${epKey}">
                                <button class="episode-dl-btn" onclick="dlEpisode('${item.id}', ${s.season}, ${ep})">
                                    <i class="fas fa-download"></i> 下载
                                </button>
                            </div>
                        </div>
                    `;
                }
                grid.innerHTML = epHtml;
                epContent.appendChild(grid);
            });
            
            document.getElementById('episodeModal').classList.add('active');
            document.body.style.overflow = 'hidden';
        }
        
        function switchSeason(idx) {
            document.querySelectorAll('.season-tab').forEach((t, i) => {
                t.classList.toggle('active', i === idx);
            });
            document.querySelectorAll('.episode-grid').forEach((g, i) => {
                g.classList.toggle('active', i === idx);
            });
        }
        
        function dlEpisode(id, season, episode) {
            const epKey = `${id}_s${season}_e${episode}`;
            const container = document.getElementById(`epUrl_${epKey}`);
            if (!container) return;
            
            container.innerHTML = `
                <div class="episode-url-input show">
                    <input type="text" id="epUrlInput_${epKey}" placeholder="输入视频URL..." style="width:240px;">
                    <button class="episode-confirm-btn" onclick="confirmEpisodeDl('${id}', ${season}, ${episode})"><i class="fas fa-check"></i></button>
                    <button class="episode-confirm-btn" style="background:#666;" onclick="cancelEpisodeDl('${epKey}')"><i class="fas fa-times"></i></button>
                </div>
            `;
            setTimeout(() => {
                const inp = document.getElementById(`epUrlInput_${epKey}`);
                if (inp) inp.focus();
            }, 100);
        }
        
        function cancelEpisodeDl(epKey) {
            const container = document.getElementById(`epUrl_${epKey}`);
            if (!container || !container.closest) return;
            const item = container.closest('.episode-item');
            if (!item) return;
            const ep = item.querySelector('.episode-num')?.textContent || '1';
            const id = container.closest('.episode-panel')?.querySelector('#episodeTitle')?.textContent || '';
            container.innerHTML = `
                <button class="episode-dl-btn" onclick="dlEpisode('${id}', 1, ${ep})">
                    <i class="fas fa-download"></i> 下载
                </button>
            `;
        }
        
        async function confirmEpisodeDl(id, season, episode) {
            const epKey = `${id}_s${season}_e${episode}`;
            const urlInput = document.getElementById(`epUrlInput_${epKey}`);
            if (!urlInput) return;
            const url = urlInput.value.trim();
            if (!url) {
                showToast('请输入视频URL', 'error');
                return;
            }
            
            const item = allItems[id];
            if (!item) return;
            
            const title = `${item.title} S${season}E${episode}`;
            const filename = `${item.title}.S${String(season).padStart(2,'0')}E${String(episode).padStart(2,'0')}.mp4`;
            
            const params = new URLSearchParams({
                title: title,
                url: url,
                episode: `${season}-${episode}`,
                filename: filename
            });
            
            try {
                const res = await fetch(`/api/download?${params}`);
                const result = await res.json();
                if (result.success) {
                    showToast(`已添加到下载队列: ${item.title} 第${season}季第${episode}集`, 'success');
                    // Reset the button
                    cancelEpisodeDl(epKey);
                } else {
                    showToast(`下载失败: ${result.error}`, 'error');
                }
            } catch (e) {
                showToast(`请求失败: ${e.message}`, 'error');
            }
        }
        
        function closeEpisodes() {
            document.getElementById('episodeModal').classList.remove('active');
            document.body.style.overflow = '';
        }
        
        // Search
        async function search() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) {
                showToast('请输入搜索内容', 'error');
                return;
            }
            
            switchTab('search');
            document.getElementById('searchResults').innerHTML = '<div class="empty-state"><i class="fas fa-spinner fa-spin"></i><h2>搜索中...</h2></div>';
            
            try {
                // Search local DB first
                const localRes = await fetch('/api/search', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                    body: 'q=' + encodeURIComponent(query)
                });
                const localResults = await localRes.json();
                
                // Also search external (豆瓣+TMDB)
                let externalResults = [];
                try {
                    const extRes = await fetch('/api/search-external?q=' + encodeURIComponent(query));
                    externalResults = await extRes.json();
                } catch (e) {
                    console.warn('外源搜索失败:', e);
                }
                
                // Merge: show local results first, then external (skip items already in local)
                const mergedResults = [...localResults];
                const localLowerTitles = new Set(localResults.map(m => m.title.toLowerCase()));
                for (const ext of externalResults) {
                    if (!localLowerTitles.has(ext.title.toLowerCase())) {
                        mergedResults.push(ext);
                    }
                }
                
                if (mergedResults.length === 0) {
                    document.getElementById('searchResults').innerHTML = 
                        '<div class="empty-state"><h2>未找到结果</h2><p>尝试其他关键词</p></div>';
                    return;
                }
                
                renderSearchResults(mergedResults);
            } catch (e) {
                document.getElementById('searchResults').innerHTML = 
                    `<div class="empty-state"><h2>请求失败</h2><p>${e.message}</p></div>`;
            }
        }
        
        function renderSearchResults(results) {
            const container = document.getElementById('searchResults');
            container.innerHTML = '<div class="video-grid">' + results.map(item => {
                const inLibrary = item._in_library;
                const posterUrl = inLibrary ? `/posters/${item.id}.jpg?v=3` : (item.poster ? proxyPoster(item.poster) : 'https://via.placeholder.com/200x300/16213e/ffffff?text=No+Poster');
                const ratingText = item.rating > 0 ? item.rating.toFixed(1) + '分' : '暂无评分';
                const ratingClass = item.rating >= 8 ? 'rating-high' : (item.rating > 0 ? 'rating-mid' : 'rating-low');
                
                return `
                <div class="video-card" onclick="event.stopPropagation();">
                    <div class="card-poster">
                        <img src="${posterUrl}" alt="${item.title}" onerror="this.src='https://via.placeholder.com/200x300/16213e/ffffff?text=${encodeURIComponent(item.title.substring(0, 4))}'">
                        <div class="card-overlay">
                            <div class="play-btn"><i class="fas fa-play"></i></div>
                        </div>
                        <div class="downloaded-badge">${inLibrary ? '<i class="fas fa-check"></i> 本地' : '<i class="fas fa-plus"></i> 新'}</div>
                        <span class="card-rating" onclick="event.stopPropagation(); openDouban('${item.title}', '${item.year || ''}')">${ratingText}</span>
                    </div>
                    <div class="card-info">
                        <div class="card-title">${item.title}</div>
                        <div class="card-meta">
                            <span class="rating-badge ${ratingClass}" onclick="event.stopPropagation(); openDouban('${item.title}', '${item.year || ''}')">${ratingText}</span>
                            <span>${item.year || ''}</span>
                            <span>${item.type || ''}</span>
                            ${item.episodes ? `<span>${item.episodes}集</span>` : ''}
                            ${item._source ? `<span>· ${item._source.toUpperCase()}</span>` : ''}
                        </div>
                        <div class="card-actions">
                            ${inLibrary ? (item.type === '电视剧' ? `
                            <button class="btn-episodes" onclick="event.stopPropagation(); showEpisodes('${item.id}')">
                                <i class="fas fa-list"></i> 查看剧集
                            </button>
                            ` : `
                            <button class="action-btn btn-play" onclick="event.stopPropagation(); downloadAndPlay('${item.id}', '${item.title}', '${item.episodes || 0}', event)">
                                <i class="fas fa-play"></i> 下载并播放
                            </button>
                            `) : `
                            <button class="action-btn btn-add-library" onclick="event.stopPropagation(); addToLibrary(this, '${item.title.replace(/'/g, "\\'")}', '${item.year || ''}', ${item.rating || 0}, '${item.type || '电影'}', '${(item.genre || '').replace(/'/g, "\\'")}', '${item.poster || ''}', '${item.douban_url || ''}', '${item.douban_id || ''}', ${item.episodes || 0})">
                                <i class="fas fa-plus"></i> 加入媒体库
                            </button>
                            <button class="action-btn btn-download" onclick="event.stopPropagation(); searchSourcesForTitle('${item.title.replace(/'/g, "\\'")}', '${item.year || ''}', '${item.type || '电影'}', event)">
                                <i class="fas fa-search"></i> 搜索资源
                            </button>
                            `}
                            <a href="javascript:void(0)" onclick="event.stopPropagation(); openDouban('${item.title}', '${item.year || ''}')" class="action-btn btn-download" style="text-decoration:none;">
                                <i class="fas fa-external-link-alt"></i> 豆瓣
                            </a>
                        </div>
                    </div>
                </div>`}).join('') + '</div>';
        }
        
        // Add an external search result to the media library
        async function addToLibrary(btn, title, year, rating, type, genre, poster, doubanUrl, doubanId, episodes) {
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 添加中...';
            btn.disabled = true;
            
            try {
                const res = await fetch('/api/add-to-library', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        title: title,
                        year: year,
                        rating: rating,
                        type: type,
                        genre: genre,
                        poster: poster,
                        douban_url: doubanUrl,
                        douban_id: doubanId,
                        episodes: episodes
                    })
                });
                const result = await res.json();
                if (result.success) {
                    showToast(`已加入媒体库: ${title}`, 'success');
                    // Reload the page data to include the new item
                    loadMovies();
                    // Replace the button with success message
                    btn.className = 'action-btn btn-play';
                    btn.innerHTML = '<i class="fas fa-check"></i> 已加入';
                    btn.disabled = true;
                } else {
                    showToast('添加失败: ' + (result.error || '未知错误'), 'error');
                    btn.innerHTML = '<i class="fas fa-plus"></i> 加入媒体库';
                    btn.disabled = false;
                }
            } catch (e) {
                showToast('请求失败: ' + e.message, 'error');
                btn.innerHTML = '<i class="fas fa-plus"></i> 加入媒体库';
                btn.disabled = false;
            }
        }
        
        // Search download sources by title (for external search results)
        // Reuses the same detectPan + source-item rendering as fetchAutoSources
        async function searchSourcesForTitle(title, year, type, event) {
            if (event) event.stopPropagation();
            
            const modal = document.getElementById('downloadModal');
            const body = modal.querySelector('.modal-body');
            
            // Searching state
            body.innerHTML = `
                <div style="background: var(--bg-card); border-radius: var(--border-radius); padding: 25px; max-width: 620px; width: 100%;">
                    <h3 style="margin-bottom: 20px; display:flex;align-items:center;gap:10px;">
                        <i class="fas fa-search"></i> 搜索资源: ${title}
                    </h3>
                    <div id="autoSearchStatus" style="text-align:center;padding:20px;">
                        <i class="fas fa-spinner fa-spin" style="font-size:2rem;color:var(--accent);"></i>
                        <p style="margin-top:15px;color:var(--text-secondary);">正在搜索下载资源...</p>
                    </div>
                    <div id="autoSearchResults" style="display:none;"></div>
                    <div id="manualDownloadArea" style="display:none;">
                        <div style="margin-top:15px;">
                            <h3 style="font-size:1rem;margin-bottom:12px;"><i class="fas fa-edit"></i> 手动输入下载URL</h3>
                            <input type="text" id="manualUrlInput" class="search-input" placeholder="输入磁链/直链/网盘URL..." style="width:100%;margin-bottom:10px;">
                            <div style="display:flex;gap:8px;">
                                <input type="text" id="manualUrlFilename" class="search-input" placeholder="文件名（可选）" style="flex:1;">
                                <button class="action-btn btn-play" onclick="startAutoDownload(document.getElementById('manualUrlInput').value, '${title.replace(/'/g, "\\'")}')" style="white-space:nowrap;">
                                    <i class="fas fa-download"></i> 下载
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            modal.classList.add('active');
            
            try {
                const res = await fetch('/api/search-sources?title=' + encodeURIComponent(title) + 
                    '&year=' + encodeURIComponent(year || '') + 
                    '&type=' + encodeURIComponent(type || '电影'));
                const data = await res.json();
                
                const statusEl = document.getElementById('autoSearchStatus');
                const resultsEl = document.getElementById('autoSearchResults');
                const manualEl = document.getElementById('manualDownloadArea');
                
                if (data.success && data.sources && data.sources.length > 0) {
                    statusEl.style.display = 'none';
                    resultsEl.style.display = 'block';
                    
                    // 网盘检测函数 (same as fetchAutoSources)
                    function detectPan(src) {
                        const url = (src.url || '').toLowerCase();
                        const stitle = ((src.title || '') + ' ' + (src.hint || '')).toLowerCase();
                        const text = url + ' ' + stitle;
                        const panMap = {
                            '百度网盘': ['pan.baidu.com', '百度网盘', 'baidupan', '百度云'],
                            '阿里云盘': ['aliyundrive.com', 'alipan.com', 'aliyundrive', '阿里云盘', '阿里云'],
                            '夸克网盘': ['quark.cn', '夸克网盘', '夸克'],
                            '115网盘': ['115.com', '115网盘', '115'],
                            '天翼云盘': ['cloud.189.cn', '天翼云盘', '天翼云'],
                            '迅雷云盘': ['pan.xunlei.com', '迅雷云盘', '迅雷'],
                            '蓝奏云': ['lanzou', '蓝奏云', '蓝奏'],
                            '123云盘': ['123pan.com', '123pan', '123云盘'],
                            '彩云盘': ['caiyun.com', '彩云'],
                            '移动云盘': ['139.com', '移动云盘'],
                        };
                        for (const [name, keywords] of Object.entries(panMap)) {
                            if (keywords.some(k => text.includes(k))) return name;
                        }
                        if (src._page_pan) {
                            const pans = src._page_pan.split('|');
                            return pans[0] + (pans.length > 1 ? '等' : '');
                        }
                        return null;
                    }
                    
                    // 来源页网盘上下文
                    function pagePanContext(src) {
                        const pan = src._page_pan || '';
                        if (pan && pan !== 'null' && pan !== 'undefined') return pan;
                        return null;
                    }
                    
                    let html = '<div class="source-list" style="margin-bottom:15px;">';
                    data.sources.forEach((src, idx) => {
                        const typeIcon = src.type_icon || '🔗';
                        const typeLabel = src.type_label || src.source || '资源';
                        const hint = src.hint ? `<div style="font-size:0.7rem;color:var(--text-secondary);margin-top:2px;">${src.hint}</div>` : '';
                        const isMagnet = (src.url || '').startsWith('magnet:');
                        const isAvail = src.available === true;
                        const isManual = src.manual_open === true;
                        const speedBadge = src.speed_badge || '';
                        const errorText = src.test_error || (isAvail ? '' : '可用性未知');
                        const bgColor = isAvail ? (isMagnet ? 'rgba(0,200,100,0.08)' : 'rgba(0,150,255,0.06)')
                                                 : (isManual ? 'rgba(255,193,7,0.06)' : 'rgba(60,60,80,0.1)');
                        const borderColor = isAvail ? (isMagnet ? 'rgba(0,200,100,0.25)' : 'rgba(0,150,255,0.2)')
                                                    : (isManual ? 'rgba(255,193,7,0.2)' : 'rgba(100,100,100,0.15)');
                        const opacity = (isAvail || isManual) ? 1 : 0.7;
                        const onclick = isManual
                            ? `window.open('${src.url.replace(/'/g, "\\'")}','_blank')`
                            : `startAutoDownload('${src.url.replace(/'/g, "\\'")}', '${title.replace(/'/g, "\\'")}')`;
                        const btnLabel = isManual ? '<i class="fas fa-external-link-alt"></i> 页面' : '<i class="fas fa-download"></i> 下载';
                        const btnStyle = isManual
                            ? 'padding:6px 12px;font-size:0.8rem;flex-shrink:0;background:rgba(255,193,7,0.15);border:1px solid rgba(255,193,7,0.3);color:#ffc107;'
                            : (isAvail ? 'padding:6px 12px;font-size:0.8rem;flex-shrink:0;'
                                        : 'padding:6px 12px;font-size:0.8rem;flex-shrink:0;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);color:var(--text-secondary);');
                        html += `
                            <div class="source-item" style="display:flex;align-items:flex-start;gap:10px;padding:12px 14px;background:${bgColor};border-radius:8px;margin-bottom:8px;border:1px solid ${borderColor};opacity:${opacity};transition:all 0.2s;cursor:pointer;"
                                 onclick="event.stopPropagation(); ${onclick}">
                                <span style="font-size:1.2rem;line-height:1.4;">${typeIcon}</span>
                                <div style="flex:1;overflow:hidden;">
                                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                                        <span style="font-size:0.7rem;padding:1px 6px;background:rgba(255,255,255,0.1);border-radius:4px;color:var(--text-secondary);white-space:nowrap;">${typeLabel}</span>
                                        ${detectPan(src) ? `<span style="font-size:0.7rem;padding:1px 6px;background:rgba(255,193,7,0.2);border-radius:4px;color:#ffc107;white-space:nowrap;border:1px solid rgba(255,193,7,0.3);">☁️ ${detectPan(src)}</span>` : ''}
                                        ${pagePanContext(src) && (!detectPan(src) || src._page_pan) ? `<span style="font-size:0.7rem;padding:1px 6px;background:rgba(100,200,255,0.15);border-radius:4px;color:#64c8ff;white-space:nowrap;border:1px solid rgba(100,200,255,0.3);"><i class="fas fa-link"></i> 来源:${pagePanContext(src).replace(/\|/g,'/')}</span>` : ''}
                                        ${speedBadge ? `<span style="font-size:0.7rem;padding:1px 6px;background:rgba(255,255,255,0.05);border-radius:4px;color:var(--text-secondary);white-space:nowrap;">${speedBadge}</span>` : ''}
                                        <span style="font-size:0.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${src.title || src.url.substring(0,80)}</span>
                                    </div>
                                    ${isAvail ? '' : `<div style="font-size:0.7rem;color:#999;margin-top:3px;">检测: ${errorText} — 点击仍可尝试下载</div>`}
                                    ${hint}
                                </div>
                                <button class="action-btn" style="${btnStyle}"
                                        onclick="event.stopPropagation(); ${onclick}">
                                    ${btnLabel}
                                </button>
                            </div>
                            `;
                        });
                        html += '</div>';
                        html += '<div style="text-align:center;margin-top:10px;padding-top:15px;border-top:1px solid rgba(255,255,255,0.1);">';
                        html += '<button class="action-btn btn-download" onclick="showManualInput()" style="background:none;border:1px dashed rgba(255,255,255,0.2);padding:8px 20px;font-size:0.85rem;">';
                        html += '<i class="fas fa-edit"></i> 手动输入URL</button>';
                        html += '<button class="action-btn btn-download" onclick="closeDownloadModal()" style="margin-left:8px;">关闭</button></div>';
                    
                    resultsEl.innerHTML = html;
                } else {
                    statusEl.style.display = 'none';
                    manualEl.style.display = 'block';
                    const msg = document.createElement('div');
                    msg.style.cssText = 'text-align:center;padding:15px;margin-bottom:15px;color:var(--text-secondary);background:var(--bg-secondary);border-radius:8px;font-size:0.9rem;';
                    msg.innerHTML = '<i class="fas fa-exclamation-circle"></i> 暂未找到自动下载源，请手动输入URL';
                    const h3 = manualEl.querySelector('div');
                    if (h3) h3.parentNode.insertBefore(msg, h3);
                }
            } catch (e) {
                const s = document.getElementById('autoSearchStatus');
                if (s) s.innerHTML = '<i class="fas fa-exclamation-triangle" style="color:#ff4757;font-size:1.5rem;"></i><p style="color:var(--text-secondary);margin-top:10px;">搜索失败，请手动输入URL</p>';
                document.getElementById('manualDownloadArea').style.display = 'block';
            }
        }
        
        // Download modal
        function showDownloadModal(id, title, episodes, event) {
            if (event) event.stopPropagation();
            currentDownload = { id, title, episodes: parseInt(episodes) || 0 };
            
            // Show searching state
            const modal = document.getElementById('downloadModal');
            const body = modal.querySelector('.modal-body');
            body.innerHTML = `
                <div style="background: var(--bg-card); border-radius: var(--border-radius); padding: 25px; max-width: 620px; width: 100%;">
                    <h3 style="margin-bottom: 20px; display:flex;align-items:center;gap:10px;">
                        <i class="fas fa-download"></i> 下载: ${title}
                    </h3>
                    <div id="autoSearchStatus" style="text-align:center;padding:20px;">
                        <i class="fas fa-spinner fa-spin" style="font-size:2rem;color:var(--accent);"></i>
                        <p style="margin-top:15px;color:var(--text-secondary);">正在自动搜索下载源...</p>
                    </div>
                    <div id="autoSearchResults" style="display:none;"></div>
                    <div id="manualDownloadArea" style="display:none;">
                        <div style="display: flex; flex-direction: column; gap: 12px;">
                            <div>
                                <label style="display: block; margin-bottom: 5px; color: var(--text-secondary);">视频URL</label>
                                <input type="text" id="downloadUrl" class="search-input" placeholder="https://.../video.mp4" style="width: 100%;">
                            </div>
                            <div>
                                <label style="display: block; margin-bottom: 5px; color: var(--text-secondary);">文件名</label>
                                <input type="text" id="downloadFilename" class="search-input" placeholder="自定义文件名.mp4">
                            </div>
                            <div style="display: flex; gap: 10px; margin-top: 10px;">
                                <button class="action-btn btn-play" onclick="confirmDownload()" style="flex: 1;">
                                    <i class="fas fa-download"></i> 开始下载
                                </button>
                                <button class="action-btn btn-download" onclick="closeDownloadModal()" style="flex: 1;">
                                    取消
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            modal.classList.add('active');
            
            // Auto-search for sources
            fetchAutoSources(id);
        }
        
        async function fetchAutoSources(id) {
            try {
                const res = await fetch('/api/auto-find?id=' + encodeURIComponent(id));
                const data = await res.json();
                
                const statusEl = document.getElementById('autoSearchStatus');
                const resultsEl = document.getElementById('autoSearchResults');
                const manualEl = document.getElementById('manualDownloadArea');
                
                if (data.success && data.sources && data.sources.length > 0) {
                    statusEl.style.display = 'none';
                    resultsEl.style.display = 'block';
                    
                    // 网盘检测函数
                    function detectPan(src) {
                        // 直接网盘链接优先
                        const url = (src.url || '').toLowerCase();
                        const title = ((src.title || '') + ' ' + (src.hint || '')).toLowerCase();
                        const text = url + ' ' + title;
                        const panMap = {
                            '百度网盘': ['pan.baidu.com', '百度网盘', 'baidupan', '百度云'],
                            '阿里云盘': ['aliyundrive.com', 'alipan.com', 'aliyundrive', '阿里云盘', '阿里云'],
                            '夸克网盘': ['quark.cn', '夸克网盘', '夸克'],
                            '115网盘': ['115.com', '115网盘', '115'],
                            '天翼云盘': ['cloud.189.cn', '天翼云盘', '天翼云'],
                            '迅雷云盘': ['pan.xunlei.com', '迅雷云盘', '迅雷'],
                            '蓝奏云': ['lanzou', '蓝奏云', '蓝奏'],
                            '123云盘': ['123pan.com', '123pan', '123云盘'],
                            '彩云盘': ['caiyun.com', '彩云'],
                            '移动云盘': ['139.com', '移动云盘'],
                        };
                        for (const [name, keywords] of Object.entries(panMap)) {
                            if (keywords.some(k => text.includes(k))) return name;
                        }
                        // _page_pan：在来源资源页上发现的网盘（磁链携带）
                        if (src._page_pan) {
                            const pans = src._page_pan.split('|');
                            return pans[0] + (pans.length > 1 ? '等' : '');
                        }
                        return null;
                    }
                    
                    // 是否有来源页网盘上下文
                    function pagePanContext(src) {
                        const pan = src._page_pan || '';
                        if (pan && pan !== 'null' && pan !== 'undefined') {
                            return pan;
                        }
                        return null;
                    }
                    
                    let html = '<div class="source-list" style="margin-bottom:15px;">';
                    if (data.page_hint) {
                        html += '<div style="color:#ffd700;margin-bottom:10px;font-size:0.85rem;padding:8px 12px;background:rgba(255,215,0,0.1);border-radius:8px;border:1px solid rgba(255,215,0,0.2);">' + data.page_hint + '</div>';
                    }
                    data.sources.forEach((src, idx) => {
                                            const typeIcon = src.type_icon || '🔗';
                                            const typeLabel = src.type_label || src.source;
                                            const hint = src.hint ? `<div style="font-size:0.7rem;color:var(--text-secondary);margin-top:2px;">${src.hint}</div>` : '';
                                            const isMagnet = src.url.startsWith('magnet:');
                                            const isAvail = src.available === true;
                                            const isManual = src.manual_open === true;
                                            const speedBadge = src.speed_badge || '';
                                            const errorText = src.test_error || (isAvail ? '' : '可用性未知');
                                            const isPan = detectPan(src) !== null;
                                            const panName = detectPan(src) || '';
                                            const bgColor = isAvail ? (isMagnet ? 'rgba(0,200,100,0.08)' : 'rgba(0,150,255,0.06)')
                                                                     : (isManual ? 'rgba(255,193,7,0.06)' : 'rgba(60,60,80,0.1)');
                                            const borderColor = isAvail ? (isMagnet ? 'rgba(0,200,100,0.25)' : 'rgba(0,150,255,0.2)')
                                                                        : (isManual ? 'rgba(255,193,7,0.2)' : 'rgba(100,100,100,0.15)');
                                            const opacity = (isAvail || isManual) ? 1 : 0.7;
                                            const cursorStyle = isManual ? 'cursor:default;' : 'cursor:pointer;';
                                            // Manual sources: open in browser tab
                                            // Cloud storage sources: show auto-download button
                                                                                        // Other sources (magnet/direct): standard download
                                                                                        const panDlBtn = isPan ? `<button class="action-btn" style="padding:6px 12px;font-size:0.8rem;flex-shrink:0;background:rgba(0,200,100,0.15);border:1px solid rgba(0,200,100,0.3);color:#00c864;margin-left:4px;" onclick="event.stopPropagation(); startCloudDownload('${src.url.replace(/'/g, "\\'")}', '${currentDownload.title.replace(/'/g, "\\'")}')"><i class="fas fa-cloud-download-alt"></i> 自动下载</button>` : '';
                                                                                        const onclick = isManual
                                                ? `window.open('${src.url.replace(/'/g, "\\'")}','_blank')`
                                                : `startAutoDownload('${src.url.replace(/'/g, "\\'")}', '${currentDownload.title.replace(/'/g, "\\'")}')`;
                                            const btnLabel = isManual ? '<i class="fas fa-external-link-alt"></i> 页面' : '<i class="fas fa-download"></i> 下载';
                                            const btnStyle = isManual
                                                ? 'padding:6px 12px;font-size:0.8rem;flex-shrink:0;background:rgba(255,193,7,0.15);border:1px solid rgba(255,193,7,0.3);color:#ffc107;'
                                                : (isAvail ? 'padding:6px 12px;font-size:0.8rem;flex-shrink:0;'
                                                            : 'padding:6px 12px;font-size:0.8rem;flex-shrink:0;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);color:var(--text-secondary);');
                        html += `
                            <div class="source-item" style="display:flex;align-items:flex-start;gap:10px;padding:12px 14px;background:${bgColor};border-radius:8px;margin-bottom:8px;border:1px solid ${borderColor};opacity:${opacity};transition:all 0.2s;${cursorStyle}"
                                 onclick="event.stopPropagation(); ${onclick}">
                                <span style="font-size:1.2rem;line-height:1.4;">${typeIcon}</span>
                                <div style="flex:1;overflow:hidden;">
                                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                                        <span style="font-size:0.7rem;padding:1px 6px;background:rgba(255,255,255,0.1);border-radius:4px;color:var(--text-secondary);white-space:nowrap;">${typeLabel}</span>
                                        ${detectPan(src) ? `<span style="font-size:0.7rem;padding:1px 6px;background:rgba(255,193,7,0.2);border-radius:4px;color:#ffc107;white-space:nowrap;border:1px solid rgba(255,193,7,0.3);">☁️ ${detectPan(src)}</span>` : ''}
                                        ${pagePanContext(src) && (!detectPan(src) || src._page_pan) ? `<span style="font-size:0.7rem;padding:1px 6px;background:rgba(100,200,255,0.15);border-radius:4px;color:#64c8ff;white-space:nowrap;border:1px solid rgba(100,200,255,0.3);"><i class="fas fa-link"></i> 来源:${pagePanContext(src).replace(/\|/g,'/')}</span>` : ''}
                                        ${speedBadge ? `<span style="font-size:0.7rem;padding:1px 6px;background:rgba(255,255,255,0.05);border-radius:4px;color:var(--text-secondary);white-space:nowrap;">${speedBadge}</span>` : ''}
                                        <span style="font-size:0.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${src.title || src.url.substring(0,80)}</span>
                                    </div>
                                    ${isAvail ? '' : `<div style="font-size:0.7rem;color:#999;margin-top:3px;">检测: ${errorText} — 点击仍可尝试下载</div>`}
                                    ${hint}
                                </div>
                                <button class="action-btn" style="${btnStyle}"
                                        onclick="event.stopPropagation(); ${onclick}">
                                    ${btnLabel}
                                </button>
                            </div>
                            `;
                        });
                        html += '</div>';
                        html += '<div style="text-align:center;margin-top:10px;padding-top:15px;border-top:1px solid rgba(255,255,255,0.1);">';
                        html += '<button class="action-btn btn-download" onclick="showManualInput()" style="background:none;border:1px dashed rgba(255,255,255,0.2);padding:8px 20px;font-size:0.85rem;">';
                        html += '<i class="fas fa-edit"></i> 手动输入URL</button>';
                        html += '<button class="action-btn btn-download" onclick="closeDownloadModal()" style="margin-left:8px;">取消</button></div>';
                    
                    resultsEl.innerHTML = html;
                } else {
                    // No sources found, show manual input
                    statusEl.style.display = 'none';
                    manualEl.style.display = 'block';
                    document.querySelector('#manualDownloadArea h3')?.remove();
                    // Add a message
                    const msg = document.createElement('div');
                    msg.style.cssText = 'text-align:center;padding:15px;margin-bottom:15px;color:var(--text-secondary);background:var(--bg-secondary);border-radius:8px;font-size:0.9rem;';
                    msg.innerHTML = '<i class="fas fa-exclamation-circle"></i> 暂未找到自动下载源，请手动输入URL';
                    const h3 = manualEl.querySelector('div');
                    if (h3) h3.parentNode.insertBefore(msg, h3);
                }
            } catch (e) {
                const s = document.getElementById('autoSearchStatus');
                if (s) s.innerHTML = '<i class="fas fa-exclamation-triangle" style="color:#ff4757;font-size:1.5rem;"></i><p style="color:var(--text-secondary);margin-top:10px;">搜索失败，请手动输入URL</p>';
                document.getElementById('manualDownloadArea').style.display = 'block';
            }
        }
        
        function showManualInput() {
            document.getElementById('autoSearchResults').style.display = 'none';
            document.getElementById('autoSearchStatus').style.display = 'none';
            document.getElementById('manualDownloadArea').style.display = 'block';
        }
        
        async function startAutoDownload(url, title) {
            const params = new URLSearchParams({ url, title: title || '下载' });
            try {
                const res = await fetch('/api/dl-url?' + params.toString());
                const result = await res.json();
                if (result.success) {
                    showToast('已添加到下载队列: ' + (title || url.substring(0,40)), 'success');
                    closeDownloadModal();
                } else {
                    showToast('下载失败: ' + result.error, 'error');
                }
            } catch (e) {
                showToast('请求失败: ' + e.message, 'error');
            }
        }
        
        async function startCloudDownload(url, title) {
            const params = new URLSearchParams({ url, title: title || '下载' });
            try {
                const res = await fetch('/api/dl-cloud?' + params.toString());
                const result = await res.json();
                if (result.success) {
                    showToast('☁️ 网盘自动下载已启动: ' + (title || '下载'), 'success');
                    closeDownloadModal();
                } else if (result.need_login) {
                    showToast('❌ ' + result.error, 'error');
                    // Show login guidance in console
                    console.log('网盘登录指引:', result.login_prompt);
                } else {
                    showToast('下载失败: ' + (result.error || '未知错误'), 'error');
                }
            } catch (e) {
                showToast('请求失败: ' + e.message, 'error');
            }
        }
        
        function closeDownloadModal() {
            document.getElementById('downloadModal').classList.remove('active');
            currentDownload = null;
        }
        
        function toggleManualInput() {
            document.getElementById('autoSearchResults').style.display = 'none';
            document.getElementById('autoSearchStatus').style.display = 'none';
            document.getElementById('manualDownloadArea').style.display = 'block';
        }
        
        function confirmDownload() {
            const url = document.getElementById('downloadUrl').value.trim();
            const filename = document.getElementById('downloadFilename').value.trim();
            
            if (!url) {
                showToast('请输入视频URL', 'error');
                return;
            }
            
            if (currentDownload.episodes > 0) {
                // 多集剧集，需要逐集下载
                showToast(`开始下载 ${currentDownload.title} 共${currentDownload.episodes}集...`, 'info');
                // 这里需要前端循环添加下载任务
                // 简化版：提示用户
                showToast('多集下载功能待完善，请逐集添加', 'info');
            } else {
                // 单集/电影
                downloadSingle(currentDownload.title, url, filename || null);
            }
            
            closeDownloadModal();
        }
        
        async function downloadSingle(title, url, filename) {
            const params = new URLSearchParams({
                title: title,
                url: url,
                filename: filename || ''
            });
            
            try {
                const res = await fetch(`/api/download?${params}`);
                const result = await res.json();
                
                if (result.success) {
                    showToast(`已添加到下载队列: ${title}`, 'success');
                    switchTab('queue');
                } else {
                    showToast(`下载失败: ${result.error}`, 'error');
                }
            } catch (e) {
                showToast(`请求失败: ${e.message}`, 'error');
            }
        }
        
        // Open Douban page for a movie/TV show
        function openDouban(title, year) {
            const query = encodeURIComponent(title + (year ? ' ' + year : ''));
            window.open('https://search.douban.com/movie/subject_search?search_text=' + query, '_blank');
        }
        
        // Download and play (download first, then play)
        async function downloadAndPlay(id, title, episodes, event) {
            if (event) event.stopPropagation();
            
            // 提示用户需要先添加下载URL
            showDownloadModal(id, title, episodes, event);
        }
        
        function proxyPoster(url) {
            if (!url || !url.startsWith('http')) return url || 'https://via.placeholder.com/200x300/16213e/ffffff?text=No+Poster';
            const b64 = btoa(url).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
            return '/api/poster/' + b64;
        }
        
        // Load library
        async function loadLibrary() {
            try {
                const localRes = await fetch('/api/local-files');
                const localData = await localRes.json();
                
                document.getElementById('videoCount').textContent = localData.count;
                
                if (localData.count === 0) {
                    document.getElementById('libraryGrid').innerHTML = `
                        <div class="empty-state">
                            <i class="fas fa-video-slash"></i>
                            <h2>暂无本地视频</h2>
                            <p>从首页搜索作品下载，或在「下载管理 > URL下载」中粘贴外部链接</p>
                        </div>
                    `;
                    return;
                }
                
                let localHtml = '';
                localData.files.forEach(f => {
                    let matchedTitle = f.name.replace(/\.(mp4|mkv|webm|avi)$/i, '');
                    const match = Object.values(allItems).find(m => 
                        f.name.toLowerCase().includes(m.title.toLowerCase())
                    );
                    const posterUrl = match ? `/posters/${match.id}.jpg?v=3` :
                        'https://via.placeholder.com/200x300/16213e/ffffff?text=' + encodeURIComponent(matchedTitle.substring(0,6));
                    const metaText = match ? (match.type || '电影') + ' · ' + match.rating.toFixed(1) + '分' : f.size_mb + ' MB';
                    
                    localHtml += `
                    <div class="video-card" onclick="playVideo('/videos/${f.name}', '${f.name.replace(/'/g, "\\'")}')">
                        <div class="card-poster">
                            <img src="${posterUrl}" alt="${f.name}" loading="lazy">
                            <div class="card-overlay">
                                <div class="play-btn"><i class="fas fa-play"></i></div>
                            </div>
                            <div class="downloaded-badge"><i class="fas fa-check"></i> 本地</div>
                        </div>
                        <div class="card-info">
                            <div class="card-title">${f.name.replace(/\.\w+$/, '')}</div>
                            <div class="card-meta">
                                <span>${f.size_mb} MB</span>
                                <span>${metaText}</span>
                            </div>
                        </div>
                    </div>`;
                });
                document.getElementById('libraryGrid').innerHTML = localHtml;
            } catch (e) {
                console.error(e);
            }
        }
        
        // Play video (边下边播 - 直接播放本地文件)
        function playVideo(url, title) {
            const modal = document.getElementById('playerModal');
            const player = document.getElementById('player');
            document.getElementById('playerTitle').textContent = title;
            
            player.src = url;
            modal.classList.add('active');
            player.load();
            player.play().catch(e => console.log('Auto-play blocked:', e));
        }
        
        // Close player
        function closePlayer() {
            const modal = document.getElementById('playerModal');
            const player = document.getElementById('player');
            player.pause();
            player.src = '';
            modal.classList.remove('active');
        }
        
        // Refresh queue
        async function refreshQueue() {
            try {
                const res = await fetch('/api/status');
                const status = await res.json();
                
                const container = document.getElementById('queueList');
                const items = status.queue || [];
                
                if (items.length === 0) {
                    container.innerHTML = `
                        <div class="empty-state">
                            <i class="fas fa-download"></i>
                            <h2>下载队列为空</h2>
                            <p>从首页点击"下载"按钮添加任务</p>
                        </div>
                    `;
                    return;
                }
                
                container.innerHTML = items.map(item => {
                    const iconClass = item.status;
                    const progress = item.progress || 0;
                    const statusText = {
                        pending: '等待中',
                        downloading: `${progress}% (${item.speed || ''})`,
                        completed: '已完成',
                        failed: item.error || '失败',
                        cancelled: '已取消'
                    }[item.status] || item.status;
                    
                    return `
                        <div class="queue-item">
                            <div class="queue-icon ${iconClass}">
                                <i class="fas ${item.status === 'downloading' ? 'fa-spinner fa-spin' : 
                                            item.status === 'completed' ? 'fa-check' : 
                                            item.status === 'failed' ? 'fa-times' : 
                                            item.status === 'cancelled' ? 'fa-ban' : 'fa-download'}"></i>
                            </div>
                            <div class="queue-info">
                                <div class="queue-title">${item.title}${item.episode ? ` (第${item.episode}集)` : ''}</div>
                                <div class="queue-progress">
                                    <div class="queue-progress-bar" style="width:${progress}%"></div>
                                </div>
                            </div>
                            <div class="queue-status">${statusText}</div>
                            <div class="queue-actions" style="display:flex;gap:6px;">
                                ${item.status === 'pending' || item.status === 'downloading' ? 
                                    `<button class="btn-cancel" onclick="cancelDownload('${item.id}')"><i class="fas fa-times"></i> 取消</button>` : 
                                    (item.status === 'completed' && item.filename ?
                                    `<button class="btn-delete" onclick="openFilePath('${item.filename.replace(/'/g, "\\'")}')" title="打开文件位置"><i class="fas fa-folder-open"></i></button><button class="btn-delete" onclick="deleteDownload('${item.id}')" title="删除任务"><i class="fas fa-trash"></i></button>` :
                                    `<button class="btn-delete" onclick="deleteDownload('${item.id}')" title="删除任务"><i class="fas fa-trash"></i></button>`)}
                            </div>
                        </div>
                    `;
                }).join('');
            } catch (e) {
                console.error(e);
            }
        }
        
        async function cancelDownload(id) {
            try {
                await fetch(`/api/cancel?id=${id}`);
                refreshQueue();
                showToast('已取消下载', 'info');
            } catch (e) {
                showToast('取消失败', 'error');
            }
        }

        async function deleteDownload(id) {
            if (!confirm('确定删除该下载任务吗？')) return;
            try {
                const res = await fetch(`/api/remove?id=${id}`);
                const data = await res.json();
                if (data.success) {
                    refreshQueue();
                    refreshDlManage();
                    showToast('已删除下载任务', 'info');
                } else {
                    showToast('删除失败: ' + (data.error || '未知错误'), 'error');
                }
            } catch (e) {
                showToast('删除失败: ' + e.message, 'error');
            }
        }
        
        // === 新增下载功能 ===
        
        // Batch download all episodes
        async function batchDownloadAll() {
            if (!currentEpisodeItemId) return;
            const item = allItems[currentEpisodeItemId];
            if (!item) return;
            
            showToast(`开始批量添加 ${item.title} ${item.episodes}集下载任务...`, 'info');
            
            // Fetch all download URLs and add each episode
            try {
                const res = await fetch(`/api/dl-batch?id=${currentEpisodeItemId}&start=1&end=${item.episodes}`);
                const result = await res.json();
                if (result.success) {
                    showToast(`已添加 ${result.total} 个下载任务: ${result.range}`, 'success');
                } else {
                    showToast(`批量添加失败: ${result.error}`, 'error');
                }
            } catch (e) {
                showToast(`请求失败: ${e.message}`, 'error');
            }
        }
        
        // Start URL download via yt-dlp
        async function startUrlDownload() {
            const url = document.getElementById('dlUrlInput').value.trim();
            const title = document.getElementById('dlUrlTitle').value.trim() || 'URL下载';
            
            if (!url) {
                showToast('请输入视频URL', 'error');
                return;
            }
            
            const statusEl = document.getElementById('urlDlStatus');
            statusEl.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 正在添加下载任务...';
            
            try {
                const params = new URLSearchParams({ url, title });
                const res = await fetch(`/api/dl-url?${params}`);
                const result = await res.json();
                
                if (result.success) {
                    statusEl.innerHTML = `<i class="fas fa-check" style="color:var(--rating-high)"></i> 已添加: ${result.title}`;
                    showToast('已添加到下载队列', 'success');
                    document.getElementById('dlUrlInput').value = '';
                } else {
                    statusEl.innerHTML = `<i class="fas fa-times" style="color:#ff4757"></i> 失败: ${result.error}`;
                }
            } catch (e) {
                statusEl.innerHTML = `<i class="fas fa-times" style="color:#ff4757"></i> 请求失败: ${e.message}`;
                showToast(`请求失败: ${e.message}`, 'error');
            }
        }
        
        // Refresh download management page
        async function refreshDlManage() {
            try {
                const res = await fetch('/api/status');
                const status = await res.json();
                const items = status.queue || [];
                
                // Progress list: downloading + pending
                const progressItems = items.filter(i => i.status === 'downloading' || i.status === 'pending');
                const progressContainer = document.getElementById('dlProgressList');
                
                if (progressItems.length === 0) {
                    progressContainer.innerHTML = `
                        <div class="empty-state">
                            <i class="fas fa-check-circle"></i>
                            <h2>没有正在进行的下载</h2>
                            <p>在"URL下载"选项卡中粘贴外部视频链接</p>
                        </div>
                    `;
                } else {
                    progressContainer.innerHTML = progressItems.map(item => `
                        <div class="queue-item">
                            <div class="queue-icon ${item.status}">
                                <i class="fas ${item.status === 'downloading' ? 'fa-spinner fa-spin' : 'fa-download'}"></i>
                            </div>
                            <div class="queue-info">
                                <div class="queue-title">${item.title}</div>
                                <div class="queue-progress">
                                    <div class="queue-progress-bar" style="width:${item.progress||0}%"></div>
                                </div>
                            </div>
                            <div class="queue-status">${item.status === 'downloading' ? `${item.progress||0}% (${item.speed||''})` : '等待中'}</div>
                                                        <div class="queue-actions" style="display:flex;gap:6px;">
                                                            <button class="btn-cancel" onclick="cancelDownload('${item.id}')"><i class="fas fa-times"></i> 取消</button>
                                                            <button class="btn-delete" onclick="deleteDownload('${item.id}')" title="删除任务"><i class="fas fa-trash"></i></button>
                                                        </div>
                                                    </div>
                                                `).join('');
                }
                
                // Completed + Failed list (both need delete ability)
                const completedItems = items.filter(i => i.status === 'completed' || i.status === 'failed' || i.status === 'cancelled');
                const completedContainer = document.getElementById('dlCompletedList');
                
                if (completedItems.length === 0) {
                    completedContainer.innerHTML = `
                        <div class="empty-state">
                            <i class="fas fa-video-slash"></i>
                            <h2>暂无已完成的下载</h2>
                            <p>完成后的视频文件会显示在这里</p>
                        </div>
                    `;
                } else {
                    completedContainer.innerHTML = completedItems.map(item => `
                        <div class="completed-file">
                            <i class="fas ${item.status === 'completed' ? 'fa-check-circle' : item.status === 'failed' ? 'fa-times-circle' : 'fa-ban'}"></i>
                            <div class="info">
                                <div class="name">${item.title}</div>
                                <div class="meta">${item.size_mb ? item.size_mb + ' MB · ' : ''}${item.completed_at ? new Date(item.completed_at).toLocaleString() : item.added_at ? new Date(item.added_at).toLocaleString() : ''}${item.error ? ' · ' + item.error : ''}</div>
                                ${item.filename ? `<div class="meta" style="margin-top:4px;"><i class="fas fa-folder-open"></i> ${item.filename}</div>` : ''}
                            </div>
                            <div style="display:flex;align-items:center;gap:8px;">
                                <div style="color:${item.status === 'completed' ? 'var(--rating-high)' : item.status === 'failed' ? '#ff4757' : '#ffa502'};font-size:0.8rem;font-weight:600;">
                                    ${item.status === 'completed' ? '已完成' : item.status === 'failed' ? '失败' : '已取消'}
                                </div>
                                ${item.filename ? `<button class="btn-delete" onclick="openFilePath('${item.filename.replace(/'/g, "\\'")}')" title="打开文件位置"><i class="fas fa-folder-open"></i></button>` : ''}
                                <button class="btn-delete" onclick="deleteDownload('${item.id}')" title="删除任务"><i class="fas fa-trash"></i></button>
                            </div>
                        </div>
                    `).join('');
                }
            } catch (e) {
                console.error(e);
            }
        }
        
        // Toast
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = `toast toast-${type} show`;
            setTimeout(() => toast.classList.remove('show'), 3000);
        }
        
        function openFilePath(filename) {
            fetch('/api/open-file?filename=' + encodeURIComponent(filename))
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        showToast('已在Finder中打开: ' + data.filename, 'success');
                    } else {
                        showToast('文件未找到', 'error');
                    }
                }).catch(() => {
                    showToast('打开文件位置失败', 'error');
                });
        }
        
        // Close modal on click outside
        document.getElementById('playerModal').addEventListener('click', (e) => {
            if (e.target.id === 'playerModal') closePlayer();
        });
        document.getElementById('downloadModal').addEventListener('click', (e) => {
            if (e.target.id === 'downloadModal') closeDownloadModal();
        });
        document.getElementById('episodeModal').addEventListener('click', (e) => {
            if (e.target.id === 'episodeModal') closeEpisodes();
        });
    </script>
</body>
</html>'''


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), StreamHandler) as httpd:
        print(f"🎬 流媒体服务器 v3 已启动")
        print(f"📺 访问地址: http://localhost:{PORT}")
        print(f"📁 视频目录: {VIDEO_DIR}")
        print(f"📚 数据库: {DB_FILE}")
        print(f"⏹️  按 Ctrl+C 停止")
        httpd.serve_forever()
