"""
流媒体服务器路由模块 — Phase 1: 从 StreamHandler.do_GET 提取路由逻辑
"""
import urllib.parse
from datetime import datetime

# 数据库模块（Phase 2: 数据库操作独立）
import database

# ─── 精确路径路由 ────────────────────────────────────────────────────
EXACT_ROUTES = {
    "/":           ("send_index",          False),
    "/index.html": ("send_index",          False),
    "/api/database":    ("send_database",      False),
    "/api/search":      ("send_search",        True),   # needs query
    "/api/search-external": ("send_search_external", True),
    "/api/download":    ("send_download",      True),
    "/api/status":      ("send_status",        False),
    "/api/queue":       ("send_status",        False),
    "/api/videos":      ("send_videos_list",   False),
    "/api/cancel":      ("send_cancel",        True),
    "/api/remove":      ("send_remove",        True),
    "/api/local-files": ("_dl_local_files",    False),
    "/api/dl-url":      ("_dl_url",            True),
    "/api/dl-cloud":    ("send_dl_cloud",      True),
    "/api/dl-batch":    ("_dl_batch",          True),
    "/api/auto-find":   ("_dl_auto_find",      True),
    "/api/auto-download": ("_dl_auto_download",  True),
    "/api/subscribe":    ("_dl_subscribe",     True),
    "/api/unsubscribe":  ("_dl_unsubscribe",   True),
    "/api/subscriptions": ("_dl_subscriptions",  False),
    "/api/subscription-log": ("_dl_subscription_log",  False),
    "/api/search-sources": ("_dl_search_sources", True),
    "/api/dl-urls":     ("send_dl_urls",       True),
    "/api/open-file":   ("send_open_file",     False),
    "/api/space":       ("_dl_space",          True),
    "/api/reload-db":   ("send_reload_db",     False),
    "/api/db-meta":     ("send_db_meta",       False),
    "/api/pan-status":  ("send_pan_status",    False),
    "/api/test":        ("_test",              True),
    "/api/search-debug": ("_search_debug",     True),
    "/api/health":      ("_health",            False),
    "/health":          ("_health",            False),
}

# ─── 前缀匹配路由 ────────────────────────────────────────────────────
PREFIX_ROUTES = [
    ("/api/poster/", "serve_poster"),
    ("/posters/",    "serve_local_poster"),
]

# ─── dl_enhancer 转发函数名映射 ────────────────────────────────────────────
DL_ENHANCER_MAP = {
    "_dl_local_files":    "handle_local_files",
    "_dl_url":            "handle_dl_url",
    "_dl_batch":          "handle_dl_batch",
    "_dl_auto_find":      "handle_auto_find",
    "_dl_auto_download":  "handle_auto_download",
    "_dl_subscribe":      "handle_subscribe",
    "_dl_unsubscribe":    "handle_unsubscribe",
    "_dl_subscriptions":  "handle_subscriptions",
    "_dl_subscription_log":  "handle_subscription_log",
    "_dl_search_sources": "handle_search_sources",
    "_dl_space":          "handle_space",
}


def resolve(path, query):
    """
    将 HTTP 路径映射到处理器方法名 + 参数。
    返回 (method_name, query_dict) 或 None（表示不由路由处理）。
    """
    # 1. 精确匹配
    if path in EXACT_ROUTES:
        method, needs_query = EXACT_ROUTES[path]
        return method, query if needs_query else None
    
    # 2. 前缀匹配
    for prefix, method in PREFIX_ROUTES:
        if path.startswith(prefix):
            return method, path  # 直接传路径字符串，而非 {"path": path}
    
    # 3. 不匹配 — 交给 SimpleHTTPRequestHandler 处理文件
    return None, None


def dispatch(handler, method_name, params):
    """
    根据 method_name 执行对应的处理器方法。
    handler:  StreamHandler 实例
    params:   参数字典
    """
    # dl_enhancer 转发的特殊处理
    if method_name in DL_ENHANCER_MAP:
        import stream_server_v3_dl as dl_enhancer
        dl_func = getattr(dl_enhancer, DL_ENHANCER_MAP[method_name])
        dl_func(handler, params, handler.download_manager)
        return
    
    # 内联端点（不涉及大量业务逻辑）
    if method_name == "_health":
        handler.send_json({"status": "ok", "time": datetime.now().isoformat()})
        return
    
    if method_name == "_test":
        db = database.MediaDB.get_db()
        handler.send_json({
            "db_loaded": True,
            "movies": [m["title"] for m in db.get("movies", [])]
        })
        return
    
    if method_name == "_search_debug":
        q = params.get("q", "")
        db = database.MediaDB.get_db()
        results = []
        details = []
        MIN_RATING = 7.0
        for m in db.get("movies", []):
            rating = m.get("rating", 0)
            title = m.get("title", "")
            matches_rating = rating >= MIN_RATING
            matches_query = not q or q.lower() in title.lower()
            details.append({
                "title": title, "rating": rating,
                "rating_ok": matches_rating, "query_match": matches_query,
                "included": matches_rating and matches_query
            })
            if matches_rating and matches_query:
                results.append(m)
        handler.send_json({"query": q, "results": results, "details": details})
        return
    
    # 普通 send_* 方法 — 通过 getattr 调用
    send_method = getattr(handler, method_name, None)
    if send_method:
        if params is not None:
            send_method(params)
        else:
            send_method()
        return
    
    # 未知路由 — 交给父类
    raise KeyError(f"Unknown route handler: {method_name}")


def route_request(handler):
    """
    StreamHandler.do_GET 的入口。
    返回 True 表示已处理，False 表示需要交给父类。
    """
    parsed = urllib.parse.urlparse(handler.path)
    path = parsed.path
    
    # POST 路由的特殊处理（搜索API使用POST避免URL编码问题）
    if path in ("/api/search", "/api/search-debug"):
        handler.handle_search_post()
        return True
    
    # 解析 query 参数
    query = {}
    if parsed.query:
        try:
            query = dict(urllib.parse.parse_qsl(parsed.query))
        except:
            pass
    
    method, params = resolve(path, query)
    
    if method is None:
        return False  # 交给父类处理（文件服务）
    
    dispatch(handler, method, params)
    return True