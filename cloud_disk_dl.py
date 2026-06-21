"""
cloud_disk_dl.py — 网盘自动下载引擎 v2
======================================
统一接口：检测URL -> 保存到网盘 -> 下载到本地

支持的网盘：
  - 阿里云盘 (Aliyundrive) -> pan_api.AliyunAPI + pan_login
  - 百度网盘 (Baidu)       -> pan_api.BaiduAPI + pan_login
  - 夸克网盘 (Quark)       -> pan_api.QuarkAPI + pan_login

核心改进：
  - 使用 pan_login.py 统一持久化登录凭证
  - 使用 pan_api.py 直接调用各网盘 API（不再依赖 CLI 猜测文件）
  - 下载走 API 返回的临时直链，用 urllib 直接下载
"""

import os
import re
import sys
import json
import time
import random
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOGIN_MGR = None
def _get_login_mgr():
    global _LOGIN_MGR
    if _LOGIN_MGR is None:
        from pan_login import PanLoginManager
        _LOGIN_MGR = PanLoginManager()
    return _LOGIN_MGR


# 网盘 API 直连层
from pan_api import AliyunAPI, BaiduAPI, QuarkAPI, TianyiAPI, XunleiAPI

# 阿里云盘浏览器自动化引擎（仅作为 API 方式的兜底）
try:
    from aliyun_browser_save import AliyunBrowserEngine
    _ALIYUN_BROWSER = AliyunBrowserEngine()
except ImportError:
    _ALIYUN_BROWSER = None

# 空间管理器
try:
    from space_manager import (
        check_and_manage_space, estimate_download_size,
        format_size, LocalSpaceManager
    )
except ImportError:
    check_and_manage_space = None
    estimate_download_size = None
    format_size = None
    LocalSpaceManager = None

# ─── 路径配置 ──────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
VIDEO_DIR = BASE_DIR / "videos"

def _ensure_video_dir():
    """延迟初始化视频目录，避免 import-time 副作用"""
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# ─── 网盘检测 ──────────────────────────────────────────────

PAN_PATTERNS = [
    (r'(?:www\.)?(aliyundrive\.com|alipan\.com)/s/([a-zA-Z0-9_-]+)', 'aliyun', '阿里云盘'),
    (r'(pan\.baidu\.com)/s/([a-zA-Z0-9_-]+)', 'baidu', '百度网盘'),
    (r'(?:pan\.)?quark\.cn/s/([a-zA-Z0-9_-]+)', 'quark', '夸克网盘'),
    (r'115\.com/s/([a-zA-Z0-9_-]+)', '115', '115网盘'),
    (r'cloud\.189\.cn/s/([a-zA-Z0-9_-]+)', 'tianyi', '天翼云盘'),
    (r'pan\.xunlei\.com/s/([a-zA-Z0-9_-]+)', 'xunlei', '迅雷云盘'),
    (r'123pan\.com/s/([a-zA-Z0-9_-]+)', '123pan', '123云盘'),
    (r'lanzou[a-z]\.com/s/([a-zA-Z0-9_-]+)', 'lanzou', '蓝奏云'),
]


def detect_pan(url: str):
    """检测URL属于哪个网盘，返回 {key, name, share_id} 或 None"""
    for pattern, key, name in PAN_PATTERNS:
        m = re.search(pattern, url)
        if m:
            groups = m.groups()
            share_id = groups[-1]
            return {'key': key, 'name': name, 'share_id': share_id}
    return None


def extract_password(url: str):
    """从URL或上下文提取提取码"""
    for pat in [r'[?&]pwd=([a-zA-Z0-9]+)', r'提取码[：:]?\s*([a-zA-Z0-9]+)',
                r'密码[：:]?\s*([a-zA-Z0-9]+)']:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


# ─── 通用下载辅助 ──────────────────────────────────────────

def _download_direct(url: str, output_path: Path, progress_callback=None):
    """通过临时直链下载文件，支持进度回调。先写入 .tmp 再重命名，防止部分文件残留"""
    start_time = time.time()
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    def report_hook(block_num, block_size, total_size):
        if progress_callback and total_size > 0:
            downloaded = block_num * block_size
            pct = min(100, int(downloaded * 100 / total_size))
            elapsed = time.time() - start_time
            speed = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
            progress_callback(pct, f"{speed:.1f} MB/s")

    urllib.request.urlretrieve(url, str(tmp_path), report_hook)
    shutil.move(str(tmp_path), str(output_path))


