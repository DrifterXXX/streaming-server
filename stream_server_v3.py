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

# 数据库模块（Phase 2: 数据库操作独立）
import database

# 搜索服务（Phase 3: 外部搜索独立）
import search_service

# Phase 1: 并发下载控制 — 最多3个同时进行（放在 v3_dl 导入前，避免循环引用）
MAX_CONCURRENT_DOWNLOADS = 3
_download_semaphore = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# 下载增强模块
import stream_server_v3_dl as dl_enhancer

# 网盘下载（Phase 4: 云下载独立）
import cloud_download

# 路由模块
import router

# 配置
VIDEO_DIR = Path.home() / "services/streaming-server" / "videos"
DB_FILE = Path.home() / "services/streaming-server" / "database.json"
QUEUE_FILE = Path.home() / "services/streaming-server" / "download_queue.json"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9878

# 豆瓣评分阈值
MIN_RATING = 7.0

# 确保目录存在
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


class _DownloadHandle:
    """下载句柄 — 用于取消 urllib 下载"""
    def __init__(self):
        self.cancel = threading.Event()
    def kill(self):
        self.cancel.set()


class DownloadManager:
    """后台下载管理器 - 支持边下边播"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
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
        self._queue_lock = threading.Lock()
        self._start_worker()
    
    def _load_queue(self):
        if QUEUE_FILE.exists():
            try:
                with self._queue_lock:
                    with open(QUEUE_FILE) as f:
                        return json.load(f)
            except Exception as e:
                print(f"[_load_queue] error: {e}")
        return []

    def _save_queue(self):
        with self._queue_lock:
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
        with self._queue_lock:
            self.queue.append(item)
        self._save_queue()
        return item["id"]
    
    def _start_worker(self):
        """启动后台下载线程"""
        def worker():
            while True:
                try:
                    with self._queue_lock:
                        # 计算当前正在下载的任务数（含 queued 标记的）
                        active_downloads = sum(1 for i in self.queue
                                               if i["status"] in ("downloading", "queued"))
                        slots_available = MAX_CONCURRENT_DOWNLOADS - active_downloads
                        if slots_available > 0:
                            for item in self.queue:
                                if item["status"] != "pending":
                                    continue
                                if slots_available <= 0:
                                    break
                                item["status"] = "queued"
                                slots_available -= 1
                            self._save_queue()

                    # 在 _queue_lock 保护下统一修改状态
                    items_to_start = []
                    with self._queue_lock:
                        for item in self.queue:
                            if item.get("status") == "queued":
                                item["status"] = "pending"
                                items_to_start.append(item)
                    for item in items_to_start:
                        threading.Thread(target=self._download, args=(item,), daemon=True).start()
                except Exception as e:
                    print(f"[worker] error: {e}")
                time.sleep(3)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
    
    def _download(self, item):
        """执行下载 — 带并发控制（最多3个）"""
        acquired = False
        try:
            acquired = _download_semaphore.acquire(timeout=120)  # 2分钟超时
        except Exception as e:
            print(f"[_download] acquire failed: {e}")
            acquired = False
        if not acquired:
            item["status"] = "failed"
            item["error"] = "等待下载槽位超时（2分钟）"
            self._save_queue()
            return
        try:
            self._do_download(item)
        except Exception as e:
            print(f"[_download] _do_download failed: {e}")
        finally:
            if acquired:
                _download_semaphore.release()

    def _set_item_status(self, item, status, **extra):
        """线程安全地设置 item 状态（在 _queue_lock 保护下）"""
        with self._queue_lock:
            item["status"] = status
            for k, v in extra.items():
                item[k] = v
            self._save_queue()

    def _do_download(self, item):
        """下载核心逻辑"""
        self._set_item_status(item, "downloading", started_at=datetime.now().isoformat())

        # 注册下载句柄以便 cancel/remove 能终止下载（线程安全）
        handle = _DownloadHandle()
        with self._queue_lock:
            self.active_downloads[item["id"]] = handle

        output_path = VIDEO_DIR / item["filename"]

        try:
            start_time = time.time()

            # 使用 urlopen 带 timeout 替代已弃用的 urlretrieve
            req = urllib.request.Request(item["url"], headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            })
            with urllib.request.urlopen(req, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                chunk_size = 8192
                downloaded = 0
                with open(output_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        # 检查取消信号
                        if handle.cancel.is_set():
                            self._set_item_status(item, "cancelled")
                            return
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            item["progress"] = min(100, int(downloaded * 100 / total))
                        elapsed = time.time() - start_time
                        if elapsed > 0 and downloaded > 0:
                            speed = downloaded / elapsed / 1024 / 1024
                            item["speed"] = f"{speed:.1f} MB/s"

            if output_path.exists() and output_path.stat().st_size > 1000000:
                self._set_item_status(
                    item, "completed",
                    completed_at=datetime.now().isoformat(),
                    size=output_path.stat().st_size,
                    size_mb=round(output_path.stat().st_size / 1024 / 1024, 1),
                )
            else:
                self._set_item_status(item, "failed", error="文件过小或损坏")
                if output_path.exists():
                    output_path.unlink()

        except Exception as e:
            self._set_item_status(item, "failed", error=str(e))
            if output_path.exists():
                output_path.unlink()

        finally:
            with self._queue_lock:
                self.active_downloads.pop(item["id"], None)

    def get_status(self):
        return {"queue": self.queue[-20:]}  # 最近20个任务

    def _scan_and_refresh(self):
        """扫描视频目录并刷新 - 供子模块回调使用"""
        pass  # 预留钩子，暂不实现
    
    def cancel(self, item_id):
        """取消下载 - 同时杀后台进程"""
        with self._queue_lock:
            proc = self.active_downloads.pop(item_id, None)
        if proc:
            try:
                proc.kill()
            except Exception as e:
                print(f"[cancel] kill failed: {e}")
        
        for item in self.queue:
            if item["id"] == item_id and item["status"] in ["pending", "downloading"]:
                item["status"] = "cancelled"
                self._save_queue()
                return True
        return False

    def remove(self, item_id):
        """从队列中删除任务（支持所有状态）- 同时杀后台进程"""
        with self._queue_lock:
            proc = self.active_downloads.pop(item_id, None)
        if proc:
            try:
                proc.kill()
            except Exception as e:
                print(f"[remove] kill failed: {e}")
        
        for i, item in enumerate(self.queue):
            if item["id"] == item_id:
                self.queue.pop(i)
                self._save_queue()
                return True
        return False


class StreamHandler(http.server.SimpleHTTPRequestHandler):
    """流媒体请求处理器"""
    
    download_manager = DownloadManager()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIDEO_DIR), **kwargs)
    
    @classmethod
    def get_db(cls):
        return database.MediaDB.get_db()
    
    def do_GET(self):
        if not router.route_request(self):
            super().do_GET()

    def serve_poster(self, path):
        """代理Douban海报图片"""
        import urllib.request
        import urllib.parse
        # /api/poster/{base64_url} 或 /api/poster/https:... 或 URL编码
        poster_path = path[len("/api/poster/"):]
        # URL解码（如果客户端做了URL编码）
        poster_path = urllib.parse.unquote(poster_path)
        if not poster_path:
            self.send_error(404)
            return

        # 先尝试base64解码，不行就当直链
        img_url = None
        import base64
        try:
            b64_input = poster_path
            padding = 4 - len(b64_input) % 4
            if padding != 4:
                b64_input += '=' * padding
            decoded = base64.urlsafe_b64decode(b64_input).decode('utf-8')
            img_url = decoded
        except Exception:
            # 不是base64，当直链（https:/ → https:// 修复，但避免重复//）
            if img_url is None:
                img_url = poster_path
                if img_url.startswith("https:/") and not img_url.startswith("https://"):
                    img_url = "https://" + img_url[7:]
                elif img_url.startswith("http:/") and not img_url.startswith("http://"):
                    img_url = "http://" + img_url[6:]
        
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
        except Exception:
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

        # 特殊处理：搜索和添加库需要自定义 body 解析
        if path == "/api/search" or path == "/api/search-debug":
            self.handle_search_post()
            return

        if path == "/api/add-to-library":
            self.handle_add_to_library()
            return

        # 其他 POST 路由走 router dispatch（读取 JSON body）
        raw_cl = self.headers.get('Content-Length', '0')
        try:
            content_length = int(raw_cl)
        except (ValueError, TypeError):
            content_length = 0
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            data = json.loads(body.decode('utf-8'))
        except Exception:
            data = {}

        method, params = router.resolve(path, data)
        if method is None:
            self.send_response(405)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Method Not Allowed")
            return

        try:
            router.dispatch(self, method, params)
        except KeyError:
            self.send_response(405)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"Method Not Allowed")
    
    def handle_search_post(self):
        """处理POST搜索请求 - 支持 JSON body 和 form-data"""
        # 读取请求体
        raw_cl = self.headers.get('Content-Length', '0')
        try:
            content_length = int(raw_cl)
        except (ValueError, TypeError):
            content_length = 0
        q = ""
        if content_length > 0:
            body = self.rfile.read(content_length)
            # 先尝试 JSON 解析
            try:
                data = json.loads(body.decode('utf-8'))
                q = data.get("q", data.get("query", ""))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # 回退到 form-data 解析
                try:
                    body_str = body.decode('utf-8')
                except Exception:
                    body_str = body.decode('latin-1')
                query = urllib.parse.parse_qs(body_str)
                q = query.get("q", [""])[0]

        # 如果 body 解析未拿到 q，检查 URL query 参数
        if not q:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.query:
                qs = urllib.parse.parse_qs(parsed.query)
                q = qs.get("q", [""])[0]
        
        # 如果是debug端点
        if self.path.startswith("/api/search-debug"):
            self.send_search_debug(q)
        else:
            self.send_search({"q": q})
    
    def send_search_json(self, q):
        """发送搜索结果JSON"""
        self.send_json(database.MediaDB.search_json(q))
    
    def send_search_debug(self, q):
        """发送搜索调试信息"""
        db = database.MediaDB.get_db()

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
            except Exception as e:
                print(f"[send_videos_list] error processing {f.name}: {e}")
        videos.sort(key=lambda x: x["modified"], reverse=True)
        self.send_json({"success": True, "videos": videos, "count": len(videos)})
    
    def send_database(self):
        """获取影视数据库（7分以上）"""
        movies = database.MediaDB.get_filtered()
        self.send_json({"success": True, "movies": movies, "count": len(movies)})
    
    def send_countries(self):
        """获取所有出品国列表"""
        db = database.MediaDB.get_db()
        movies = db.get("movies", [])
        country_set = {}
        for m in movies:
            c = m.get("country", "其他")
            if c not in country_set:
                country_set[c] = 0
            country_set[c] += 1
        # 排序：数量多的排前，'其他'放最后
        sorted_countries = sorted(country_set.items(), key=lambda x: (-x[1], x[0]))
        country_list = [{"name": c, "count": n} for c, n in sorted_countries]
        self.send_json({"success": True, "countries": country_list})

    def send_search(self, query):
        """搜索本地数据库（不限评分）"""
        q = query.get("q", "")
        if not q:
            self.send_json([])
            return
        self.send_json(database.MediaDB.search(q))
    
    def send_search_external(self, query):
        """搜索外部源（豆瓣+TMDB），返回未在本地库的作品"""
        q = query.get("q", "")
        if not q:
            self.send_json([])
            return
        existing_titles = database.MediaDB.get_existing_titles()
        results = search_service.search_external(q, existing_titles)
        self.send_json(results)
    
    def handle_add_to_library(self):
        """将外部搜索结果添加到本地媒体库"""
        raw_cl = self.headers.get('Content-Length', '0')
        try:
            content_length = int(raw_cl)
        except (ValueError, TypeError):
            content_length = 0
        if content_length <= 0:
            self.send_json({"success": False, "error": "空请求"}, 400)
            return
        
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            try:
                body_str = body.decode('utf-8')
                data = json.loads(body_str)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"[handle_add_to_library] JSON parse error: {e}")
                self.send_json({"success": False, "error": "JSON解析失败"}, 400)
                return
        
        result = database.MediaDB.add_entry(data)
        
        # 下载海报
        if result.get("success"):
            poster_url = data.get("poster", "")
            media_id = result["id"]
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
                        with open(poster_path, "wb") as f:
                            f.write(resp.read())
                except Exception as e:
                    print(f"[handle_add_to_library] poster download failed: {e}")
        
        self.send_json(result)
    
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
        result = cloud_download.handle_dl_cloud(url, title, self.download_manager)
        status = 400 if not result.get("success") else 200
        if result.get("need_login"):
            status = 401
        if result.get("error", "").startswith("网盘引擎"):
            status = 500
        self.send_json(result, status=status)
    
    def send_status(self):
        self.send_json(self.download_manager.get_status())
    
    def send_open_file(self):
        """打开文件路径（在Finder中显示）"""
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        filename = qs.get("filename", [None])[0]
        if not filename:
            self.send_json({"success": False, "error": "缺少文件名"}, 400)
            return

        # 安全校验：禁止路径穿越
        filename = os.path.basename(filename)
        video_dir = str(VIDEO_DIR)
        filepath = os.path.join(video_dir, filename)

        # 确保文件在 VIDEO_DIR 目录内
        real_path = os.path.realpath(filepath)
        if not real_path.startswith(os.path.realpath(video_dir) + os.sep) and real_path != os.path.realpath(video_dir):
            self.send_json({"success": False, "error": "路径无效"}, 403)
            return

        found = None
        if os.path.exists(real_path) and os.path.isfile(real_path):
            found = real_path
        else:
            # 模糊匹配：在 VIDEO_DIR 中查找包含文件名关键词的文件
            filename_lower = filename.lower()
            for f in os.listdir(video_dir):
                if filename_lower in f.lower():
                    candidate = os.path.join(video_dir, f)
                    if os.path.isfile(candidate):
                        found = candidate
                        break

        if found:
            subprocess.Popen(["open", "-R", found])
            self.send_json({"success": True, "path": found, "filename": os.path.basename(found)})
        else:
            self.send_json({"success": False, "error": "文件未找到"}, 404)

    def send_reload_db(self):
        """手动重新加载数据库"""
        try:
            db = database.MediaDB.reload()
            self.send_json({"success": True, "message": "数据库已重新加载", "count": len(db.get("movies", []))})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, 500)

    def _dl_save_source(self):
        """
        持久化单个资源源到 media_id。
        用于外部搜索页和自动搜索结果。
        """
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        media_id = (qs.get("media_id") or [""])[0]
        url = (qs.get("url") or [""])[0]
        name = (qs.get("name") or [""])[0]
        source = (qs.get("source") or [""])[0]
        pwd = (qs.get("pwd") or [None])[0]
        if not media_id or not url:
            self.send_json({"success": False, "error": "缺少 media_id 或 url"}, 400)
            return
        item = {
            "url": url,
            "title": name,
            "source": source,
            "status": "need_pwd" if pwd else "available",
        }
        if pwd:
            item["pwd"] = pwd
        res = database.MediaDB.update_resource_sources(media_id, [item])
        if res.get("success"):
            self.send_json({"success": True, "saved": 1})
        else:
            self.send_json(res, 400)

    def _dl_save_search_to_library(self):
        """
        把外部搜索整条结果写入 local library。
        参数：title/year/rating/type/genre/poster/douban_url/douban_id
        并将 resource_sources 也随 data 一起回填。
        """
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        data = {k: (v[0] if v else "") for k, v in qs.items()}
        url = data.pop("url", "")
        source = data.pop("source", "")
        pwd = data.pop("pwd", None)
        if not data.get("title"):
            self.send_json({"success": False, "error": "缺少 title"}, 400)
            return
        # 预处理类型映射
        t = data.get("type", "")
        if "电视" in t or t.lower().startswith("tv"):
            data["type"] = "电视剧"
        elif "电影" in t or t.lower().startswith("movie"):
            data["type"] = "电影"
        else:
            data.setdefault("type", "电影")
        try:
            data["rating"] = float(data.get("rating", 0) or 0)
        except Exception:
            data["rating"] = 0.0
        data.setdefault("seasons", [])
        data.setdefault("episodes", 0)
        res = database.MediaDB.add_entry(data)
        if res.get("success"):
            media_id = res["id"]
            saved = 0
            if url:
                item = {
                    "url": url,
                    "title": f"{res['entry']['title']}",
                    "source": source,
                    "status": "need_pwd" if pwd else "available",
                }
                if pwd:
                    item["pwd"] = pwd
                save_res = database.MediaDB.update_resource_sources(media_id, [item])
                saved = 1 if save_res.get("success") else 0
            self.send_json({"success": True, "media_id": media_id, "source_saved": saved})
        else:
            self.send_json(res, 400)

    def send_db_meta(self):
        """返回数据库元信息（修改时间、条目数），用于前端热更新检测"""
        self.send_json(database.MediaDB.get_meta())

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
        self.send_json(cloud_download.pan_status())
    
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
        # Load template from external file (reduces main file by 109KB)
        tmpl_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
        if os.path.exists(tmpl_path):
            with open(tmpl_path, "r", encoding="utf-8") as f:
                return f.read()
        return "<!-- Template not found -->"


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), StreamHandler) as httpd:
        print(f"🎬 流媒体服务器 v3 已启动")
        print(f"📺 访问地址: http://localhost:{PORT}")
        print(f"📁 视频目录: {VIDEO_DIR}")
        print(f"📚 数据库: {DB_FILE}")
        print(f"⏹️  按 Ctrl+C 停止")
        httpd.serve_forever()
