"""
流媒体数据库模块 — Phase 2: 从 StreamHandler 提取数据库操作
"""
import json
import hashlib
import time
import os
from pathlib import Path
from datetime import datetime

DB_FILE = Path.home() / "services/streaming-server" / "database.json"
MIN_RATING = 7.0


class MediaDB:
    """媒体数据库管理器 — 带自动缓存失效检测"""

    _db_cache = None
    _cache_mtime = 0

    @classmethod
    def _write(cls, db):
        tmp_path = DB_FILE.with_suffix('.json.tmp')
        with open(tmp_path, 'w') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(DB_FILE))
        cls._cache_mtime = 0

    @classmethod
    def get_db(cls):
        """加载数据库（带缓存，文件变化时自动重读）"""
        mtime = 0
        if DB_FILE.exists():
            mtime = os.path.getmtime(str(DB_FILE))

        if cls._db_cache is None or mtime > cls._cache_mtime:
            if DB_FILE.exists():
                try:
                    with open(DB_FILE) as f:
                        cls._db_cache = json.load(f)
                except Exception:
                    cls._db_cache = {"movies": []}
            else:
                cls._db_cache = {"movies": []}
            cls._cache_mtime = mtime

        return cls._db_cache

    @classmethod
    def reload(cls):
        """强制重新加载数据库"""
        cls._db_cache = None
        return cls.get_db()

    @classmethod
    def get_filtered(cls, min_rating=None):
        """获取评分过滤后的电影列表"""
        db = cls.get_db()
        threshold = min_rating if min_rating is not None else MIN_RATING
        return [m for m in db.get("movies", []) if m.get("rating", 0) >= threshold]

    @classmethod
    def search(cls, q):
        """本地数据库按标题搜索（不限评分，标记 _in_library）"""
        db = cls.get_db()
        results = []
        for m in db.get("movies", []):
            title = m.get("title", "")
            if q.lower() in title.lower():
                m["_in_library"] = True
                results.append(m)
        return results

    @classmethod
    def search_json(cls, q):
        """搜索并评分过滤（用于JSON API）"""
        db = cls.get_db()
        results = []
        for m in db.get("movies", []):
            rating = m.get("rating", 0)
            title = m.get("title", "")
            if rating < MIN_RATING:
                continue
            if not q or q.lower() in title.lower():
                results.append(m)
        return results

    @classmethod
    def get_existing_titles(cls):
        """获取现有标题的集合（用于外部搜索去重）"""
        db = cls.get_db()
        return {m.get("title", "").lower() for m in db.get("movies", [])}

    @classmethod
    def add_entry(cls, data):
        """添加媒体条目到数据库"""
        title = data.get("title", "")
        if not title:
            return {"success": False, "error": "缺少标题"}

        id_hash = hashlib.md5(title.encode()).hexdigest()[:8]
        media_id = f"ext_{id_hash}_{int(time.time())}"

        entry = {
            "id": media_id,
            "title": title,
            "year": data.get("year", ""),
            "rating": data.get("rating", 0),
            "type": data.get("type", "电影"),
            "genre": data.get("genre", ""),
            "poster": data.get("poster", ""),  # 保存海报URL以便后续使用
            "douban_url": data.get("douban_url", ""),
            "douban_id": data.get("douban_id", ""),
            "download_urls": [],
            "seasons": data.get("seasons", []),
            "episodes": data.get("episodes", 0),
            "added_at": datetime.now().isoformat(),
        }

        db = cls.get_db()
        db.setdefault("movies", []).append(entry)
        with open(DB_FILE, "w") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

        # 强制下次重新读取
        cls._cache_mtime = 0

        return {"success": True, "id": media_id, "entry": entry}

    @classmethod
    def update_resource_sources(cls, media_id, sources):
        """
        按 media_id 写回/合并 resource_sources。
        sources: list[dict]，每个 dict 至少含 url、source、pan_type、status
        """
        db = cls.get_db()
        for m in db.get("movies", []):
            if m.get("id") == media_id:
                existing = {s.get("url"): s for s in m.get("resource_sources", []) if s.get("url")}
                for raw in sources:
                    s = cls._normalize_source(raw)
                    url = s.get("url")
                    if not url:
                        continue
                    base = existing.get(url, {})
                    existing[url] = {**base, **s, "added_at": base.get("added_at") or datetime.now().isoformat()}
                m["resource_sources"] = list(existing.values())
                m["download_urls"] = [s["url"] for s in m["resource_sources"] if s.get("status") in ("available", "need_pwd")]
                break
        else:
            return {"success": False, "error": f"未找到 media_id={media_id}"}
        cls._write(db)
        return {"success": True}

    @classmethod
    def _normalize_source(cls, s):
        if not isinstance(s, dict):
            return {"url": "", "source": "", "pan_type": "", "status": "unknown"}
        out = {
            "url": s.get("url", ""),
            "source": s.get("source", ""),
            "pan_type": s.get("pan_type", "") or s.get("_pan", "") or cls._pan_type_from_url(s.get("url", "")),
            "status": s.get("status", "available") or ("need_pwd" if s.get("_pwd") else "available"),
            "title": s.get("title", ""),
        }
        if s.get("_pwd"):
            out["pwd"] = s["_pwd"]
        if s.get("_pan_key"):
            out["_pan_key"] = s["_pan_key"]
        if s.get("use_count"):
            out["use_count"] = s["use_count"]
        if s.get("last_checked_at"):
            out["last_checked_at"] = s["last_checked_at"]
        if not out["source"] and out["pan_type"]:
            out["source"] = out["pan_type"]
        return out

    @classmethod
    def _pan_type_from_url(cls, url: str):
        import re
        for pattern, key in [("pan.baidu.com", "baidu"), ("aliyundrive.com", "aliyun"), ("alipan.com", "aliyun"),
                             ("quark.cn", "quark"), ("cloud.189.cn", "tianyi"), ("pan.xunlei.com", "xunlei"),
                             ("115.com", "115"), ("123pan.com", "123pan"), ("lanzou", "lanzou")]:
            if pattern in url:
                return key
        if url.startswith("magnet:"):
            return "magnet"
        return "link"

    @classmethod
    def get_meta(cls):
        """返回数据库元信息（修改时间、条目数、文件大小）"""
        db_path = str(DB_FILE)
        if os.path.exists(db_path):
            mtime = os.path.getmtime(db_path)
            db = cls.get_db()
            return {
                "mtime": mtime,
                "count": len(db.get("movies", [])),
                "size": os.path.getsize(db_path),
            }
        return {"mtime": 0, "count": 0, "size": 0}