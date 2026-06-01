#!/usr/bin/env python3
"""
轻量级流媒体服务器 - 支持视频播放和Range请求
"""
import http.server
import socketserver
import os
import json
from pathlib import Path
from urllib.parse import unquote

VIDEO_DIR = Path.home() / "streaming-server" / "videos"
PORT = 9878

class VideoHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIDEO_DIR), **kwargs)
    
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_index()
        elif self.path == "/api/videos":
            self.send_videos_list()
        else:
            # 处理视频文件的Range请求
            super().do_GET()
    
    def send_index(self):
        html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>流媒体服务器</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh; color: #fff; padding: 20px;
        }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; }
        .header p { color: #8892b0; }
        .video-grid { 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); 
            gap: 20px; 
            max-width: 1400px; 
            margin: 0 auto; 
        }
        .video-card {
            background: rgba(255,255,255,0.1);
            border-radius: 12px;
            overflow: hidden;
            transition: transform 0.3s, box-shadow 0.3s;
            cursor: pointer;
        }
        .video-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        .video-card video {
            width: 100%;
            height: 160px;
            object-fit: cover;
            background: #000;
        }
        .video-info { padding: 15px; }
        .video-info h3 { font-size: 1rem; margin-bottom: 5px; }
        .video-info p { color: #8892b0; font-size: 0.85rem; }
        .player-modal {
            display: none;
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: rgba(0,0,0,0.95);
            z-index: 1000;
            flex-direction: column;
        }
        .player-modal.active { display: flex; }
        .player-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 20px;
            background: rgba(0,0,0,0.5);
        }
        .player-header h2 { font-size: 1.2rem; }
        .close-btn {
            background: none;
            border: none;
            color: #fff;
            font-size: 2rem;
            cursor: pointer;
            padding: 5px 10px;
        }
        .player-container {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .player-container video {
            max-width: 100%;
            max-height: 80vh;
            background: #000;
            border-radius: 8px;
        }
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #8892b0;
        }
        .empty-state h2 { margin-bottom: 15px; }
        .upload-hint {
            background: rgba(255,255,255,0.05);
            border: 2px dashed rgba(255,255,255,0.2);
            border-radius: 12px;
            padding: 30px;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎬 流媒体服务器</h1>
        <p>tv.drifter.indevs.in</p>
    </div>
    <div class="video-grid" id="videoGrid">
        <!-- 视频列表将在这里加载 -->
    </div>
    <div class="player-modal" id="playerModal">
        <div class="player-header">
            <h2 id="playerTitle">视频播放</h2>
            <button class="close-btn" onclick="closePlayer()">&times;</button>
        </div>
        <div class="player-container">
            <video id="videoPlayer" controls playsinline>
                <source src="" type="video/mp4">
                您的浏览器不支持视频播放。
            </video>
        </div>
    </div>
    <script>
        let videos = [];
        
        async function loadVideos() {
            try {
                const res = await fetch('/api/videos');
                videos = await res.json();
                renderVideos();
            } catch (e) {
                document.getElementById('videoGrid').innerHTML = 
                    '<div class="empty-state"><h2>暂无视频</h2><p>请将视频文件放入 videos 目录</p></div>';
            }
        }
        
        function renderVideos() {
            const grid = document.getElementById('videoGrid');
            if (videos.length === 0) {
                grid.innerHTML = `
                    <div class="empty-state">
                        <h2>📁 暂无视频</h2>
                        <p>请将视频文件放入 <code>~/streaming-server/videos</code> 目录</p>
                        <div class="upload-hint">
                            <p>支持格式: MP4, MKV, AVI, MOV, WEBM</p>
                            <p style="margin-top:10px;color:#8892b0">上传后刷新页面即可看到</p>
                        </div>
                    </div>`;
                return;
            }
            grid.innerHTML = videos.map(v => `
                <div class="video-card" onclick="playVideo('${v.name}')">
                    <video src="${encodeURIComponent(v.name)}#t=1" preload="metadata"></video>
                    <div class="video-info">
                        <h3>${v.displayName}</h3>
                        <p>${v.size}</p>
                    </div>
                </div>
            `).join('');
        }
        
        function playVideo(filename) {
            const modal = document.getElementById('playerModal');
            const video = document.getElementById('videoPlayer');
            document.getElementById('playerTitle').textContent = decodeURIComponent(filename);
            video.src = encodeURIComponent(filename);
            modal.classList.add('active');
            video.play();
        }
        
        function closePlayer() {
            const modal = document.getElementById('playerModal');
            const video = document.getElementById('videoPlayer');
            video.pause();
            video.src = '';
            modal.classList.remove('active');
        }
        
        // 点击背景关闭
        document.getElementById('playerModal').addEventListener('click', (e) => {
            if (e.target.id === 'playerModal') closePlayer();
        });
        
        loadVideos();
    </script>
</body>
</html>'''
        
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def send_videos_list(self):
        videos = []
        for f in VIDEO_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v']:
                size_mb = f.stat().st_size / (1024 * 1024)
                videos.append({
                    'name': f.name,
                    'displayName': f.stem.replace('_', ' ').replace('.', ' ').title(),
                    'size': f'{size_mb:.1f} MB'
                })
        videos.sort(key=lambda x: x['name'])
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(videos).encode())
    
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), VideoHandler) as httpd:
        print(f"🎬 流媒体服务器启动")
        print(f"📁 视频目录: {VIDEO_DIR}")
        print(f"🌐 本地访问: http://localhost:{PORT}")
        print(f"📡 远程访问: https://tv.drifter.indevs.in")
        print("\n按 Ctrl+C 停止服务器")
        httpd.serve_forever()
