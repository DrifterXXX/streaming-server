"""
网盘下载模块 — Phase 4: 从 StreamHandler 抽取网盘和云下载逻辑
"""
import threading
from datetime import datetime

# Semaphore to limit concurrent cloud downloads
_CLOUD_DL_SEMAPHORE = threading.Semaphore(5)


def handle_dl_cloud(url, title, download_manager):
    """
    网盘自动下载 — 保存分享 + 下载到本地。
    
    Args:
        url: 网盘分享链接
        title: 标题
        download_manager: DownloadManager 实例
        
    Returns:
        dict: {"success": True/False, "id": ..., "message": ..., ...}
        或 {"success": False, "error": ..., "need_login": True, ...}
    """
    if not url:
        return {"success": False, "error": "缺少URL"}
    
    # 延迟导入 CloudDiskDL
    try:
        from cloud_disk_dl import CloudDiskDL
        cdd = CloudDiskDL()
    except ImportError as e:
        return {"success": False, "error": f"网盘引擎加载失败: {str(e)}"}
    
    # 检测网盘类型
    pan_info = cdd.detect(url)
    if not pan_info:
        return {"success": False, "error": "无法识别的网盘链接"}
    
    # 检查登录
    pan_key = pan_info["key"]
    engine = cdd.engines.get(pan_key)
    if engine and hasattr(engine, "is_logged_in") and not engine.is_logged_in():
        login_prompt = cdd.login_prompt(pan_key)
        return {
            "success": False,
            "error": f"{pan_info['name']} 未登录",
            "need_login": True,
            "pan_key": pan_key,
            "login_prompt": login_prompt,
        }
    
    # 添加到下载队列（先占位）
    item_id = download_manager.add(
        title or url, url,
        output_name=f"[网盘] {title or '下载'}.mp4",
    )
    
    # 后台启动下载（通过信号量限制并发数）
    t = threading.Thread(
        target=_cloud_dl_worker_sem,
        args=(url, title or url, download_manager),
        daemon=True,
    )
    t.start()
    
    return {
        "success": True,
        "id": item_id,
        "status": "queued",
        "message": f"已排队（等待{pan_info['name']}下载）",
    }


def pan_status():
    """返回所有网盘的登录状态"""
    try:
        from cloud_disk_dl import CloudDiskDL
        cdd = CloudDiskDL()
        return {"success": True, "status": cdd.login_status_all()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _cloud_dl_worker_sem(url, title, download_manager):
    """Wrapper that acquires semaphore before delegating"""
    try:
        _CLOUD_DL_SEMAPHORE.acquire()
        _cloud_dl_worker(url, title, download_manager)
    finally:
        _CLOUD_DL_SEMAPHORE.release()


def _cloud_dl_worker(url, title, download_manager):
    """后台网盘下载工作线程"""
    from cloud_disk_dl import CloudDiskDL
    cdd = CloudDiskDL()

    # 更新队列状态（使用锁保护）
    item_id = None
    with download_manager._queue_lock:
        for item in download_manager.queue:
            if item["url"] == url and item["status"] == "pending":
                item_id = item["id"]
                item["status"] = "downloading"
                download_manager._save_queue()
                break

    if item_id is None:
        import logging
        logging.warning(f"[cloud_download] item not found in queue: {url}")
        return

    def progress(pct, msg):
        with download_manager._queue_lock:
            for item in download_manager.queue:
                if item.get("id") == item_id:
                    item["progress"] = pct
                    item["speed"] = msg
                    download_manager._save_queue()
                    break

    result = cdd.download(url, title, progress_callback=progress)

    # 更新最终状态（使用锁保护）
    with download_manager._queue_lock:
        for item in download_manager.queue:
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
                download_manager._save_queue()
                break