"""
外部搜索服务模块 — Phase 3: 豆瓣+TMDB 搜索，从 StreamHandler 抽取
"""
import json
import re
import urllib.parse
import urllib.request


def search_external(q, existing_titles=None):
    """
    搜索外部源（豆瓣+TMDB），返回不在本地库的作品列表。
    
    Args:
        q: 搜索关键词
        existing_titles: 已存在于本地库的标题集合（用于去重）
        
    Returns:
        list of dict: 搜索结果
    """
    if not q:
        return []
    
    if existing_titles is None:
        existing_titles = set()
    
    results = []
    
    # ─── 豆瓣搜索 ─────────────────────────────────────────────────
    try:
        douban_url = f"https://movie.douban.com/j/subject_suggest?q={urllib.parse.quote(q)}"
        req = urllib.request.Request(douban_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://movie.douban.com/"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            douban_data = json.loads(resp.read())
            for item in douban_data[:8]:  # 最多8个外部结果
                title = item.get("title", "")
                title_lower = title.lower()
                
                rating = _parse_rating(item)
                
                # 通过 TMDB 补充评分
                if rating == 0:
                    rating = _tmdb_rating(title)
                
                media_type = "电视剧" if item.get("sub_type") == "tv" else "电影"
                year = item.get("year", "")
                
                episodes, seasons_count = 0, 1
                if media_type == "电视剧":
                    episodes, seasons_count = _tmdb_episode_info(title)
                
                result = {
                    "title": title,
                    "year": year,
                    "rating": rating,
                    "type": media_type,
                    "genre": item.get("genres", "").replace("/", "/"),
                    "poster": item.get("img", ""),
                    "douban_id": item.get("id", ""),
                    "douban_url": f"https://movie.douban.com/subject/{item.get('id', '')}/",
                    "_source": "douban",
                    "_in_library": title_lower in existing_titles,
                    "episodes": episodes or (12 if media_type == "电视剧" else 0),
                    "seasons": [],
                }
                
                # 构建季信息
                if media_type == "电视剧" and episodes > 0:
                    eps_per_season = episodes // max(seasons_count, 1)
                    result["seasons"] = [
                        {
                            "season": i + 1,
                            "episode_count": eps_per_season
                            if i + 1 < seasons_count
                            else episodes - eps_per_season * (seasons_count - 1),
                        }
                        for i in range(seasons_count)
                    ]
                
                results.append(result)
    except Exception:
        pass  # 豆瓣搜索失败，继续尝试 TMDB
    
    # ─── TMDB 补充搜索（豆瓣结果不足时）──────────────────────────────
    if len(results) < 3:
        results.extend(_tmdb_fallback_search(q, results, existing_titles))
    
    return results


def _parse_rating(item):
    """解析豆瓣评分"""
    rating = item.get("rate", "0")
    try:
        return float(rating)
    except (ValueError, TypeError):
        return 0.0


def _tmdb_rating(title):
    """通过 TMDB 搜索获取评分"""
    try:
        url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(title)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            match = re.search(r'data-rating="([\d.]+)"', html)
            if match:
                tmdb_rating = float(match.group(1))
                if tmdb_rating > 0:
                    return tmdb_rating / 2  # TMDB 10分制 → 5分制
    except Exception:
        pass
    return 0.0


def _tmdb_episode_info(title):
    """通过 TMDB 获取剧集信息（集数、季数）"""
    episodes, seasons_count = 0, 1
    try:
        url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(title)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            tv_match = re.search(r'class="media"[^>]*>(电视剧|剧集|TV)', html, re.I)
            if tv_match:
                ep_match = re.search(r'(\d+) 集', html)
                if ep_match:
                    episodes = int(ep_match.group(1))
                season_match = re.search(r'(\d+) 季', html)
                if season_match:
                    seasons_count = int(season_match.group(1))
    except Exception:
        pass
    return episodes, seasons_count


def _tmdb_fallback_search(q, existing_results, existing_titles):
    """当豆瓣搜索结果不足时，从 TMDB 补充"""
    new_results = []
    try:
        url = f"https://www.themoviedb.org/search?query={urllib.parse.quote(q)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            blocks = html.split("data-object-id=")
            for block in blocks[1:6]:
                title_match = re.search(r'alt="([^"]+)"', block)
                img_match = re.search(
                    r'src="(https://media\.themoviedb\.org/t/p/[^"]+\.jpg)"', block
                )
                if not (title_match and img_match):
                    continue
                
                tmdb_title = title_match.group(1)
                tmdb_title_lower = tmdb_title.lower()
                
                # 跳过已存在的
                if any(r["title"].lower() == tmdb_title_lower for r in existing_results):
                    continue
                if tmdb_title_lower in existing_titles:
                    continue
                
                img_url = img_match.group(1).replace(
                    "/t/p/w94_and_h141_face/", "/t/p/original/"
                ).replace("media.themoviedb.org", "image.tmdb.org")
                
                new_results.append({
                    "title": tmdb_title,
                    "year": "",
                    "rating": 0,
                    "type": "电影",
                    "genre": "",
                    "poster": img_url,
                    "douban_id": "",
                    "douban_url": "",
                    "_source": "tmdb",
                    "_in_library": False,
                    "episodes": 0,
                    "seasons": [],
                })
    except Exception:
        pass
    return new_results