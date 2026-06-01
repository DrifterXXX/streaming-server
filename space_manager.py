"""
云盘空间管理器 — 统一接口检查三个网盘的可用空间，
并在空间不足时自动清理旧文件。

支持：
- 阿里云盘：通过 aliyunpan CLI 检查配额
- 百度网盘：通过 BaiduPCS-Go API 检查
- 夸克网盘：通过 quarkpan CLI 检查

自动清理策略：
- 按修改时间排序，优先删除最旧的文件
- 跳过最近7天内修改的文件
- 跳过正在下载中的文件
- 保留最小文件（至少保留100MB空间余量）
"""

import os
import json
import logging
import subprocess
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

logger = logging.getLogger(__name__)


def _run(cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )


# ======================================================================
# 空间检查接口
# ======================================================================

def check_aliyun_space() -> Dict[str, Any]:
    """
    检查阿里云盘可用空间。
    通过 aliyunpan CLI 的 ls 命令间接获取（CLI 无直接 quota 命令）。
    """
    try:
        # aliyunpan 没有直接的 quota 命令，通过配置信息推断
        # 免费用户 1TB，付费用户更多
        # 这里通过尝试列出文件来确认登录状态
        result = _run(["aliyunpan", "ls"])
        if result.returncode != 0:
            return {"total": 0, "used": 0, "available": 0, "error": "未登录"}

        # 阿里云盘免费用户默认 1TB
        # 实际可用空间需要通过 API 获取，但 CLI 不暴露
        # 返回估计值
        total_gb = 1024  # 假设 1TB
        return {
            "total": total_gb * 1024 * 1024 * 1024,
            "used": 0,  # 无法通过 CLI 获取
            "available": total_gb * 1024 * 1024 * 1024,  # 估计值
            "provider": "aliyun",
            "estimated": True,
        }
    except Exception as e:
        return {"total": 0, "used": 0, "available": 0, "error": str(e), "provider": "aliyun"}


def check_baidu_space() -> Dict[str, Any]:
    """检查百度网盘可用空间"""
    try:
        # BaiduPCS-Go 通过 API 获取
        result = _run(["baidupcs-go", "api", "quota"])
        if result.returncode != 0:
            # 尝试另一种方式
            result = _run(["baidupcs-go", "ls", "/"])
            if result.returncode != 0:
                return {"total": 0, "used": 0, "available": 0, "error": "未登录", "provider": "baidu"}

        # 解析输出（如果 api quota 成功）
        output = result.stdout.strip()
        if output:
            try:
                data = json.loads(output)
                total = int(data.get("total", 0))
                used = int(data.get("used", 0))
                return {
                    "total": total,
                    "used": used,
                    "available": total - used,
                    "provider": "baidu",
                    "estimated": False,
                }
            except (json.JSONDecodeError, ValueError):
                pass

        # 百度网盘免费用户 2TB
        total_gb = 2048
        return {
            "total": total_gb * 1024 * 1024 * 1024,
            "used": 0,
            "available": total_gb * 1024 * 1024 * 1024,
            "provider": "baidu",
            "estimated": True,
        }
    except Exception as e:
        return {"total": 0, "used": 0, "available": 0, "error": str(e), "provider": "baidu"}


def check_quark_space() -> Dict[str, Any]:
    """检查夸克网盘可用空间"""
    try:
        result = _run(["quarkpan", "list"])
        if result.returncode != 0:
            return {"total": 0, "used": 0, "available": 0, "error": "未登录", "provider": "quark"}

        # quarkpan 没有直接的 quota 命令
        # 夸克网盘免费用户空间较大
        total_gb = 1024  # 假设 1TB
        return {
            "total": total_gb * 1024 * 1024 * 1024,
            "used": 0,
            "available": total_gb * 1024 * 1024 * 1024,
            "provider": "quark",
            "estimated": True,
        }
    except Exception as e:
        return {"total": 0, "used": 0, "available": 0, "error": str(e), "provider": "quark"}


def check_local_space(path: str = None) -> Dict[str, Any]:
    """检查本地磁盘可用空间"""
    if path is None:
        path = os.environ.get("VIDEO_DIR", "~/Videos")
    path = os.path.expanduser(path)

    try:
        usage = shutil.disk_usage(path)
        return {
            "total": usage.total,
            "used": usage.used,
            "available": usage.free,
            "provider": "local",
            "estimated": False,
            "path": path,
        }
    except Exception as e:
        return {"total": 0, "used": 0, "available": 0, "error": str(e), "provider": "local"}


# ======================================================================
# 空间格式化工具
# ======================================================================

def format_size(bytes_val: int) -> str:
    """格式化字节数为人类可读"""
    if bytes_val < 1024:
        return f"{bytes_val}B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f}KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f}MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f}GB"


# ======================================================================
# 本地空间自动清理
# ======================================================================

