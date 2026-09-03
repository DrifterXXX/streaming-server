# Streaming Server — 自托管视频流媒体服务器 | Self-hosted Video Streaming Media Server

自托管视频流媒体服务器，集成多云盘下载引擎。Netflix 风格 Web UI，随时随地观看你的媒体库。

A self-hosted video streaming media server with multi-cloud drive download engine. Watch your media collection from anywhere with a Netflix-style web UI.

---

## 功能 | Features

- **Netflix 风格 UI** — 海报墙 + 元数据展示 Beautiful poster view with metadata
- **HTTP Range 支持** — 可拖拽进度条的 seekable 播放 Seekable video playback
- **多云盘下载 Multi-Cloud Download** — 阿里云盘、夸克网盘、百度网盘、天翼云盘、迅雷云盘 Alibaba/Quark/Baidu/Tianyi/Xunlei
- **外部搜索 External Search** — 豆瓣 & TMDB 元数据刮取 Douban & TMDB metadata scraping
- **后台下载队列 Background Queue** — 并发下载 + 进度追踪 Concurrent downloads with progress
- **订阅管理 Subscription** — 剧集追踪与季管理 Episode tracking & season management
- **移动端友好 Mobile Friendly** — 响应式设计 + 横屏播放 Responsive design with landscape playback

## 架构 | Architecture

```
streaming-server/
├── stream_server_v3.py      # 主 HTTP 服务器（Range 请求）Main server
├── stream_server_v3_dl.py   # 下载增强模块 Download enhancement
├── cloud_download.py        # 多云盘下载引擎 Multi-cloud download engine
├── cloud_disk_dl.py         # 云盘下载实现 Cloud disk implementations
├── pan_api.py               # 云存储 API 适配器 Cloud storage API adapters
├── router.py                # URL 路由 Routing
├── database.py              # SQLite 媒体元数据库 Media metadata DB
├── search_service.py        # 豆瓣/TMDB 搜索 External search
├── space_manager.py         # 存储空间管理 Storage management
├── add_seasons.py           # 季/集管理 Season/episode management
└── templates/               # HTML 模板
```

## 快速启动 | Quick Start

```bash
git clone https://github.com/DrifterXXX/streaming-server.git
cd streaming-server
mkdir -p videos
python3 stream_server_v3.py
```

### 云盘配置 | Cloud Drive Setup

```bash
python3 pan_login.py          # 认证云盘账号 Authenticate
# 凭据存储于 data/pan_creds.json（已 gitignore）Credentials stored locally
python3 cloud_download.py     # 下载媒体 Download media
```

### 视频管理 | Video Management

- 将视频文件放入 `videos/` 目录 Place files in `videos/`
- 支持格式 Formats: MP4, MKV, AVI, MOV, WEBM
- 命名规范 Naming: `ShowName.ep01.mp4`

## 支持的云盘 | Supported Cloud Drives

| 云盘 Provider | 状态 Status | 特性 Features |
|---|---|---|
| 阿里云盘 Alibaba Cloud Drive | ✅ | JWT 认证, 文件列表, 下载 |
| 夸克网盘 Quark Pan | ✅ | Cookie 认证, 福利探索 |
| 百度网盘 Baidu Netdisk | ✅ | BDUSS 认证, 文件列表 |
| 天翼云盘 Tianyi Cloud | ✅ | Cookie 认证, 下载 |
| 迅雷云盘 Xunlei Cloud | ✅ | Session 认证, 文件列表 |

## 安全 | Security

所有云盘凭据仅存储在本地，绝不推送到 git。`data/`、`pan_creds.json`、`.env` 已通过 `.gitignore` 排除。

All cloud drive credentials are stored locally and never pushed to git.

## 技术栈 | Tech Stack

Python 3.9+ / SQLite / HTTP Range / 多线程下载 Multi-threaded downloads

## 许可证 | License

MIT