def _find_video_in_files(files, title: str = ""):
    """从 API 返回的文件列表中找出视频文件"""
    video_ext = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.ts'}
    videos = []
    for f in files:
        name = f.get("name") or f.get("file_name") or ""
        ext = Path(name).suffix.lower()
        if ext in video_ext:
            videos.append((f, name))
    if not videos:
        return files[0] if files else None
    # 优先返回最大的视频文件
    def size(f):
        return f.get("size") or f.get("file_size") or 0
    return max(videos, key=lambda vf: size(vf[0]))[0]


# ═══════════════════════════════════════════════════════════
#  阿里云盘引擎 (API 直连)
# ═══════════════════════════════════════════════════════════

class AliyunEngine:
    """阿里云盘下载引擎 — 基于 pan_api.AliyunAPI + pan_login"""

    def __init__(self):
        self.login_mgr = _get_login_mgr()
        self.api = AliyunAPI(self.login_mgr)
        # 保留 CLI 路径用于兜底
        self.bin = shutil.which('aliyunpan')

    def is_logged_in(self):
        return self.login_mgr.aliyun_check()

    def login_prompt(self):
        return self.login_mgr.login_prompt("aliyun")

    def download(self, url, title, password=None, progress_callback=None):
        """阿里云盘下载 — API 直连：解析分享 → 转存 → 获取直链 → 下载"""
        is_share = bool(re.search(r'(?:aliyundrive\.com|alipan\.com)/s/', url))

        if not is_share:
            # 非分享链接，直接通过 CLI 下载
            return self._cli_download(url, title, progress_callback)

        # ── 分享链接流程 ──
        pan_info = detect_pan(url)
        if not pan_info:
            return {'success': False, 'error': '无法识别的阿里云盘链接'}

        share_id = pan_info['share_id']
        pwd = password or extract_password(url)

        if progress_callback:
            progress_callback(5, '正在解析分享链接...')

        # 1. 获取分享 token
        share_tokens = self.api.get_share_tokens(share_id, pwd or "")
        if not share_tokens:
            return {'success': False, 'error': '无法解析分享链接，可能链接无效或需要密码'}

        share_token = share_tokens.get("share_token", "")
        if not share_token:
            return {'success': False, 'error': '无法获取分享 token'}

        if progress_callback:
            progress_callback(15, '正在获取文件列表...')

        # 2. 获取文件列表，找到视频文件
        files = self.api.get_share_files(share_id, share_token)
        if not files:
            return {'success': False, 'error': '分享链接中未找到文件'}

        video_file = _find_video_in_files(files, title)
        if not video_file:
            return {'success': False, 'error': '分享链接中未找到视频文件'}

        video_file_id = video_file.get("file_id") or video_file.get("fid") or ""
        video_name = video_file.get("name") or video_file.get("file_name") or title or "download"
        if not video_file_id:
            return {'success': False, 'error': '无法获取文件 ID'}

        if progress_callback:
            progress_callback(25, f'找到文件 "{video_name}"，正在转存到网盘...')

        # 3. 转存到用户网盘
        copied = self.api.copy_file(share_id, share_token, video_file_id)
        if not copied:
            # API 转存失败，尝试浏览器自动化兜底
            if _ALIYUN_BROWSER:
                if progress_callback:
                    progress_callback(30, 'API 转存失败，尝试浏览器自动化...')
                save_result = _ALIYUN_BROWSER.save_share(url, pwd)
                if save_result.get('success'):
                    if progress_callback:
                        progress_callback(50, '浏览器转存成功，正在下载...')
                    return self._cli_download_after_save(title, progress_callback)
                else:
                    return {'success': False, 'error': f'转存失败: {save_result.get("error", "未知错误")}'}
            return {'success': False, 'error': '转存失败，请检查登录状态'}

        if progress_callback:
            progress_callback(50, '转存成功，正在获取下载链接...')

        # 4. 在用户网盘中轮询刚转存的文件，避免固定 sleep 导致取直链失败
        target_file = None
        for attempt in range(20):
            searched = self.api.search_file(video_name)
            for f in searched:
                fname = (f.get("name") or "").strip()
                if video_name and video_name in fname:
                    target_file = f
                    break
            if target_file:
                break
            listed = self.api.list_files("root")
            for f in listed:
                fname = (f.get("name") or "").strip()
                if video_name and video_name in fname:
                    target_file = f
                    break
            if target_file:
                break
            time.sleep(0.5)
        if not target_file:
            return {'success': False, 'error': '转存成功但无法在网盘中找到文件'}

        target_file_id = target_file.get("file_id") or ""

        if progress_callback:
            progress_callback(60, '正在获取下载直链...')

        # 5. 获取下载直链
        download_url = self.api.get_download_url(target_file_id)
        if not download_url:
            # 尝试视频预览地址
            download_url = self.api.get_video_preview(target_file_id)
        if not download_url:
            return {'success': False, 'error': '无法获取下载链接'}

        if progress_callback:
            progress_callback(65, '开始下载文件...')

        # 6. 下载文件
        output_name = re.sub(r'[^\w一-鿿\s-]', '', video_name)
        output_path = VIDEO_DIR / f"{output_name}_{int(time.time())}_{os.getpid()}_{random.randint(1000,9999)}.mp4"

        try:
            _download_direct(download_url, output_path, progress_callback)

            if output_path.exists() and output_path.stat().st_size > 1024 * 1024:
                size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
                if progress_callback:
                    progress_callback(100, f'下载完成 ({size_mb}MB)')
                return {
                    'success': True,
                    'filename': output_path.name,
                    'size': output_path.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(output_path),
                }
            else:
                return {'success': False, 'error': '下载完成但文件过小'}
        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            return {'success': False, 'error': '下载失败，请稍后重试'}

    def _cli_download_after_save(self, title, progress_callback):
        """浏览器转存成功后，通过 CLI 下载"""
        if not self.bin:
            return {'success': False, 'error': 'aliyunpan CLI 未安装'}
        try:
            output_name_cli = re.sub(r'[^\w一-鿿\s-]', '', title or 'download')
            cmd = [self.bin, 'download', '--saveto', str(VIDEO_DIR), f'/{title}']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                return {'success': False, 'error': f'CLI下载失败: {result.stderr[:200]}'}
            marker = VIDEO_DIR / f".dl_marker_{int(time.time()*1000)}_{os.getpid()}"
            marker.touch()
            try:
                files = list(VIDEO_DIR.glob("*"))
                new_files = [f for f in files
                             if f.stat().st_mtime > marker.stat().st_mtime
                             and f.suffix.lower() in ('.mp4', '.mkv', '.webm', '.avi')
                             and f.stat().st_size > 1024 * 1024]
            finally:
                marker.unlink(missing_ok=True)
            if new_files:
                newest = max(new_files, key=lambda f: f.stat().st_size)
                size_mb = round(newest.stat().st_size / 1024 / 1024, 1)
                if progress_callback:
                    progress_callback(100, f'下载完成 ({size_mb}MB)')
                return {
                    'success': True,
                    'filename': newest.name,
                    'size': newest.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(newest),
                }
            return {'success': False, 'error': '转存成功但下载时未找到视频文件'}
        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            return {'success': False, 'error': '下载失败，请稍后重试'}

    def _cli_download(self, url, title, progress_callback):
        """非分享链接的 CLI 下载兜底"""
        if not self.bin:
            return {'success': False, 'error': 'aliyunpan CLI 未安装'}
        try:
            file_path = url if url.startswith('/') else url
            output_name_cli = re.sub(r'[^\w一-鿿\s-]', '', title or 'download')
            cmd = [self.bin, 'download', '--saveto', str(VIDEO_DIR), file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                return {'success': False, 'error': f'CLI下载失败: {result.stderr[:200]}'}
            marker = VIDEO_DIR / f".dl_marker_{int(time.time()*1000)}_{os.getpid()}"
            marker.touch()
            try:
                files = list(VIDEO_DIR.glob("*"))
                new_files = [f for f in files
                             if f.stat().st_mtime > marker.stat().st_mtime
                             and f.suffix.lower() in ('.mp4', '.mkv', '.webm', '.avi')
                             and f.stat().st_size > 1024 * 1024]
            finally:
                marker.unlink(missing_ok=True)
            if new_files:
                newest = max(new_files, key=lambda f: f.stat().st_size)
                size_mb = round(newest.stat().st_size / 1024 / 1024, 1)
                return {
                    'success': True,
                    'filename': newest.name,
                    'size': newest.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(newest),
                }
            return {'success': False, 'error': '下载完成但未找到视频文件'}
        except Exception as e:
            logger.error(f"阿里云盘下载失败: {e}", exc_info=True)
            return {'success': False, 'error': '下载失败，请稍后重试'}


# ═══════════════════════════════════════════════════════════
#  百度网盘引擎 (API 直连)
# ═══════════════════════════════════════════════════════════

class BaiduEngine:
    """百度网盘下载引擎 — 基于 pan_api.BaiduAPI + pan_login"""

    def __init__(self):
        self.login_mgr = _get_login_mgr()
        self.api = BaiduAPI(self.login_mgr)
        self.bin = shutil.which('BaiduPCS-Go')

    def is_logged_in(self) -> bool:
        return self.login_mgr.baidu_check()

    def login_prompt(self) -> str:
        return self.login_mgr.login_prompt("baidu")

    def download(self, url: str, title: str, password: str = None,
                 progress_callback=None) -> dict:
        """百度网盘分享链接 -> 转存 -> 下载"""
        pan_info = detect_pan(url)
        if not pan_info:
            return {'success': False, 'error': '无法识别的百度网盘链接'}

        share_id = pan_info['share_id']
        pwd = password or extract_password(url)

        if progress_callback:
            progress_callback(5, '正在解析分享链接...')

        # 1. 获取分享文件列表
        share_info = self.api.get_share_info(share_id, pwd)
        if not share_info:
            return {'success': False, 'error': '无法解析分享链接，可能链接无效或需要密码'}

        files = share_info.get("list", [])
        if not files:
            return {'success': False, 'error': '分享链接中未找到文件'}

        video_file = _find_video_in_files(files, title)
        if not video_file:
            return {'success': False, 'error': '分享链接中未找到视频文件'}

        video_name = video_file.get("server_filename") or video_file.get("path") or title or "download"
        if progress_callback:
            progress_callback(15, f'找到文件 "{video_name}"，正在转存...')

        # 2. 转存到用户网盘
        saved_ids = self.api.save_share(share_id, pwd, [video_file], "/")
        if not saved_ids:
            # API 转存失败，回退到 BaiduPCS-Go CLI
            if self.bin:
                if progress_callback:
                    progress_callback(20, 'API 转存失败，尝试 BaiduPCS-Go...')
                return self._cli_download(url, title, pwd, progress_callback)
            return {'success': False, 'error': '转存失败'}

        if progress_callback:
            progress_callback(40, '转存成功，正在获取下载链接...')

        # 3. 获取下载直链
        time.sleep(1)
        listed = self.api.list_files("/")
        target = None
        for f in listed:
            fname = f.get("path", "").rsplit("/", 1)[-1]
            if video_name in fname:
                target = f
                break

        if not target:
            return {'success': False, 'error': '转存成功但无法在网盘中找到文件'}

        file_path = target.get("path", "")
        download_url = self.api.get_file_download(file_path)
        if not download_url:
            return {'success': False, 'error': '无法获取下载链接'}

        if progress_callback:
            progress_callback(50, '开始下载文件...')

        # 4. 下载文件
        output_name = re.sub(r'[^\w一-鿿\s-]', '', video_name)
        output_path = VIDEO_DIR / f"{output_name}_{int(time.time())}_{os.getpid()}_{random.randint(1000,9999)}.mp4"

        try:
            _download_direct(download_url, output_path, progress_callback)

            if output_path.exists() and output_path.stat().st_size > 1024 * 1024:
                size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
                if progress_callback:
                    progress_callback(100, f'下载完成 ({size_mb}MB)')
                return {
                    'success': True,
                    'filename': output_path.name,
                    'size': output_path.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(output_path),
                }
            else:
                return {'success': False, 'error': '下载完成但文件过小'}
        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            return {'success': False, 'error': '下载失败，请稍后重试'}

    def _cli_download(self, url, title, pwd, progress_callback):
        """通过 BaiduPCS-Go CLI 兜底下载"""
        if not self.bin:
            return {'success': False, 'error': 'BaiduPCS-Go 未安装'}
        try:
            transfer_cmd = [self.bin, 'transfer', url]
            transfer_result = subprocess.run(transfer_cmd, capture_output=True, text=True, timeout=30)
            if transfer_result.returncode != 0:
                return {'success': False, 'error': f'转存失败: {transfer_result.stderr[:200]}'}
            output_name_cli = re.sub(r'[^\w一-鿿\s-]', '', title or 'download')
            dl_cmd = [self.bin, 'download', f'/{output_name_cli}', '--saveto', str(VIDEO_DIR)]
            dl_result = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=600)
            if dl_result.returncode != 0:
                return {'success': False, 'error': f'CLI下载失败: {dl_result.stderr[:200]}'}
            marker = VIDEO_DIR / f".dl_marker_{int(time.time()*1000)}_{os.getpid()}"
            marker.touch()
            try:
                files = list(VIDEO_DIR.glob("*"))
                new_files = [f for f in files
                             if f.stat().st_mtime > marker.stat().st_mtime
                             and f.suffix.lower() in ('.mp4', '.mkv', '.webm')
                             and f.stat().st_size > 1024 * 1024]
            finally:
                marker.unlink(missing_ok=True)
            if new_files:
                newest = max(new_files, key=lambda f: f.stat().st_size)
                size_mb = round(newest.stat().st_size / 1024 / 1024, 1)
                return {
                    'success': True,
                    'filename': newest.name,
                    'size': newest.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(newest),
                }
            return {'success': False, 'error': 'CLI 下载完成但未找到视频文件'}
        except Exception as e:
            logger.error(f"百度网盘 CLI 下载失败: {e}", exc_info=True)
            return {'success': False, 'error': 'CLI下载失败，请稍后重试'}


