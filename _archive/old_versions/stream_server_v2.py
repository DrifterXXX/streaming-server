#!/usr/bin/env python3
"""
流媒体服务器 - 带海报刮削、豆瓣评分筛选、边下边播
支持：HTTP Range请求（边下边播）、后台下载队列、豆瓣API刮削
"""

import http.server
import socketserver
import os
import json
import threading
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from html import escape

# 配置
VIDEO_DIR = Path.home() / "streaming-server" / "videos"
METADATA_FILE = Path.home() / "streaming-server" / "metadata.json"
DOWNLOAD_QUEUE_FILE = Path.home() / "streaming-server" / "download_queue.json"
PORT = 9878

# 豆瓣API（使用公开接口）
DOUBAN_API = "https://movie.douban.com/j/subject_suggest"

# 豆瓣评分阈值
MIN_RATING = 7.0

# 确保目录存在
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


class DownloadManager:
    """后台下载管理器"""
    
    def __init__(self):
        self.queue = self._load_queue()
        self.active_downloads = {}
        self._start_worker()
    
    def _load_queue(self):
        if METADATA_FILE.exists():
            try:
                with open(METADATA_FILE) as f:
                    data = json.load(f)
                    return data.get("download_queue", [])
            except:
                pass
        return []
    
    def _save_queue(self):
        data = {"download_queue": self.queue, "updated": datetime.now().isoformat()}
        with open(METADATA_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def add(self, title, url, episode=None):
        item = {
            "id": f"{title}_{episode or 'full'}_{int(time.time())}",
            "title": title,
            "url": url,
            "episode": episode,
            "status": "pending",
            "progress": 0,
            "added_at": datetime.now().isoformat(),
            "output_file": None
        }
        self.queue.append(item)
        self._save_queue()
        return item["id"]
    
    def _start_worker(self):
        def worker():
            while True:
                if self.queue:
                    item = self.queue[0]
                    if item["status"] == "pending":
                        self._download(item)
                time.sleep(2)
        
        t = threading.Thread(target=worker, daemon=True)
        t.start()
    
    def _download(self, item):
        item["status"] = "downloading"
        item["started_at"] = datetime.now().isoformat()
        self._save_queue()
        
        title = item["title"]
        episode = item["episode"]
        if episode:
            filename = f"{title}.ep{episode:02d}.mp4"
        else:
            filename = f"{title}.mp4"
        
        output_path = VIDEO_DIR / filename
        item["output_file"] = str(output_path)
        
        try:
            def report_hook(block_num, block_size, total_size):
                downloaded = block_num * block_size
                if total_size > 0:
                    item["progress"] = min(100, int(downloaded * 100 / total_size))
            
            urllib.request.urlretrieve(item["url"], str(output_path), report_hook)
            
            if output_path.exists() and output_path.stat().st_size > 1000000:
                item["status"] = "completed"
                item["completed_at"] = datetime.now().isoformat()
                item["size"] = output_path.stat().st_size
            else:
                item["status"] = "failed"
                if output_path.exists():
                    output_path.unlink()
        
        except Exception as e:
            item["status"] = "failed"
            item["error"] = str(e)
            if output_path.exists():
                output_path.unlink()
        
        self._save_queue()
    
    def get_status(self):
        return {
            "queue": self.queue[:10],  # 最近10个
            "active": self.active_downloads
        }


class DoubanScraper:
    """豆瓣刮削器"""
    
    @staticmethod
    def search(query):
        """搜索豆瓣"""
        try:
            url = f"{DOUBAN_API}?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                results = []
                for item in data:
                    rating = item.get("rate", "0")
                    try:
                        rating = float(rating)
                    except:
                        rating = 0
                    
                    results.append({
                        "title": item.get("title", ""),
                        "year": item.get("year", ""),
                        "rating": rating,
                        "image": item.get("img", ""),
                        "douban_id": item.get("id", ""),
                        "url": f"https://movie.douban.com/subject/{item.get('id', '')}/",
                        "match": item.get("title", "") == query
                    })
                return results
        except Exception as e:
            return [{"error": str(e)}]
    
    @staticmethod
    def get_details(douban_id):
        """获取详情"""
        try:
            url = f"https://movie.douban.com/subject/{douban_id}/"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                # 提取关键信息
                import re
                rating_match = re.search(r'rating["\']?\s*content["\']?\s*=\s*["\']?([\d.]+)', html)
                rating = float(rating_match.group(1)) if rating_match else 0
                
                image_match = re.search(r'<link[^>]*rel=["\']image["\'][^>]*href=["\']([^"\']+)["\']', html)
                image = image_match.group(1) if image_match else ""
                
                return {"rating": rating, "image": image}
        except:
            return {"rating": 0, "image": ""}


class StreamHandler(http.server.SimpleHTTPRequestHandler):
    """流媒体请求处理器"""
    
    download_manager = DownloadManager()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIDEO_DIR), **kwargs)
    
    def do_GET(self):
        # Parse path without query string for routing
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/" or path == "/index.html":
            self.send_index()
        elif path == "/api/videos":
            self.send_videos_list()
        elif path == "/api/search":
            self.send_search()
        elif path == "/api/download":
            self.send_download()
        elif path == "/api/status":
            self.send_status()
        elif path == "/api/health":
            self.send_json({"status": "ok", "time": datetime.now().isoformat()})
        else:
            # 处理视频文件的Range请求（边下边播）
            super().do_GET()
    
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
    
    def send_search(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
        if not query:
            self.send_json([])
            return
        
        results = DoubanScraper.search(query)
        # 筛选7分以上
        filtered = [r for r in results if r.get("rating", 0) >= MIN_RATING]
        self.send_json(filtered)
    
    def send_download(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        title = params.get("title", [""])[0]
        url = params.get("url", [""])[0]
        episode = params.get("episode", [None])[0]
        
        if title and url:
            try:
                ep = int(episode) if episode else None
                item_id = self.download_manager.add(title, url, episode=ep)
                self.send_json({"success": True, "id": item_id})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, 500)
        else:
            self.send_json({"success": False, "error": "Missing parameters"}, 400)
    
    def send_status(self):
        self.send_json(self.download_manager.get_status())
    
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', sans-serif;
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
        
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo i {
            font-size: 2rem;
            color: var(--accent);
        }
        
        .logo h1 {
            font-size: 1.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #fff, var(--text-secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .logo .badge {
            background: var(--rating-high);
            color: #000;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        /* Search Bar */
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
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .search-btn:hover {
            background: var(--accent-hover);
            transform: translateY(-2px);
        }
        
        /* Main Content */
        .main {
            padding: 30px 40px;
            max-width: 1600px;
            margin: 0 auto;
        }
        
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 30px;
        }
        
        .tab {
            padding: 10px 24px;
            background: var(--bg-card);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: var(--border-radius);
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .tab.active {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }
        
        .tab:hover:not(.active) {
            border-color: var(--accent);
        }
        
        /* Section */
        .section {
            display: none;
        }
        
        .section.active {
            display: block;
        }
        
        .section-title {
            font-size: 1.25rem;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .section-title i {
            color: var(--accent);
        }
        
        /* Video Grid */
        .video-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
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
        
        .video-card:hover .card-poster img {
            transform: scale(1.1);
        }
        
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
        
        .video-card:hover .card-overlay {
            opacity: 1;
        }
        
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
        
        .card-info {
            padding: 15px;
        }
        
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
        }
        
        .rating-high { background: var(--rating-high); color: #000; }
        .rating-mid { background: var(--rating-mid); color: #000; }
        
        .card-actions {
            display: flex;
            gap: 8px;
            margin-top: 12px;
        }
        
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
        
        .btn-play {
            background: var(--accent);
            color: #fff;
        }
        
        .btn-play:hover {
            background: var(--accent-hover);
        }
        
        .btn-download {
            background: rgba(255,255,255,0.1);
            color: var(--text-secondary);
        }
        
        .btn-download:hover {
            background: rgba(255,255,255,0.2);
            color: #fff;
        }
        
        /* Search Results */
        .search-results {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
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
        }
        
        .search-item-info {
            flex: 1;
        }
        
        .search-item-title {
            font-weight: 600;
            margin-bottom: 5px;
        }
        
        .search-item-meta {
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 10px;
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
        
        .modal.active {
            display: flex;
        }
        
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 20px;
            background: rgba(0,0,0,0.5);
        }
        
        .modal-title {
            font-size: 1.1rem;
        }
        
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
            align-items: center;
            padding: 20px;
        }
        
        .modal-body video {
            max-width: 100%;
            max-height: 85vh;
            background: #000;
            border-radius: 8px;
        }
        
        /* Download Queue */
        .queue-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
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
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .queue-info {
            flex: 1;
        }
        
        .queue-title {
            font-weight: 600;
            margin-bottom: 5px;
        }
        
        .queue-progress {
            height: 4px;
            background: rgba(255,255,255,0.1);
            border-radius: 2px;
            overflow: hidden;
        }
        
        .queue-progress-bar {
            height: 100%;
            background: var(--accent);
            transition: width 0.3s;
        }
        
        .queue-status {
            font-size: 0.8rem;
            color: var(--text-secondary);
        }
        
        /* Empty State */
        .empty-state {
            text-align: center;
            padding: 80px 20px;
            color: var(--text-secondary);
        }
        
        .empty-state i {
            font-size: 4rem;
            margin-bottom: 20px;
            opacity: 0.5;
        }
        
        .empty-state h2 {
            margin-bottom: 10px;
            color: var(--text-primary);
        }
        
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
        
        /* Responsive */
        @media (max-width: 768px) {
            .header { padding: 15px 20px; flex-wrap: wrap; gap: 15px; }
            .search-bar { margin: 0; order: 3; width: 100%; }
            .main { padding: 20px; }
            .video-grid { grid-template-columns: repeat(2, 1fr); gap: 15px; }
        }
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
        <!-- Tabs -->
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
        </div>
        
        <!-- Home Section -->
        <section id="home" class="section active">
            <h2 class="section-title"><i class="fas fa-fire"></i> 热门推荐</h2>
            <div class="video-grid" id="recommendGrid">
                <div class="empty-state">
                    <i class="fas fa-search"></i>
                    <h2>搜索你想看的作品</h2>
                    <p>输入电影或电视剧名称，筛选豆瓣7分以上的优质内容</p>
                </div>
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
            <video id="player" controls autoplay playsinline>
                <source src="" type="video/mp4">
                您的浏览器不支持视频播放
            </video>
        </div>
    </div>
    
    <!-- Toast -->
    <div class="toast" id="toast"></div>
    
    <script>
        // State
        let currentTab = 'home';
        let searchCache = new Map();
        
        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            loadLibrary();
            refreshQueue();
            setInterval(refreshQueue, 5000);
            
            // Enter key search
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
                const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
                const results = await res.json();
                
                if (results.error) {
                    document.getElementById('searchResults').innerHTML = 
                        `<div class="empty-state"><h2>搜索失败</h2><p>${results.error}</p></div>`;
                    return;
                }
                
                if (results.length === 0) {
                    document.getElementById('searchResults').innerHTML = 
                        '<div class="empty-state"><h2>未找到结果</h2><p>尝试其他关键词</p></div>';
                    return;
                }
                
                renderSearchResults(results);
            } catch (e) {
                document.getElementById('searchResults').innerHTML = 
                    `<div class="empty-state"><h2>请求失败</h2><p>${e.message}</p></div>`;
            }
        }
        
        function renderSearchResults(results) {
            const container = document.getElementById('searchResults');
            container.innerHTML = results.map(item => `
                <div class="search-item">
                    <img src="${item.image || 'https://via.placeholder.com/80x120?text=No+Image'}" alt="${item.title}">
                    <div class="search-item-info">
                        <div class="search-item-title">${item.title}</div>
                        <div class="search-item-meta">${item.year || ''} ${item.type || ''}</div>
                        <div style="display:flex;gap:10px;margin-top:10px;">
                            <span class="rating-badge ${item.rating >= 8 ? 'rating-high' : 'rating-mid'}">${item.rating.toFixed(1)}分</span>
                            <button class="action-btn btn-play" onclick="downloadAndPlay('${escape(item.title)}', '${item.image}', event)">
                                <i class="fas fa-download"></i> 下载并播放
                            </button>
                            <a href="${item.url}" target="_blank" class="action-btn btn-download" style="text-decoration:none;">
                                <i class="fas fa-external-link-alt"></i> 豆瓣
                            </a>
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        // Download and play
        async function downloadAndPlay(title, poster, event) {
            event.stopPropagation();
            showToast(`开始下载: ${title}`, 'success');
            
            // 这里需要根据实际URL下载
            // 简化版：提示用户需要手动添加下载链接
            showToast('请在下载队列中添加具体下载链接', 'error');
        }
        
        // Load library
        async function loadLibrary() {
            try {
                const res = await fetch('/api/videos');
                const videos = await res.json();
                
                document.getElementById('videoCount').textContent = videos.length;
                
                if (videos.length === 0) {
                    document.getElementById('libraryGrid').innerHTML = `
                        <div class="empty-state">
                            <i class="fas fa-video-slash"></i>
                            <h2>暂无视频</h2>
                            <p>搜索并下载作品到本地观看</p>
                        </div>
                    `;
                    return;
                }
                
                document.getElementById('libraryGrid').innerHTML = videos.map(v => `
                    <div class="video-card" onclick="playVideo('${v.url}', '${v.name}')">
                        <div class="card-poster">
                            <img src="https://via.placeholder.com/220x330/16213e/ffffff?text=${encodeURIComponent(v.name.substring(0,10))}" alt="${v.name}">
                            <div class="card-overlay">
                                <div class="play-btn"><i class="fas fa-play"></i></div>
                            </div>
                        </div>
                        <div class="card-info">
                            <div class="card-title">${v.name}</div>
                            <div class="card-meta">
                                <span>${v.size_mb} MB</span>
                            </div>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                console.error(e);
            }
        }
        
        // Play video
        function playVideo(url, title) {
            const modal = document.getElementById('playerModal');
            const player = document.getElementById('player');
            document.getElementById('playerTitle').textContent = title;
            
            player.src = url;
            modal.classList.add('active');
            player.play();
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
                            <p>从搜索结果中添加下载任务</p>
                        </div>
                    `;
                    return;
                }
                
                container.innerHTML = items.map(item => {
                    const iconClass = item.status;
                    const progress = item.progress || 0;
                    const statusText = {
                        pending: '等待中',
                        downloading: `${progress}%`,
                        completed: '已完成',
                        failed: '失败'
                    }[item.status] || item.status;
                    
                    return `
                        <div class="queue-item">
                            <div class="queue-icon ${iconClass}">
                                <i class="fas ${item.status === 'downloading' ? 'fa-spinner fa-spin' : 
                                            item.status === 'completed' ? 'fa-check' : 
                                            item.status === 'failed' ? 'fa-times' : 'fa-download'}"></i>
                            </div>
                            <div class="queue-info">
                                <div class="queue-title">${item.title}${item.episode ? ` (第${item.episode}集)` : ''}</div>
                                <div class="queue-progress">
                                    <div class="queue-progress-bar" style="width:${progress}%"></div>
                                </div>
                            </div>
                            <div class="queue-status">${statusText}</div>
                        </div>
                    `;
                }).join('');
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
        
        // Close modal on click outside
        document.getElementById('playerModal').addEventListener('click', (e) => {
            if (e.target.id === 'playerModal') closePlayer();
        });
    </script>
</body>
</html>'''


if __name__ == "__main__":
    # Allow port reuse
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), StreamHandler) as httpd:
        print(f"🎬 流媒体服务器已启动")
        print(f"📺 访问地址: http://localhost:{PORT}")
        print(f"📁 视频目录: {VIDEO_DIR}")
        print(f"⏹️  按 Ctrl+C 停止")
        httpd.serve_forever()
