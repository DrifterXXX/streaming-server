#!/usr/bin/env python3
"""
流媒体服务器下载增强模块 — stream_server_v3.py 的下载功能扩展
本地文件扫描 + yt-dlp 外部下载 + 批量下载 + 自动搜索下载源 + 增强管理 + 网盘下载
"""
import os, json, time, re, threading, subprocess as sp, fcntl
from pathlib import Path
from datetime import datetime
from urllib.parse import quote as url_quote, urlencode
import urllib.request as urlreq
from concurrent.futures import ThreadPoolExecutor
import atexit
import database

# 网盘下载引擎
try:
    from cloud_disk_dl import AliyunEngine, BaiduEngine, QuarkEngine, detect_pan
    _ALIYUN_ENGINE = AliyunEngine()
    _BAIDU_ENGINE = BaiduEngine()
    _QUARK_ENGINE = QuarkEngine()
except Exception:
    _ALIYUN_ENGINE = None
    _BAIDU_ENGINE = None
    _QUARK_ENGINE = None

VIDEO_DIR = Path.home() / "services/streaming-server" / "videos"
DB_FILE = Path.home() / "services/streaming-server" / "database.json"
QUEUE_FILE = Path.home() / "services/streaming-server" / "download_queue.json"

# 下载工具路径自动解析
def _find_executable(name, fallbacks=None):
    """优先使用 shutil.which，再回退到常见安装路径"""
    import shutil
    path = shutil.which(name)
    if path:
        return path
    if fallbacks:
        for p in fallbacks:
            if Path(p).exists():
                return p
    return name

YT_DLP_BIN = _find_executable(
    "yt-dlp",
    fallbacks=[
        str(Path.home() / "Library/Python/3.9/bin/yt-dlp"),
        "/usr/local/bin/yt-dlp",
        "/opt/homebrew/bin/yt-dlp",
    ],
)
ARIA2C_BIN = _find_executable(
    "aria2c",
    fallbacks=["/opt/homebrew/bin/aria2c", "/usr/local/bin/aria2c"],
)


# BT公共Tracker列表 — 提高磁力链找到做种者的概率
BT_TRACKERS = "&tr=" + "&tr=".join([
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.cyberia.is:6969/announce",
    "udp://tracker.dler.com:6969/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "https://tracker.nanoha.org:443/announce",
    "https://tr.anidl.org:443/announce",
    "https://tracker.lilithraws.org:443/announce",
    "wss://tracker.openwebtorrent.com:443/announce",
])

# aria2c基础参数模板
ARIA2_BASE = [
    "--seed-time=0",
    "--enable-dht=true",
    "--dht-listen-port=6881",
    "--bt-require-crypto=true",
    "--bt-max-peers=100",
    "--max-connection-per-server=16",
    "--split=16",
    "--allow-overwrite=true",
    "--async-dns=false",
    "--user-agent=qBittorrent/4.6.5",
]

# 下载管理器线程锁
_queue_lock = threading.Lock()

# Phase 1: 并发下载控制 — 最多3个同时进行 (使用 stream_server_v3 的信号量)
from stream_server_v3 import _download_semaphore

# 并发下载执行器 (最多5个线程)
_download_executor = ThreadPoolExecutor(max_workers=5)
atexit.register(_download_executor.shutdown, wait=False)

# 网盘分享链接正则 — 用于深度爬取资源页
PAN_URL_PATTERNS = [
    (r'(https?://pan\.baidu\.com/s/[a-zA-Z0-9_-]+(?:\?pwd=[a-zA-Z0-9]+)?)', "百度网盘"),
    (r'(https?://(?:www\.)?aliyundrive\.com/s/[a-zA-Z0-9_-]+)', "阿里云盘"),
    (r'(https?://(?:www\.)?alipan\.com/s/[a-zA-Z0-9_-]+)', "阿里云盘"),
    (r'(https?://(?:pan\.)?quark\.cn/s/[a-zA-Z0-9_-]+)', "夸克网盘"),
    (r'(https?://cloud\.189\.cn/s/[a-zA-Z0-9_-]+)', "天翼云盘"),
    (r'(https?://pan\.xunlei\.com/s/[a-zA-Z0-9_-]+)', "迅雷云盘"),
    (r'(https?://115\.com/s/[a-zA-Z0-9_-]+)', "115网盘"),
    (r'(https?://123pan\.com/s/[a-zA-Z0-9_-]+)', "123云盘"),
    (r'(https?://www\.123pan\.com/s/[a-zA-Z0-9_-]+)', "123云盘"),
    (r'(https?://(?:www\.)?lanzou[a-z]\.com/[a-zA-Z0-9/]+)', "蓝奏云"),
    (r'(https?://(?:www\.)?lanzou[a-z]\.cn/[a-zA-Z0-9/]+)', "蓝奏云"),
]

# 已登录可自动下载的网盘
AUTO_PAN_KEYS = {"aliyun", "baidu", "quark", "tianyi", "xunlei"}

# 网盘名称 → key 映射
PAN_NAME_TO_KEY = {
    "百度网盘": "baidu", "百度云": "baidu",
    "阿里云盘": "aliyun", "阿里云": "aliyun",
    "夸克网盘": "quark", "夸克": "quark",
    "天翼云盘": "tianyi", "天翼云": "tianyi",
    "迅雷云盘": "xunlei", "迅雷": "xunlei",
    "115网盘": "115", "115": "115",
    "123云盘": "123pan", "123pan": "123pan",
    "蓝奏云": "lanzou", "蓝奏": "lanzou",
}

# ─── 本地文件扫描 ───────────────────────────────────