# ═══════════════════════════════════════════════════════════
#  夸克网盘引擎 (API 直连)
# ═══════════════════════════════════════════════════════════

class QuarkEngine:
    """夸克网盘下载引擎 — 基于 pan_api.QuarkAPI + pan_login"""

    def __init__(self):
        self.login_mgr = _get_login_mgr()
        self.api = QuarkAPI(self.login_mgr)
        self.bin = shutil.which('quarkpan')

    def is_logged_in(self) -> bool:
        return self.login_mgr.quark_check()

    def login_prompt(self) -> str:
        return self.login_mgr.login_prompt("quark")

    def download(self, url: str, title: str, password: str = None,
                 progress_callback=None) -> dict:
        """夸克网盘分享链接 -> 保存+下载到本地"""
        pan_info = detect_pan(url)
        if not pan_info:
            return {'success': False, 'error': '无法识别的夸克网盘链接'}

        share_id = pan_info['share_id']

        if progress_callback:
            progress_callback(5, '正在解析分享链接...')

        # 1. 获取分享信息
        share_info = self.api.get_share_info(share_id)
        if not share_info:
            return {'success': False, 'error': '无法解析分享链接'}

        share_token = share_info.get("share_token", "")
        if not share_token:
            return {'success': False, 'error': '无法获取分享 token'}

        if progress_callback:
            progress_callback(15, '正在获取文件列表...')

        # 2. 获取文件列表
        files = self.api.get_share_files(share_id, share_token)
        if not files:
            return {'success': False, 'error': '分享链接中未找到文件'}

        video_file = _find_video_in_files(files, title)
        if not video_file:
            return {'success': False, 'error': '分享链接中未找到视频文件'}

        video_fid = str(video_file.get("fid", video_file.get("file_id", "")))
        video_name = video_file.get("file_name") or video_file.get("name") or title or "download"

        if progress_callback:
            progress_callback(25, f'找到文件 "{video_name}"，正在保存到网盘...')

        # 3. 保存到用户网盘
        saved = self.api.save_share(share_id, share_token, video_fid, "/")
        if not saved:
            return {'success': False, 'error': '保存到网盘失败，请检查登录状态'}

        if progress_callback:
            progress_callback(50, '保存成功，正在获取下载链接...')

        # 4. 在用户网盘中搜索刚保存的文件
        time.sleep(1)
        searched = self.api.search_file(video_name)
        target_fid = None
        for f in searched:
            fname = f.get("file_name") or f.get("name") or ""
            if video_name in fname:
                target_fid = str(f.get("fid", f.get("file_id", "")))
                break

        if not target_fid:
            listed = self.api.list_files("0")
            for f in listed:
                fname = f.get("file_name") or f.get("name") or ""
                if video_name in fname:
                    target_fid = str(f.get("fid", f.get("file_id", "")))
                    break

        if not target_fid:
            return {'success': False, 'error': '保存成功但无法在网盘中找到文件'}

        # 5. 获取下载直链
        download_url = self.api.get_download_url(target_fid)
        if not download_url:
            return {'success': False, 'error': '无法获取下载链接'}

        if progress_callback:
            progress_callback(60, '开始下载文件...')

        # 6. 下载文件
        output_name = re.sub(r'[^\w一-鿿\s-]', '', video_name)
        output_path = VIDEO_DIR / f"{output_name}_{int(time.time())}_{os.getpid()}_{random.randint(1000,9999)}.mp4"

        try:
            _download_direct(download_url, output_path, progress_callback)

            if output_path.exists() and output_path.stat().st_size > 1024 * 1024:
                size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
                if progress_callback:
                    progress_callback(100, f'下载完成 ({size_mb}MB)')
                return {
                    'success': True,
                    'filename': output_path.name,
                    'size': output_path.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(output_path),
                }
            else:
                return {'success': False, 'error': '下载完成但文件过小'}
        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            return {'success': False, 'error': '下载失败，请稍后重试'}