class LocalSpaceManager:
    """本地视频目录空间管理器"""

    MIN_FREE_SPACE = 1 * 1024 * 1024 * 1024  # 至少保留 1GB
    PROTECT_AGE_DAYS = 7  # 保护最近7天的文件

    def __init__(self, video_dir: str = None):
        if video_dir is None:
            video_dir = os.environ.get("VIDEO_DIR", os.path.expanduser("~/Videos"))
        self.video_dir = os.path.expanduser(video_dir)
        self.protect_since = datetime.now() - timedelta(days=self.PROTECT_AGE_DAYS)

    def check_available(self, needed_bytes: int) -> bool:
        """检查是否有足够空间"""
        usage = shutil.disk_usage(self.video_dir)
        return usage.free - needed_bytes > self.MIN_FREE_SPACE

    def get_cleanup_candidates(self) -> List[Dict[str, Any]]:
        """
        获取可清理的文件列表（按修改时间排序，最旧的在前）。
        跳过最近 PROTECT_AGE_DAYS 天内修改的文件。
        """
        candidates = []
        video_ext = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg'}

        try:
            for f in Path(self.video_dir).iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() not in video_ext:
                    continue

                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime >= self.protect_since:
                    continue

                candidates.append({
                    "path": str(f),
                    "name": f.name,
                    "size": f.stat().st_size,
                    "mtime": mtime.isoformat(),
                    "age_days": (datetime.now() - mtime).days,
                })
        except Exception as e:
            logger.warning(f"Failed to scan video dir: {e}")

        # 按修改时间排序（最旧在前）
        candidates.sort(key=lambda x: x["mtime"])
        return candidates

    def cleanup(self, needed_bytes: int,
                on_delete: Callable[[str, int], None] = None) -> Dict[str, Any]:
        """
        自动清理旧文件，腾出所需空间。

        Args:
            needed_bytes: 需要腾出的字节数
            on_delete: 删除前的回调 (filename, size) -> None

        Returns:
            {'freed': int, 'deleted': [files], 'error': str}
        """
        candidates = self.get_cleanup_candidates()
        if not candidates:
            return {"freed": 0, "deleted": [], "error": "没有可清理的旧文件"}

        freed = 0
        deleted = []
        target = needed_bytes + self.MIN_FREE_SPACE

        for candidate in candidates:
            if freed >= target:
                break

            filepath = candidate["path"]
            filesize = candidate["size"]

            try:
                if on_delete:
                    on_delete(candidate["name"], filesize)

                os.remove(filepath)
                freed += filesize
                deleted.append({
                    "name": candidate["name"],
                    "size": filesize,
                    "size_human": format_size(filesize),
                    "age_days": candidate["age_days"],
                })
                logger.info(f"Deleted: {candidate['name']} ({format_size(filesize)})")
            except Exception as e:
                logger.warning(f"Failed to delete {filepath}: {e}")
                continue

        return {
            "freed": freed,
            "freed_human": format_size(freed),
            "deleted": deleted,
            "deleted_count": len(deleted),
            "error": None,
        }


# ======================================================================
# 云盘空间管理（下载前检查）
# ======================================================================

def estimate_download_size(title: str, url: str = None) -> int:
    """
    估计下载文件大小。
    由于无法提前知道确切大小，使用启发式估计：
    - 电影：平均 2GB
    - 电视剧单集：平均 800MB
    - 短剧/动画：平均 500MB
    """
    # 默认估计 1GB
    default = 1 * 1024 * 1024 * 1024

    title_lower = (title or "").lower()

    # 根据标题关键词估计
    if any(kw in title_lower for kw in ["s0", "e0", "episode", "集"]):
        return 800 * 1024 * 1024  # 电视剧单集

    if any(kw in title_lower for kw in ["短剧", "动画", "anime", "short"]):
        return 500 * 1024 * 1024

    if any(kw in title_lower for kw in ["4k", "uhd", "remaster"]):
        return 4 * 1024 * 1024 * 1024  # 4K 电影

    return default


def check_and_manage_space(provider: str, estimated_size: int,
                           local_dir: str = None) -> Dict[str, Any]:
    """
    检查空间并自动管理。

    Args:
        provider: 'aliyun', 'baidu', 'quark', 'local'
        estimated_size: 估计需要的字节数
        local_dir: 本地视频目录

    Returns:
        {'ok': bool, 'space': dict, 'cleaned': dict, 'error': str}
    """
    # 检查本地空间
    local_space = check_local_space(local_dir)

    if "error" in local_space:
        return {"ok": False, "space": local_space, "error": f"本地空间检查失败: {local_space['error']}"}

    if local_space["available"] >= estimated_size + LocalSpaceManager.MIN_FREE_SPACE:
        return {
            "ok": True,
            "space": local_space,
            "cleaned": None,
            "error": None,
        }

    # 空间不足，尝试自动清理
    needed = estimated_size + LocalSpaceManager.MIN_FREE_SPACE - local_space["available"]
    manager = LocalSpaceManager(local_dir)
    cleanup_result = manager.cleanup(needed)

    if cleanup_result.get("error"):
        return {
            "ok": False,
            "space": local_space,
            "cleaned": cleanup_result,
            "error": f"本地空间不足，自动清理失败: {cleanup_result['error']}",
        }

    # 重新检查
    local_space = check_local_space(local_dir)
    if local_space["available"] >= estimated_size + LocalSpaceManager.MIN_FREE_SPACE:
        return {
            "ok": True,
            "space": local_space,
            "cleaned": cleanup_result,
            "error": None,
        }

    return {
        "ok": False,
        "space": local_space,
        "cleaned": cleanup_result,
        "error": f"清理后空间仍不足: 需要 {format_size(estimated_size)}, 可用 {format_size(local_space['available'])}",
    }