def scan_local_files():
    """扫描 videos 目录，返回已有文件列表"""
    if not VIDEO_DIR.exists():
        return {"files": [], "total_size_mb": 0, "count": 0}
    
    files = []
    total_size = 0
    for f in sorted(VIDEO_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
            size_mb = round(f.stat().st_size / 1024 / 1024, 1)
            total_size += f.stat().st_size
            files.append({
                "name": f.name,
                "size_mb": size_mb,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "path": str(f)
            })
    
    return {
        "files": files,
        "total_size_mb": round(total_size / 1024 / 1024, 1),
        "count": len(files)
    }


def match_local_files(local_data, db_movies):
    """将本地文件与媒体库匹配，标注已下载状态"""
    local_names = {f["name"] for f in local_data["files"]}
    
    # 构建匹配规则：对每部剧/电影，检查是否有匹配的本地文件
    for movie in db_movies:
        movie["_local_files"] = []
        movie["_downloaded"] = False
        
        title = movie.get("title", "")
        episodes = movie.get("episodes", 0)
        movie_type = movie.get("type", "电影")
        
        # 电影匹配：文件名包含标题
        if movie_type == "电影":
            matching = [f for f in local_data["files"] if title in f["name"] or _normalize(title) in _normalize(f["name"])]
            if matching:
                movie["_local_files"] = matching
                movie["_downloaded"] = True
        else:
            # 电视剧匹配：匹配第X集
            ep_matched = []
            for f in local_data["files"]:
                # 匹配模式: S01E01, ep01, 第1集, E01
                for ep_num in range(1, (episodes or 99) + 1):
                    patterns = [
                        f"S{1:02d}E{ep_num:02d}",
                        f"ep{ep_num:02d}",
                        f"E{ep_num:02d}",
                        f"第{ep_num}集",
                        f".{ep_num:02d}.",
                    ]
                    if title in f["name"] and any(p.lower() in f["name"].lower() for p in patterns):
                        ep_matched.append({"episode": ep_num, **f})
                        break
            if ep_matched:
                movie["_local_files"] = ep_matched
                movie["_downloaded"] = len(ep_matched) >= episodes * 0.8  # 下载80%以上算完整


def _normalize(s):
    """简单的文件名归一化"""
    s = re.sub(r'[^\w\u4e00-\u9fff]', '', s.lower())
    return s


# ─── yt-dlp 下载引擎 ───────────────────────────────────

class YtDlpDownloader:
    """使用 yt-dlp 下载视频"""
    
    def __init__(self, download_manager):
        self.dm = download_manager

    def _set_item(self, item, **kwargs):
        """线程安全地更新 item 字典并保存队列"""
        with self.dm._queue_lock:
            item.update(kwargs)
            self.dm._save_queue()

    def add_url_download(self, url, title=None, episode=None, season=None):
        """添加URL下载任务（支持B站/YouTube等）"""
        if not title:
            title = f"URL_{int(time.time())}"
        
        item = {
            "id": f"url_{int(time.time() * 1000)}_{os.urandom(2).hex()}",
            "title": title,
            "url": url,
            "episode": episode,
            "season": season,
            "filename": "",
            "dl_type": "ytdlp",
            "status": "pending",
            "progress": 0,
            "speed": "0 MB/s",
            "added_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None,
            "output_path": str(VIDEO_DIR)
        }
        
        self.dm.queue.append(item)
        self.dm._save_queue()
        return item["id"]
    
    def execute_download(self, item):
        """实际执行 yt-dlp / aria2c 下载 — 带并发控制（最多3个）和失败重试"""
        # Phase 1: 并发控制
        acquired = _download_semaphore.acquire(timeout=120)
        if not acquired:
            self._set_item(item, status="failed", error="等待下载槽位超时（2分钟）")
            return
        try:
            self._do_download(item)
            # Phase 1: 失败自动重试1次
            if item.get("status") == "failed" and not item.get("_retried"):
                self._set_item(item, _retried=True, status="retrying", error=None, progress=0)
                self._do_download(item)
        except Exception as e:
            self._set_item(item, status="failed", error=str(e))
        finally:
            if acquired:
                _download_semaphore.release()

    def _do_download(self, item):
        """下载核心逻辑（不含并发控制）"""
        self._set_item(item, status="downloading", started_at=datetime.now().isoformat())
        
        try:
            output_template = str(VIDEO_DIR / "%(title)s.%(ext)s")
            
            url = item["url"]
            is_magnet = url.startswith("magnet:")
            # 检测网盘链接
            is_quark = bool(re.search(r'quark\.cn/s/', url))
            is_aliyun = bool(re.search(r'(?:aliyundrive\.com|alipan\.com)/s/', url))
            is_baidu = bool(re.search(r'pan\.baidu\.com/s/', url))
            
            # ── 夸克网盘 ──
            if is_quark and _QUARK_ENGINE:
                # 夸克网盘下载
                title = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', item.get("title", "download"))
                output_path = VIDEO_DIR / f"{title}_{int(time.time())}.mp4"
                
                def quark_progress(pct, msg):
                    item["progress"] = pct
                    item["speed"] = msg
                
                result = _QUARK_ENGINE.download(
                    url, title, progress_callback=quark_progress
                )
                
                if result.get("success"):
                    self._set_item(item, status="completed", progress=100, filepath=result.get("filepath"), size_mb=result.get("size_mb", 0))
                    self.dm._scan_and_refresh()
                    return

                self._set_item(item, status="failed", error=result.get("error", "夸克网盘下载失败"))
                return
            
            # ── 阿里云盘 ──
            if is_aliyun and _ALIYUN_ENGINE:
                title = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', item.get("title", "download"))
                
                def aliyun_progress(pct, msg):
                    item["progress"] = pct
                    item["speed"] = msg
                
                result = _ALIYUN_ENGINE.download(
                    url, title, progress_callback=aliyun_progress
                )
                
                if result.get("success"):
                    self._set_item(item, status="completed", progress=100, filepath=result.get("filepath"), size_mb=result.get("size_mb", 0))
                    self.dm._scan_and_refresh()
                    return

                self._set_item(item, status="failed", error=result.get("error", "阿里云盘下载失败"))
                return
            
            # ── 百度网盘 ──
            if is_baidu and _BAIDU_ENGINE:
                title = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', item.get("title", "download"))
                
                def baidu_progress(pct, msg):
                    item["progress"] = pct
                    item["speed"] = msg
                
                result = _BAIDU_ENGINE.download(
                    url, title, progress_callback=baidu_progress
                )
                
                if result.get("success"):
                    self._set_item(item, status="completed", progress=100, filepath=result.get("filepath"), size_mb=result.get("size_mb", 0))
                    self.dm._scan_and_refresh()
                    return

                self._set_item(item, status="failed", error=result.get("error", "百度网盘下载失败"))
                return
            
            if is_magnet:
                # 磁力链接用 aria2c（BT Tracker + DHT，文件监控进度）
                out_name = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', item.get("title", "download"))
                out_name = f"{out_name}_{int(time.time())}"  # 唯一名称防止冲突
                url_with_tr = url + BT_TRACKERS
                cmd = [
                    "aria2c",
                ] + ARIA2_BASE + [
                    "--bt-stop-timeout=0",      # 永不停止（Python控制超时）
                    "--timeout=30",
                    "--dir", str(VIDEO_DIR),
                    "--out", out_name + ".mp4",
                    "--console-log-level=warn",
                    "--dht-entry-point=dht.transmissionbt.com:6881",
                    url_with_tr
                ]
            else:
                # 流媒体用 yt-dlp（带分集支持）
                cmd = [YT_DLP_BIN, "-o", output_template]
                episode = item.get("episode")
                if episode is not None and isinstance(episode, int):
                    cmd += ["--playlist-start", str(episode), "--playlist-end", str(episode)]
                else:
                    cmd += ["--no-playlist"]
                cmd += ["-q", "--no-warnings", url]
            
            # Ensure PATH includes all tool locations
            env = os.environ.copy()
            extra_paths = "/Users/ayong/Library/Python/3.9/bin:/opt/homebrew/bin:/usr/local/bin"
            if "PATH" in env:
                if extra_paths not in env["PATH"]:
                    env["PATH"] = extra_paths + ":" + env["PATH"]
            else:
                env["PATH"] = extra_paths
            
            if is_magnet:
                process = sp.Popen(
                    cmd,
                    stdout=sp.DEVNULL,
                    stderr=sp.DEVNULL,
                    env=env
                )
            else:
                process = sp.Popen(
                    cmd,
                    stdout=sp.PIPE,
                    stderr=sp.STDOUT,
                    text=True,
                    bufsize=0,  # 无缓冲（aria2c用\\r输出进度）
                    env=env
                )
            
            # 注册到下载管理器，供cancel/remove杀进程
            with self.dm._queue_lock:
                self.dm.active_downloads[item["id"]] = process
            
            start_time = time.time()
            last_check = time.time()
            max_wait = 600  # 最多等10分钟
            
            if is_magnet:
                # ── 磁力链：文件监控方式 ──
                while True:
                    ret = process.poll()
                    now = time.time()
                    elapsed_wait = now - start_time
                    
                    # 每5秒检查文件系统是否有新文件
                    if now - last_check > 5:
                        files = list(VIDEO_DIR.glob("*"))
                        dl_files = [f for f in files 
                                   if f.stat().st_mtime > start_time - 5
                                   and f.suffix.lower() in ('.mp4', '.mkv', '.webm', '.avi')
                                   and f.stat().st_size > 1024]  # 至少1KB
                        
                        if dl_files:
                            newest = max(dl_files, key=lambda f: f.stat().st_mtime)
                            size_mb = newest.stat().st_size / 1024 / 1024
                            speed = size_mb / elapsed_wait if elapsed_wait > 0 else 0
                            estimated = max(2000, size_mb * 3)
                            progress = min(99, int(size_mb / estimated * 100))
                            
                            self._set_item(item, progress=max(item.get("progress", 0), progress), speed=f"{speed:.2f} MB/s", error=None)
                        else:
                            # 还没连上，显示等待时间
                            if elapsed_wait < 30:
                                speed_text = "连接DHT..."
                            elif elapsed_wait < 120:
                                speed_text = f"等待节点 ({elapsed_wait:.0f}s)"
                            else:
                                speed_text = f"搜索中 ({elapsed_wait:.0f}s)"
                            self._set_item(item, speed=speed_text, progress=3)
                        
                        last_check = now
                    
                    if ret is not None:
                        break  # aria2c自己退出了
                    if now - start_time > max_wait:
                        process.kill()
                        break
                    
                    time.sleep(1)
            else:
                # ── yt-dlp：标准输出解析 ──
                for line in process.stdout:
                    elapsed = time.time() - start_time
                    if elapsed > max_wait:
                        process.kill()
                        self._set_item(item, status="failed", error="下载超时（10分钟）")
                        break
                    if time.time() - last_check > 1:
                        self._set_item(item, progress=min(99, int(elapsed * 2)), speed=f"{elapsed:.1f}s")
                        last_check = time.time()
                try:
                    process.wait(timeout=10)
                except sp.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            
            # 清理进程记录
            with self.dm._queue_lock:
                self.dm.active_downloads.pop(item["id"], None)
            elapsed = time.time() - start_time
            
            # ── 查找下载完成的文件 ──
            if is_magnet:
                # 精确名称匹配
                expected_files = list(VIDEO_DIR.glob(f"{out_name}*"))
                if not expected_files:
                    # 回退：扫描所有新出现的视频文件
                    all_files = list(VIDEO_DIR.glob("*"))
                    expected_files = [f for f in all_files 
                                    if f.stat().st_mtime > start_time - 5
                                    and f.suffix.lower() in ('.mp4', '.mkv', '.webm', '.avi')
                                    and f.stat().st_size > 1024 * 1024]  # >1MB
                
                if expected_files:
                    newest = max(expected_files, key=lambda f: f.stat().st_size)
                    self._set_item(item, status="completed", completed_at=datetime.now().isoformat(), filename=newest.name, size=newest.stat().st_size, size_mb=round(newest.stat().st_size / 1024 / 1024, 1), progress=100, speed=f"耗时{elapsed:.0f}s")
                else:
                    self._set_item(item, status="failed", error=f"无做种或下载超时（{elapsed:.0f}s）")
            else:
                # yt-dlp: scan for any new video file
                downloaded_files = list(VIDEO_DIR.glob("*"))
                new_files = [f for f in downloaded_files 
                           if f.stat().st_mtime > start_time - 2 
                           and f.suffix.lower() in ('.mp4', '.mkv', '.webm')]
                if new_files:
                    newest = max(new_files, key=lambda f: f.stat().st_mtime)
                    self._set_item(item, status="completed", completed_at=datetime.now().isoformat(), filename=newest.name, size=newest.stat().st_size, size_mb=round(newest.stat().st_size / 1024 / 1024, 1), progress=100)
                else:
                    self._set_item(item, status="failed", error="下载完成但未找到视频文件，链接可能无效或格式不支持")

        except Exception as e:
            with self.dm._queue_lock:
                self.dm.active_downloads.pop(item["id"], None)
            self._set_item(item, status="failed", error=str(e))

        self.dm._save_queue()


# ─── 批量下载 ───────────────────────────────────

def batch_download_series(download_manager, series_info, episodes_range=None):
    """批量下载电视剧全部或指定剧集"""
    title = series_info.get("title", "")
    total_eps = series_info.get("episodes", 0)
    
    if not episodes_range:
        episodes_range = range(1, total_eps + 1)
    
    added_ids = []
    
    for ep_num in episodes_range:
        # 先用DownloadManager的add方法创建普通下载任务
        # 这里假设 download_urls 已配置
        urls = series_info.get("download_urls", [])
        if urls and ep_num <= len(urls):
            url = urls[ep_num - 1]
            ep_title = f"{title} S01E{ep_num:02d}"
            filename = f"{title}.S01E{ep_num:02d}.mp4"
            item_id = download_manager.add(ep_title, url, episode=ep_num, output_name=filename)
            added_ids.append(item_id)
    
    return added_ids


# ─── 配置文件管理 ───────────────────────────────────

def get_database():
    """读取数据库"""
    if DB_FILE.exists():
        try:
            with open(DB_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[get_database] error: {e}")
    return {"movies": [], "last_updated": ""}


def update_download_urls(media_id, urls):
    """更新某条媒体的下载链接"""
    db = get_database()
    for m in db.get("movies", []):
        if m.get("id") == media_id:
            m["download_urls"] = urls
            break
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


# ─── 自动搜索下载源（仅网盘） ─────────────────────────

def _curl_fetch(url, timeout=12):
    """用 curl 替代 urllib 获取网页内容（系统 Python 的 LibreSSL 太旧，无法连大部分 HTTPS）"""
    try:
        proc = sp.run(
            ["curl", "-sL", "--max-time", str(timeout),
             "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
             "-H", "Accept-Language: zh-CN,zh;q=0.9",
             url],
            capture_output=True, text=True, timeout=timeout+5,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        return ""
    except Exception as ex:
        print(f"[_curl_fetch] error for {url[:80]}: {ex}")
        return ""


def auto_search_sources(title, year=None, media_type="电影", max_results=15):
    """自动搜索网盘下载源 — 深度爬取资源页提取网盘分享链接（使用 curl 而非 urllib）"""
    results = []
    dedup = set()

    # 只搜网盘相关关键词
    search_terms = [
        f"{title} 百度网盘",
        f"{title} 阿里云盘",
        f"{title} 夸克网盘",
        f"{title} 网盘",
        f"{title} 资源分享",
        f"{title} {year} 网盘" if year else "",
    ]
    search_terms = [t for t in search_terms if t]

    def extract_pan_from_page(page_url):
        """访问页面提取网盘分享链接"""
        found = []
        html = _curl_fetch(page_url, timeout=10)
        if not html:
            return found

        # 提取网盘分享链接
        for pattern, pan_name in PAN_URL_PATTERNS:
            for m in re.finditer(pattern, html, re.I):
                url = m.group(1)
                if url not in dedup:
                    dedup.add(url)
                    # 尝试提取密码
                    pwd = None
                    pwd_match = re.search(r'(?:pwd|密码|提取码)[=：:\s]*([a-zA-Z0-9]{4,6})', html[max(0, m.end()-200):m.end()+200])
                    if pwd_match:
                        pwd = pwd_match.group(1)
                        # 避免已有 pwd 参数时重复追加
                        if '?pwd=' in url or '&pwd=' in url:
                            if pwd not in url:
                                url = f"{url}&pwd={pwd}"
                        else:
                            url = f"{url}?pwd={pwd}"

                    found.append({
                        "url": url,
                        "title": f"{pan_name}: {title}",
                        "source": pan_name,
                        "_pan": pan_name,
                        "_pan_key": PAN_NAME_TO_KEY.get(pan_name, ""),
                        "_pwd": pwd,
                    })

        # 也提取页面中的提取码（与最近一个网盘链接关联）
        # 有些页面用 "密码：xxxx" 格式
        pwd_matches = re.finditer(r'(?:密码|提取码|验证码)[：:]\s*([a-zA-Z0-9]{4,6})', html)
        for pm in pwd_matches:
            # 查找这个密码附近是否有网盘链接
            pos = pm.start()
            nearby_html = html[max(0, pos-500):pos+100]
            for pattern, pan_name in PAN_URL_PATTERNS:
                url_match = re.search(pattern, nearby_html, re.I)
                if url_match:
                    url = url_match.group(1)
                    if url not in dedup:
                        dedup.add(url)
                        found.append({
                            "url": url + "?pwd=" + pm.group(1),
                            "title": f"{pan_name}: {title}",
                            "source": pan_name,
                            "_pan": pan_name,
                            "_pan_key": PAN_NAME_TO_KEY.get(pan_name, ""),
                            "_pwd": pm.group(1),
                        })
        return found

    # Bing 搜索（替代被墙的 DuckDuckGo，使用 curl 绕过系统 Python SSL 问题）
    pan_found_count = 0
    for term in search_terms:
        if pan_found_count >= 8:
            break

        term_pan = None
        for pk, pn in [('百度网盘','百度网盘'),('百度云','百度网盘'),('阿里云盘','阿里云盘'),
                      ('夸克网盘','夸克网盘'),('天翼云盘','天翼云盘'),
                      ('迅雷云盘','迅雷云盘'),('网盘','网盘')]:
            if pk in term:
                term_pan = pn
                break

        try:
            from urllib.parse import urlencode
            bing_url = f"https://www.bing.com/search?{urlencode({'q': term, 'cc': 'cn', 'setlang': 'zh-cn'})}"
            html = _curl_fetch(bing_url, timeout=12)
            if not html or len(html) < 5000:
                continue

            # Bing 结果解析：li.b_algo > h2 > a
            for block in re.finditer(r'<li class="b_algo[^"]*".*?</li>', html, re.I | re.S):
                a_href = re.search(r'href="(https?://[^"]+)"', block.group())
                h2_text = re.search(r'<h2[^>]*>(.*?)</h2>', block.group(), re.S)
                if not (a_href and h2_text):
                    continue
                url = a_href.group(1)
                text = re.sub(r'<[^>]+>', '', h2_text.group(1)).strip()
                if not text:
                    continue

                # 跳过 Bing 自己的链接
                if 'r.bing.com' in url or 'bing.com' in url:
                    continue

                if url in dedup:
                    continue
                dedup.add(url)

                # 检查这个 URL 是否本身就是网盘链接
                is_pan_directly = False
                for pattern, pan_name in PAN_URL_PATTERNS:
                    m = re.search(pattern, url, re.I)
                    if m:
                        pan_url = m.group(1)
                        found_item = {
                            "url": pan_url,
                            "title": f"{pan_name}: {title}",
                            "source": pan_name,
                            "_pan": pan_name,
                            "_pan_key": PAN_NAME_TO_KEY.get(pan_name, ""),
                        }
                        results.append(found_item)
                        pan_found_count += 1
                        is_pan_directly = True
                        break

                if is_pan_directly:
                    continue

                # 检查是否是资源页（包含网盘关键词）
                is_resource_page = any(k in (text + url) for k in ['网盘', '百度网盘', '阿里云盘', '夸克', '天翼云', '迅雷云', 'pan.baidu', 'aliyundrive', 'alipan', 'quark.cn', 'cloud.189', 'pan.xunlei'])

                if is_resource_page:
                    # 深度爬取资源页
                    deep = extract_pan_from_page(url)
                    results.extend(deep)
                    pan_found_count += len(deep)

            # 也直接提取整个页面中的网盘链接（Bing 搜索结果页可能有直链）
            if pan_found_count < 3:
                for pattern, pan_name in PAN_URL_PATTERNS:
                    for m in re.finditer(pattern, html, re.I):
                        pan_url = m.group(1)
                        if pan_url not in dedup:
                            dedup.add(pan_url)
                            # 尝试提取密码
                            pwd = None
                            pwd_match = re.search(r'(?:pwd|密码|提取码)[=：:\s]*([a-zA-Z0-9]{4,6})', html[max(0, m.end()-200):m.end()+200])
                            if pwd_match:
                                pwd = pwd_match.group(1)
                                pan_url = f"{pan_url}?pwd={pwd}" if '?' not in pan_url else f"{pan_url}&pwd={pwd}"
                            found_item = {
                                "url": pan_url,
                                "title": f"{pan_name}: {title}",
                                "source": pan_name,
                                "_pan": pan_name,
                                "_pan_key": PAN_NAME_TO_KEY.get(pan_name, ""),
                                "_pwd": pwd,
                            }
                            results.append(found_item)
                            pan_found_count += 1
        except Exception as ex:
            print(f"[auto_search_sources] bing search '{term}' failed: {ex}")
            continue

    # 按网盘类型排序：已登录的可自动下载的排前面
    def sort_key(item):
        pan_key = item.get("_pan_key", "")
        if pan_key in AUTO_PAN_KEYS:
            return 0  # 可自动下载
        return 1  # 需手动

    results.sort(key=sort_key)
    return results[:max_results]


def test_source_availability(source):
    """快速检测一个源是否可用（排除预告片），返回检测结果"""
    url = source.get("url", "")
    result = dict(source)
    result["available"] = False
    result["speed_ms"] = None
    result["duration_m"] = None
    result["size_mb"] = None
    result["test_error"] = None
    
    start_t = time.time()
    
    try:
        # ── 磁力链接 ──
        if url.startswith("magnet:"):
            # 拼接tracker到磁链URL
            url_with_tr = url + BT_TRACKERS
            proc = sp.run(
                ["aria2c"] + ARIA2_BASE + ["--bt-stop-timeout=15",
                 "--bt-max-peers=50", "--max-connection-per-server=4",
                 "--split=4", "-d", "/tmp/aria2test",
                 "--dht-entry-point=dht.transmissionbt.com:6881", url_with_tr],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH","")}
            )
            output = proc.stdout + proc.stderr
            elapsed = time.time() - start_t
            result["speed_ms"] = round(elapsed * 1000)
            
            # 精确判断是否有做种：
            # "Download complete" = 100%有种子
            # "CN:"后跟数字>0且有DL: > 0 = 正在下载
            # 其他情况（仅GID/Allocated）= 仅tracker响应，不一定有做种
            if "Download complete" in output:
                result["available"] = True
                result["duration_m"] = 148
                result["label"] = "有做种(已完成)"
            elif "CN:" in output and "DL:0B" not in output and "DL:" in output:
                # 有连接且有下载速度
                result["available"] = True
                result["label"] = "有做种"
            elif "CN:" in output and "CN:0" not in output:
                # 有连接但还没开始下载
                result["available"] = True
                result["label"] = "可尝试(有连接)"
            elif "No peer" in output or "timeout" in output.lower() or "retry" in output.lower():
                # tracker未找到做种者但可能仍有DHT做种
                result["available"] = True  # 允许尝试！
                result["test_error"] = "tracker无做种（可尝试DHT）"
                result["label"] = "可尝试DHT"
            elif "aborted" in output.lower() or "error" in output.lower():
                result["available"] = False
                result["test_error"] = "无做种者"
                result["label"] = "无做种"
            else:
                result["available"] = True
                result["label"] = "可能可用"
        
        # ── YouTube / 解说类 ──
        elif 'youtube.com' in url or 'youtu.be' in url:
            proc = sp.run(
                [YT_DLP_BIN, "--no-download",
                 "--print", "duration", url],
                capture_output=True, text=True, timeout=15
            )
            elapsed = time.time() - start_t
            result["speed_ms"] = round(elapsed * 1000)
            dur_str = proc.stdout.strip()
            try:
                dur_sec = int(dur_str)
                result["duration_m"] = round(dur_sec / 60, 1)
            except:
                result["duration_m"] = 0
            
            if result["duration_m"] and result["duration_m"] >= 90:
                result["available"] = True
                result["label"] = f"完整影片({result['duration_m']}min)"
            else:
                result["available"] = False
                result["test_error"] = f"解说/预告({result['duration_m']}min)"
                result["label"] = "解说/预告片"
        
        # ── 流媒体（腾讯/爱奇艺/优酷/B站） ──
        elif any(d in url for d in ['v.qq.com', 'iqiyi.com', 'youku.com', 'mgtv.com']):
            proc = sp.run(
                [YT_DLP_BIN, "--no-download",
                 "--print", "duration", url],
                capture_output=True, text=True, timeout=15
            )
            elapsed = time.time() - start_t
            result["speed_ms"] = round(elapsed * 1000)
            dur_str = proc.stdout.strip()
            try:
                dur_sec = int(dur_str)
                result["duration_m"] = round(dur_sec / 60, 1)
            except:
                result["duration_m"] = 0
            
            if result["duration_m"] and result["duration_m"] >= 90:
                result["available"] = True
                result["label"] = f"完整影片({result['duration_m']}min)"
            else:
                result["available"] = False
                if result["duration_m"] and result["duration_m"] > 0:
                    result["test_error"] = f"预告片段({result['duration_m']}min)"
                    result["label"] = "预告片"
                else:
                    result["test_error"] = "需会员/解析失败"
                    result["label"] = "需会员"
        
        # ── B站 ──
        elif 'bilibili.com' in url:
            proc = sp.run(
                [YT_DLP_BIN, "--no-download",
                 "--print", "duration", url],
                capture_output=True, text=True, timeout=15
            )
            elapsed = time.time() - start_t
            result["speed_ms"] = round(elapsed * 1000)
            dur_str = proc.stdout.strip()
            try:
                dur_sec = int(dur_str)
                result["duration_m"] = round(dur_sec / 60, 1)
            except:
                result["duration_m"] = 0
            
            if result["duration_m"] and result["duration_m"] >= 90:
                result["available"] = True
                result["label"] = f"完整影片({result['duration_m']}min)"
            else:
                result["available"] = False
                result["test_error"] = "非完整影片" if result["duration_m"] else "无法解析"
                result["label"] = "非完整"
        
        # ── 直链 ──
        elif any(ext in url for ext in ['.mp4', '.mkv', '.avi', '.ts', '.webm']):
            req = urlreq.Request(url, method="HEAD")
            with urlreq.urlopen(req, timeout=8) as resp:
                content_len = resp.headers.get("Content-Length")
                if content_len:
                    size_mb = int(content_len) / 1024 / 1024
                    result["size_mb"] = round(size_mb, 1)
                    result["available"] = size_mb > 50  # >50MB = real file
                    result["label"] = f"直链({result['size_mb']:.0f}MB)"
                    if size_mb < 50:
                        result["test_error"] = f"文件太小({result['size_mb']:.0f}MB)"
                else:
                    result["available"] = True
                    result["label"] = "直链(未知大小)"
                result["speed_ms"] = round((time.time() - start_t) * 1000)
        
        # ── 网盘链接（手动打开） ──
        elif any(pan in url for pan in ['pan.baidu.com', 'aliyundrive.com', 'alipan.com',
                                         'quark.cn', '115.com', 'cloud.189.cn',
                                         'pan.xunlei.com', '123pan.com', 'lanzou']):
            pan_name = None
            for p, n in [('pan.baidu.com', '百度网盘'), ('aliyundrive.com', '阿里云盘'),
                         ('alipan.com', '阿里云盘'), ('quark.cn', '夸克网盘'),
                         ('115.com', '115网盘'), ('cloud.189.cn', '天翼云盘'),
                         ('pan.xunlei.com', '迅雷云盘'), ('123pan.com', '123云盘'),
                         ('lanzou', '蓝奏云')]:
                if p in url:
                    pan_name = n
                    break
            result["available"] = False
            result["manual_open"] = True
            result["_pan"] = pan_name or "网盘"
            result["label"] = f"需手动去{pan_name or '网盘'}下载"
            result["test_error"] = "需手动下载"
        
        # ── 资源页 ──
        else:
            req = urlreq.Request(url, method="HEAD")
            with urlreq.urlopen(req, timeout=8) as resp:
                elapsed = time.time() - start_t
                result["speed_ms"] = round(elapsed * 1000)
                # 资源页不可用 yt-dlp 下载，需要手动打开
                result["available"] = False
                result["manual_open"] = True
                result["label"] = "需手动去页面获取链接"
                result["test_error"] = "手动操作"
    
    except sp.TimeoutExpired:
        result["speed_ms"] = 15000
        result["test_error"] = "超时"
        result["label"] = "连接超时"
    except Exception as e:
        result["speed_ms"] = round((time.time() - start_t) * 1000)
        result["test_error"] = str(e)[:40]
        result["label"] = "不可用"
    
    return result


def test_all_sources(sources):
    """批量检测所有源，排除预告片/解说/短片段，按速度排序"""
    import concurrent.futures
    
    tested = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_to_src = {executor.submit(test_source_availability, s): s for s in sources}
        for future in concurrent.futures.as_completed(future_to_src):
            try:
                result = future.result()
                tested.append(result)
            except Exception as e:
                s = future_to_src[future]
                s["available"] = False
                s["label"] = "检测失败"
                s["test_error"] = str(e)[:40]
                tested.append(s)
    
    # 排除不可用的（预告片、解说、失效资源）
    available = [s for s in tested if s.get("available")]
    unavailable = [s for s in tested if not s.get("available")]
    
    # 可用源按速度排序（快在前）
    available.sort(key=lambda s: s.get("speed_ms") or 99999)
    
    # 不可用源排在最后，也按速度排
    unavailable.sort(key=lambda s: s.get("speed_ms") or 99999)
    
    return available + unavailable


import uuid as _uuid

AUTO_FIND_RESULTS = {}


def _auto_find_set_result(job_id, data):
    AUTO_FIND_RESULTS[job_id] = {"status": "done", "data": data}


def _auto_find_set_error(job_id, error):
    AUTO_FIND_RESULTS[job_id] = {"status": "error", "error": str(error)}


import uuid as _uuid

AUTO_FIND_RESULTS = {}
MAX_AUTO_FIND_AGE_SECONDS = 30 * 60


def _auto_find_set_result(job_id, data):
    AUTO_FIND_RESULTS[job_id] = {"status": "done", "data": data}


def _auto_find_set_error(job_id, error):
    AUTO_FIND_RESULTS[job_id] = {"status": "error", "error": str(error)}


def start_auto_search_task(media_id, media):
    job_id = _uuid.uuid4().hex
    AUTO_FIND_RESULTS[job_id] = {"status": "processing", "media_id": media_id}

    def task():
        try:
            title = media.get("title", "")
            year = media.get("year", "")
            mtype = media.get("type", "电影")
            episodes = media.get("episodes", 0)

            sources = auto_search_sources(title, year, mtype)
            if not sources:
                _auto_find_set_result(
                    job_id,
                    {
                        "success": False,
                        "error": "未找到任何下载源",
                        "manual": True,
                        "job_id": job_id,
                    },
                )
                return

            tested = test_all_sources(sources)
            _auto_find_set_result(
                job_id,
                {
                    "success": True,
                    "job_id": job_id,
                    "media_id": media_id,
                    "sources": tested,
                    "is_series": mtype == "电视剧",
                    "episodes": episodes,
                },
            )
        except Exception as e:
            _auto_find_set_error(job_id, e)

    t = threading.Thread(target=task, daemon=True)
    t.start()
    return job_id


def get_auto_find_result(job_id):
    return AUTO_FIND_RESULTS.get(job_id)


def handle_auto_download(server, query, download_manager):
    """处理 /api/auto-download — 异步提交后立即返回 job_id"""
    media_id = query.get("id", "")
    if not media_id:
        server.send_json({"success": False, "error": "缺少媒体ID", "job_id": None}, 400)
        return

    db = get_database()
    media = None
    for m in db.get("movies", []):
        if m.get("id") == media_id:
            media = m
            break

    if not media:
        server.send_json({"success": False, "error": "未找到该媒体", "job_id": None}, 404)
        return

    try:
        job_id = start_auto_search_task(media_id, media)
    except Exception as e:
        server.send_json({"success": False, "error": str(e), "job_id": None}, 500)
        return

    server.send_json(
        {
            "success": True,
            "job_id": job_id,
            "media_id": media_id,
            "message": "搜索任务已提交，请通过 /api/auto-result 轮询结果",
        }
    )


def handle_auto_find(server, query, download_manager):
    """处理 /api/auto-find — 异步提交后立即返回 job_id"""
    media_id = query.get("id", "")
    if not media_id:
        server.send_json({"success": False, "error": "缺少媒体ID", "job_id": None}, 400)
        return

    db = get_database()
    media = None
    for m in db.get("movies", []):
        if m.get("id") == media_id:
            media = m
            break

    if not media:
        server.send_json({"success": False, "error": "未找到该媒体", "job_id": None}, 404)
        return

    try:
        job_id = start_auto_search_task(media_id, media)
    except Exception as e:
        server.send_json({"success": False, "error": str(e), "job_id": None}, 500)
        return

    server.send_json(
        {
            "success": True,
            "job_id": job_id,
            "media_id": media_id,
            "message": "搜索任务已提交，请通过 /api/auto-result 轮询结果",
        }
    )


def handle_auto_result(server, query, download_manager):
    """处理 /api/auto-result — 返回指定 job 的结果或 processing 状态"""
    job_id = query.get("job_id", "")
    if not job_id:
        server.send_json({"success": False, "error": "缺少 job_id"}, 400)
        return

    result = get_auto_find_result(job_id)
    if result is None:
        server.send_json({"success": False, "error": "job 不存在或已过期"}, 404)
        return

    server.send_json({"success": True, **result})






# ─── API 处理器 ───────────────────────────────────

def handle_local_files(server, query=None, download_manager=None):
    """处理 /api/local-files 请求"""
    data = scan_local_files()
    server.send_json(data)


def handle_dl_url(server, query, download_manager):
    """处理 /api/dl-url 请求 — 从外部URL下载"""
    url = query.get("url", "")
    title = query.get("title", "")

    if not url:
        server.send_json({"success": False, "error": "缺少URL参数"}, 400)
        return

    dl = YtDlpDownloader(download_manager)
    item_id = dl.add_url_download(url, title=title or url)

    # Start download in background
    item = next((i for i in download_manager.queue if i["id"] == item_id), None)
    if item is None:
        server.send_json({"success": False, "error": "下载任务创建失败"})
        return
    _download_executor.submit(dl.execute_download, item)

    server.send_json({"success": True, "id": item_id, "title": title or url})


def handle_dl_batch(server, query, download_manager):
    """处理 /api/dl-batch 请求 — 批量下载"""
    media_id = query.get("id", "")
    start_ep = int(query.get("start", "1"))
    end_ep = int(query.get("end", "0"))
    
    db = get_database()
    series = None
    for m in db.get("movies", []):
        if m.get("id") == media_id:
            series = m
            break
    
    if not series:
        server.send_json({"success": False, "error": "未找到该媒体"}, 404)
        return
    
    total = series.get("episodes", 0)
    if end_ep <= 0:
        end_ep = total
    
    episode_range = range(start_ep, end_ep + 1)
    ids = batch_download_series(download_manager, series, episode_range)
    
    server.send_json({
        "success": True,
        "total": len(ids),
        "ids": ids,
        "title": series.get("title", ""),
        "range": f"第{start_ep}集 - 第{end_ep}集"
    })


def handle_search_sources(server, query, download_manager):
    """处理 /api/search-sources — 按标题搜索下载源（用于外部搜索结果）"""
    title = query.get("title", "")
    year = query.get("year", "")
    mtype = query.get("type", "电影")
    
    if not title:
        server.send_json({"success": False, "error": "缺少标题"}, 400)
        return
    
    sources = auto_search_sources(title, year, mtype)
    
    import threading
    tested = []
    def run_tests():
        nonlocal tested
        tested = test_all_sources(sources)
    
    t = threading.Thread(target=run_tests, daemon=True)
    t.start()
    t.join(timeout=15)  # Phase 1.2: 从25s缩短到15s
    
    if t.is_alive():
        for s in sources:
            url = s.get("url", "")
            if url.startswith("magnet:"):
                s["available"] = True
                s["speed_badge"] = "⏱超时"
                s["label"] = "做种未知（测试超时）"
                s["type_label"] = "磁力链接"
                s["type_icon"] = "🔗"
            elif 'youtube.com' in url:
                s["available"] = False
                s["speed_badge"] = "⏱超时"
                s["label"] = "跳过测试"
                s["type_label"] = "YouTube"
                s["type_icon"] = "📺"
            else:
                s["available"] = True
                s["speed_badge"] = "⏱超时"
                s["label"] = "可用性未知"
    
    server.send_json({"success": True, "sources": sources})


# ═══════════════════════════════════════════════════════════
#  空间管理 API
# ═══════════════════════════════════════════════════════════

def handle_space(server, query=None, download_manager=None):
    """处理 /api/space 请求 - 返回磁盘空间信息"""
    try:
        from space_manager import (
            check_local_space, format_size, LocalSpaceManager,
            check_aliyun_space, check_baidu_space, check_quark_space
        )
    except ImportError:
        server.send_json({"success": False, "error": "空间管理器不可用"})
        return

    action = query.get("action", "info")

    if action == "info":
        # 返回空间信息
        local = check_local_space(str(VIDEO_DIR))
        aliyun = check_aliyun_space()
        baidu = check_baidu_space()
        quark = check_quark_space()

        server.send_json({
            "success": True,
            "local": {
                "total": local.get("total", 0),
                "used": local.get("used", 0),
                "available": local.get("available", 0),
                "total_fmt": format_size(local.get("total", 0)),
                "used_fmt": format_size(local.get("used", 0)),
                "available_fmt": format_size(local.get("available", 0)),
                "path": str(VIDEO_DIR),
            },
            "cloud": {
                "aliyun": aliyun,
                "baidu": baidu,
                "quark": quark,
            }
        })

    elif action == "cleanup":
        # 自动清理
        needed_str = query.get("needed", "0")
        try:
            needed = int(needed_str)
        except ValueError:
            needed = 0

        mgr = LocalSpaceManager(str(VIDEO_DIR))
        result = mgr.cleanup(needed)

        server.send_json({
            "success": result.get("error") is None,
            "freed": result.get("freed", 0),
            "freed_fmt": result.get("freed_human", "0B"),
            "deleted": result.get("deleted", []),
            "deleted_count": result.get("deleted_count", 0),
            "error": result.get("error"),
        })

    elif action == "candidates":
        # 获取可清理文件列表
        mgr = LocalSpaceManager(str(VIDEO_DIR))
        candidates = mgr.get_cleanup_candidates()

        server.send_json({
            "success": True,
            "candidates": candidates,
            "total": len(candidates),
        })

    else:
        server.send_json({"success": False, "error": f"未知操作: {action}"})




# ═══════════════════════════════════════════════════════════
#  订阅系统 — 一劳永逸追剧
# ═══════════════════════════════════════════════════════════

# ─── 订阅文件锁 ───────────────────────────────────
_SUBS_LOCK = threading.Lock()  # 进程内锁
_SUBS_FILE_LOCK_PATH = Path.home() / "services/streaming-server" / "subscriptions.lock"


def _lock_subs():
    """跨进程文件锁上下文管理器（使用fcntl，无需额外依赖）"""
    class _FileLock:
        def __enter__(self2):
            self2.fd = os.open(str(_SUBS_FILE_LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
            import fcntl
            fcntl.flock(self2.fd, fcntl.LOCK_EX)
            return self2
        def __exit__(self2, *args):
            import fcntl
            fcntl.flock(self2.fd, fcntl.LOCK_UN)
            os.close(self2.fd)
    return _FileLock()


SUBS_FILE = Path.home() / "services/streaming-server" / "subscriptions.json"


def _load_subs():
    """加载订阅列表（带文件锁）"""
    if not SUBS_FILE.exists():
        return []
    try:
        with _SUBS_LOCK:
            with open(SUBS_FILE) as f:
                return json.load(f)
    except Exception as e:
        print(f"[_load_subs] error: {e}")
        return []


def _save_subs(subs):
    """保存订阅列表（带文件锁）"""
    SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _SUBS_LOCK:
        with open(SUBS_FILE, "w") as f:
            json.dump(subs, f, ensure_ascii=False, indent=2)


def handle_subscribe(server, query, download_manager):
    """处理 /api/subscribe — 订阅电视剧，有新集自动下载"""
    media_id = query.get("id", "")
    if not media_id:
        server.send_json({"success": False, "error": "缺少媒体ID"}, 400)
        return

    db = get_database()
    media = None
    for m in db.get("movies", []):
        if m.get("id") == media_id:
            media = m
            break

    if not media:
        server.send_json({"success": False, "error": "未找到该媒体"}, 404)
        return

    title = media.get("title", "")
    year = media.get("year", "")
    episodes = media.get("episodes", 0)
    mtype = media.get("type", "电视剧")

    if mtype != "电视剧" or episodes <= 0:
        server.send_json({"success": False, "error": "仅支持电视剧订阅（需有集数信息）"}, 400)
        return

    with _lock_subs():
        subs = _load_subs()
        # 检查是否已订阅
        for s in subs:
            if s.get("title") == title and s.get("year") == year:
                server.send_json({"success": True, "already_subscribed": True, "subscription": s})
                return

        sub = {
            "id": media_id,
            "title": title,
            "year": year or "",
            "episodes": episodes,
            "downloaded_eps": [],       # 已下载的集号
            "added_at": datetime.now().isoformat(),
            "last_checked": None,
            "status": "active",
        }
        subs.append(sub)
        _save_subs(subs)

    # 立即执行一次搜索下载
    threading.Thread(target=check_subscription, args=(sub, download_manager), daemon=True).start()

    server.send_json({
        "success": True,
        "message": f"已订阅 {title}（共{episodes}集），开始搜索下载",
        "subscription": sub,
    })


def handle_unsubscribe(server, query, download_manager):
    """处理 /api/unsubscribe — 取消订阅"""
    media_id = query.get("id", "")
    title = query.get("title", "")

    with _lock_subs():
        subs = _load_subs()
        before = len(subs)

        if media_id:
            subs = [s for s in subs if s.get("id") != media_id]
        elif title:
            subs = [s for s in subs if s.get("title") != title]
        else:
            server.send_json({"success": False, "error": "缺少媒体ID或标题"}, 400)
            return

        _save_subs(subs)
    removed = before - len(subs)
    server.send_json({"success": True, "removed": removed, "remaining": len(subs)})


def handle_subscriptions(server, query, download_manager):
    """处理 /api/subscriptions — 列出所有订阅"""
    subs = _load_subs()
    server.send_json({"success": True, "subscriptions": subs, "count": len(subs)})


def check_subscription(sub, download_manager):
    """检查单个订阅，搜索并下载未有的剧集"""
    title = sub.get("title", "")
    year = sub.get("year", "")
    episodes = sub.get("episodes", 0)
    downloaded = set(sub.get("downloaded_eps", []))

    if not title or episodes <= 0:
        return

    # 1. 搜索下载源
    sources = auto_search_sources(title, year, "电视剧")
    if not sources:
        return

    # 2. 测速
    tested = test_all_sources(sources)
    available = [s for s in tested if s.get("available")]

    if not available:
        return

    # 3. 逐个剧集下载（使用 yt-dlp 分集参数）
    new_downloads = []
    best = available[0]
    url = best.get("url", "")

    for ep in range(1, episodes + 1):
        if ep in downloaded:
            continue

        ep_title = f"{title} EP{ep:02d}"
        # 对 yt-dlp 支持的源，加 --playlist-start/end 指定剧集；非播放列表源跳过
        dl = YtDlpDownloader(download_manager)
        item_id = dl.add_url_download(url, title=ep_title, episode=ep)
        item = next((i for i in download_manager.queue if i["id"] == item_id), None)
        if item is None:
            continue
        _download_executor.submit(dl.execute_download, item)
        downloaded.add(ep)
        new_downloads.append(ep)

        if len(new_downloads) >= 3:  # 每轮最多3集（避免队列爆炸）
            break

    # 更新已下载记录
    sub["downloaded_eps"] = sorted(downloaded)
    sub["last_checked"] = datetime.now().isoformat()

    # 保存到文件（线程安全，加锁保护）
    with _lock_subs():
        all_subs = _load_subs()
        for s in all_subs:
            if s.get("id") == sub.get("id"):
                s["downloaded_eps"] = sub["downloaded_eps"]
                s["last_checked"] = sub["last_checked"]
                break
        _save_subs(all_subs)

    # 记录日志
    if new_downloads:
        now = datetime.now().isoformat()
        log_entry = {"title": title, "downloaded": new_downloads, "time": now}
        log_file = Path.home() / "services/streaming-server" / "subscription_log.json"
        try:
            with open(log_file, "a+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                try:
                    log = json.load(f)
                except Exception:
                    log = []
                log.append(log_entry)
                f.seek(0)
                f.truncate(0)
                json.dump(log, f, ensure_ascii=False, indent=2)
                f.flush()
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            print(f"[check_subscription] log write error: {e}")


def check_all_subscriptions(download_manager):
    """检查所有活跃订阅"""
    subs = _load_subs()
    active = [s for s in subs if s.get("status") == "active"]
    for sub in active:
        try:
            check_subscription(sub, download_manager)
        except Exception as e:
            print(f"[check_all_subscriptions] error for {sub.get('title')}: {e}")


# ─── 订阅定时任务入口（cron调用） ───────────────────
def run_subscription_check():
    from stream_server_v3 import StreamHandler
    dm = StreamHandler.download_manager
    if dm is None:
        now = datetime.now().isoformat()
        print(f"[{now}] download_manager 未初始化，跳过")
        return

    check_all_subscriptions(dm)




def handle_subscription_log(server, query, download_manager):
    """处理 /api/subscription-log — 返回订阅下载记录"""
    log_file = Path.home() / "services/streaming-server" / "subscription_log.json"
    try:
        if log_file.exists():
            with open(log_file) as f:
                log = json.load(f)
        else:
            log = []
        server.send_json({"success": True, "log": log, "count": len(log)})
    except Exception as e:
        server.send_json({"success": False, "error": str(e)})



if __name__ == "__main__":
    import sys
    import socketserver
    # 导入主服务器的 StreamHandler（它会加载 dl_enhancer 作为下载增强）
    from stream_server_v3 import StreamHandler, PORT, VIDEO_DIR, DB_FILE
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), StreamHandler) as httpd:
        print(f"🎬 流媒体服务器 v3 (下载增强版) 已启动")
        print(f"📺 访问地址: http://localhost:{port}")
        print(f"📁 视频目录: {VIDEO_DIR}")
        print(f"📚 数据库: {DB_FILE}")
        print(f"⏹️  按 Ctrl+C 停止")
        httpd.serve_forever()


def handle_save_source(server, query, download_manager=None):
    """保存单个资源到当前影片"""
    import database
    media_id = (query.get("media_id") or "").strip()
    url = (query.get("url") or "").strip()
    source = (query.get("source") or "").strip()
    pwd = (query.get("pwd") or "").strip()
    if not media_id or not url:
        server.send_json({"ok": False, "msg": "缺少 media_id 或 url"})
        return
    item = {
        "url": url,
        "title": (query.get("title") or "").strip(),
        "source": source or "手动保存",
        "type": "external",
    }
    if pwd:
        item["pwd"] = pwd
    db = database.get_database()
    for m in db.get("movies", []):
        if m.get("id") == media_id:
            m.setdefault("resource_sources", []).append(item)
            with open(database.DB_FILE, "w", encoding="utf-8") as f:
                f.write(json.dumps(db, ensure_ascii=False, indent=2))
            server.send_json({"ok": True, "msg": "已保存"})
            return
    server.send_json({"ok": False, "msg": "未找到影片"})


def handle_save_search_to_library(server, query, download_manager=None):
    """外部搜索结果一键入库 => database.json"""
    import database
    title = (query.get("title") or "").strip()
    url = (query.get("url") or "").strip()
    if not title or not url:
        server.send_json({"ok": False, "msg": "缺少 title 或 url"})
        return
    db = database.get_database()
    mid = "ext-%d" % int(time.time() * 1000)
    entry = {
        "id": mid,
        "title": title,
        "year": (query.get("year") or "").strip(),
        "type": query.get("type") or "电影",
        "rating": float(query.get("rating") or 0),
        "genre": query.get("genre") or "",
        "poster": query.get("poster") or "",
        "douban_url": query.get("douban_url") or "",
        "douban_id": query.get("douban_id") or "",
        "episodes": int(query.get("episodes") or 0),
        "created_at": datetime.now().isoformat(),
        "resource_sources": [
            {
                "url": url,
                "title": query.get("title") or title,
                "source": query.get("source") or "外部入库",
                "type": "external",
                "pwd": query.get("pwd") or "",
                "added_at": datetime.now().isoformat(),
            }
        ],
    }
    db.setdefault("movies", []).append(entry)
    with open(database.DB_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(db, ensure_ascii=False, indent=2))
    server.send_json({"ok": True, "media_id": mid})