# ═══════════════════════════════════════════════════════════
#  天翼云盘引擎 (API 直连)
# ═══════════════════════════════════════════════════════════

class TianyiEngine:
    """天翼云盘下载引擎 — 基于 pan_api.TianyiAPI + pan_login"""

    def __init__(self):
        self.login_mgr = _get_login_mgr()
        self.api = TianyiAPI(self.login_mgr)

    def is_logged_in(self) -> bool:
        return self.login_mgr.tianyi_check()

    def login_prompt(self) -> str:
        return self.login_mgr.login_prompt("tianyi")

    def download(self, url: str, title: str, password: str = None,
                 progress_callback=None) -> dict:
        """天翼云盘分享链接 -> 转存 -> 下载"""
        pan_info = detect_pan(url)
        if not pan_info:
            return {'success': False, 'error': '无法识别的天翼云盘链接'}

        share_id = pan_info['share_id']
        pwd = password or extract_password(url)

        if progress_callback:
            progress_callback(5, '正在解析分享链接...')

        # 1. 获取分享文件列表
        share_info = self.api.get_share_info(share_id, pwd)
        if not share_info:
            return {'success': False, 'error': '无法解析分享链接，可能链接无效或需要密码'}

        files = share_info.get("data", {}).get("fileInfos", {}).get("list", [])
        if not files:
            return {'success': False, 'error': '分享链接中未找到文件'}

        video_file = _find_video_in_files(files, title)
        if not video_file:
            return {'success': False, 'error': '分享链接中未找到视频文件'}

        video_file_id = video_file.get("fileId") or ""
        video_name = video_file.get("fileName") or title or "download"
        if not video_file_id:
            return {'success': False, 'error': '无法获取文件 ID'}

        if progress_callback:
            progress_callback(15, f'找到文件 "{video_name}"，正在转存...')

        # 2. 转存到用户网盘
        saved_ids = self.api.save_share(share_id, [video_file_id], "root", pwd)
        if not saved_ids:
            return {'success': False, 'error': '转存失败，请检查登录状态'}

        if progress_callback:
            progress_callback(40, '转存成功，正在获取下载链接...')

        # 3. 获取下载直链
        time.sleep(1)
        target_file_id = saved_ids[0]

        download_url = self.api.get_download_url(target_file_id)
        if not download_url:
            # 尝试在网盘中搜索文件再获取下载链接
            searched = self.api.search_file(video_name)
            for f in searched:
                fname = f.get("fileName") or ""
                if video_name in fname:
                    target_file_id = f.get("fileId") or ""
                    break
            if target_file_id:
                download_url = self.api.get_download_url(target_file_id)

        if not download_url:
            return {'success': False, 'error': '无法获取下载链接'}

        if progress_callback:
            progress_callback(60, '开始下载文件...')

        # 4. 下载文件
        output_name = re.sub(r'[^\w一-鿿\s-]', '', video_name)
        output_path = VIDEO_DIR / f"{output_name}_{int(time.time())}_{os.getpid()}_{random.randint(1000,9999)}.mp4"

        try:
            _download_direct(download_url, output_path, progress_callback)

            if output_path.exists() and output_path.stat().st_size > 1024 * 1024:
                size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
                if progress_callback:
                    progress_callback(100, f'下载完成 ({size_mb}MB)')
                return {
                    'success': True,
                    'filename': output_path.name,
                    'size': output_path.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(output_path),
                }
            else:
                return {'success': False, 'error': '下载完成但文件过小'}
        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            return {'success': False, 'error': '下载失败，请稍后重试'}


# ═══════════════════════════════════════════════════════════
#  迅雷云盘引擎 (API 直连)
# ═══════════════════════════════════════════════════════════

class XunleiEngine:
    """迅雷云盘下载引擎 — 基于 pan_api.XunleiAPI + pan_login"""

    def __init__(self):
        self.login_mgr = _get_login_mgr()
        self.api = XunleiAPI(self.login_mgr)

    def is_logged_in(self) -> bool:
        return self.login_mgr.xunlei_check()

    def login_prompt(self) -> str:
        return self.login_mgr.login_prompt("xunlei")

    def download(self, url: str, title: str, password: str = None,
                 progress_callback=None) -> dict:
        """迅雷云盘分享链接 -> 转存 -> 下载"""
        pan_info = detect_pan(url)
        if not pan_info:
            return {'success': False, 'error': '无法识别的迅雷云盘链接'}

        share_id = pan_info['share_id']
        pwd = password or extract_password(url)

        if progress_callback:
            progress_callback(5, '正在解析分享链接...')

        # 1. 获取分享文件列表
        share_info = self.api.get_share_info(share_id, pwd)
        if not share_info:
            return {'success': False, 'error': '无法解析分享链接，可能链接无效或需要密码'}

        files = share_info.get("data", {}).get("files", share_info.get("data", {}).get("list", []))
        if not files:
            return {'success': False, 'error': '分享链接中未找到文件'}

        video_file = _find_video_in_files(files, title)
        if not video_file:
            return {'success': False, 'error': '分享链接中未找到视频文件'}

        video_file_id = str(video_file.get("fileId") or video_file.get("id") or "")
        video_name = video_file.get("fileName") or video_file.get("name") or title or "download"
        if not video_file_id:
            return {'success': False, 'error': '无法获取文件 ID'}

        if progress_callback:
            progress_callback(15, f'找到文件 "{video_name}"，正在转存...')

        # 2. 转存到用户网盘
        saved_ids = self.api.save_share(share_id, [video_file_id], "0", pwd)
        if not saved_ids:
            return {'success': False, 'error': '转存失败，请检查登录状态'}

        if progress_callback:
            progress_callback(40, '转存成功，正在获取下载链接...')

        # 3. 获取下载直链
        time.sleep(1)
        target_file_id = saved_ids[0]

        download_url = self.api.get_download_url(target_file_id)
        if not download_url:
            # 尝试在网盘中搜索文件再获取下载链接
            searched = self.api.search_file(video_name)
            for f in searched:
                fname = f.get("fileName") or f.get("name") or ""
                if video_name in fname:
                    target_file_id = str(f.get("fileId") or f.get("id") or "")
                    break
            if target_file_id:
                download_url = self.api.get_download_url(target_file_id)

        if not download_url:
            return {'success': False, 'error': '无法获取下载链接'}

        if progress_callback:
            progress_callback(60, '开始下载文件...')

        # 4. 下载文件
        output_name = re.sub(r'[^\w一-鿿\s-]', '', video_name)
        output_path = VIDEO_DIR / f"{output_name}_{int(time.time())}_{os.getpid()}_{random.randint(1000,9999)}.mp4"

        try:
            _download_direct(download_url, output_path, progress_callback)

            if output_path.exists() and output_path.stat().st_size > 1024 * 1024:
                size_mb = round(output_path.stat().st_size / 1024 / 1024, 1)
                if progress_callback:
                    progress_callback(100, f'下载完成 ({size_mb}MB)')
                return {
                    'success': True,
                    'filename': output_path.name,
                    'size': output_path.stat().st_size,
                    'size_mb': size_mb,
                    'filepath': str(output_path),
                }
            else:
                return {'success': False, 'error': '下载完成但文件过小'}
        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            return {'success': False, 'error': '下载失败，请稍后重试'}


# ═══════════════════════════════════════════════════════════
#  统一入口
# ═══════════════════════════════════════════════════════════

class CloudDiskDL:
    """网盘自动下载引擎统一入口"""

    def __init__(self):
        self.login_mgr = _get_login_mgr()
        _ensure_video_dir()
        self.engines = {
            'aliyun': AliyunEngine(),
            'baidu': BaiduEngine(),
            'quark': QuarkEngine(),
            'tianyi': TianyiEngine(),
            'xunlei': XunleiEngine(),
            '115': None,
        }

    def detect(self, url: str):
        return detect_pan(url)

    def check_login(self, pan_key: str) -> bool:
        return self.login_mgr.is_logged_in(pan_key)

    def login_prompt(self, pan_key: str) -> str:
        return self.login_mgr.login_prompt(pan_key)

    def login_status_all(self) -> dict:
        return self.login_mgr.all_status()

    def download(self, url: str, title: str, password: str = None,
                 progress_callback=None) -> dict:
        """
        全自动下载流程：
        1. 检测URL类型
        2. 检查本地空间
        3. 路由到对应引擎（API 直连）
        4. 解析分享 → 转存 → 获取直链 → 下载
        5. 返回结果
        """
        pan_info = self.detect(url)
        if not pan_info:
            return {'success': False, 'error': '无法识别的网盘链接'}

        pan_key = pan_info['key']
        engine = self.engines.get(pan_key)

        if not engine:
            return {'success': False, 'error': f'{pan_info["name"]} 暂不支持自动下载'}

        if not engine.is_logged_in():
            return {
                'success': False,
                'error': f'{pan_info["name"]} 未登录',
                'need_login': True,
                'pan_key': pan_key,
            }

        # 检查并管理本地空间
        if check_and_manage_space and estimate_download_size:
            estimated = estimate_download_size(title, url)
            space_result = check_and_manage_space(pan_key, estimated, str(VIDEO_DIR))

            if not space_result['ok']:
                return {
                    'success': False,
                    'error': space_result['error'],
                    'space_check': True,
                    'cleaned': space_result.get('cleaned'),
                }

            if space_result.get('cleaned') and space_result['cleaned'].get('deleted_count', 0) > 0:
                cleaned = space_result['cleaned']
                logger.info(
                    f"空间不足，自动清理了 {cleaned['deleted_count']} 个旧文件，"
                    f"释放 {cleaned.get('freed_human', '未知')}"
                )
                if progress_callback:
                    progress_callback(1, f'空间不足，已自动清理 {cleaned["deleted_count"]} 个旧文件')

        if progress_callback:
            progress_callback(2, f'开始处理 {pan_info["name"]} 分享链接...')

        return engine.download(url, title, password, progress_callback)


if __name__ == '__main__':
    cdd = CloudDiskDL()
    print("\n=== 网盘工具状态 ===")
    for k, v in cdd.login_status_all().items():
        status = '✅ 已登录' if v['logged_in'] else '❌ 未登录'
        print(f"  {v['name']}: {status}")

    print("\n支持检测的网盘:")
    for _, key, name in PAN_PATTERNS:
        print(f"  {name} ({key})")
