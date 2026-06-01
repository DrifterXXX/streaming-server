"""
pan_api.py — 网盘 API 直连层
===========================
直接调用各网盘的 OpenAPI/WebAPI，不依赖第三方 CLI 工具。

支持的 API：
  - 阿里云盘 OpenAPI (https://open.aliyundrive.com)
  - 夸克网盘 WebAPI (https://drive-pc.quark.cn)
  - 百度网盘 PCS API (https://pan.baidu.com/rest/2.0/xpan)

核心能力：
  1. 解析分享链接 → 获取文件列表
  2. 转存分享文件到用户网盘
  3. 获取临时下载直链
  4. 列出用户网盘文件
"""

import json
import time
import logging
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List
from pathlib import Path

from pan_login import PanLoginManager

logger = logging.getLogger(__name__)


def _http_request(url: str, method: str = "GET", data: dict = None,
                  headers: dict = None, timeout: int = 30) -> Dict[str, Any]:
    """通用 HTTP 请求，返回 parsed JSON"""
    req_headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    if headers:
        req_headers.update(headers)

    body = None
    if data and method in ("POST", "PUT"):
        body = json.dumps(data).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning(f"HTTP {e.code} for {url}: {e.read(500)}")
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        logger.warning(f"Request failed for {url}: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
#  阿里云盘 OpenAPI
# ═══════════════════════════════════════════════════════════

class AliyunAPI:
    """阿里云盘 OpenAPI — 分享解析、转存、下载直链"""

    BASE = "https://open.aliyundrive.com"

    def __init__(self, login_mgr: PanLoginManager):
        self.login_mgr = login_mgr

    def _token(self) -> Optional[str]:
        creds = self.login_mgr.get_token("aliyun")
        if not creds:
            return None
        return creds.get("access_token")

    def _drive_id(self) -> str:
        creds = self.login_mgr.get_token("aliyun")
        if creds and creds.get("drive_id"):
            return creds["drive_id"]
        return ""

    def _request(self, path: str, data: dict = None) -> Dict[str, Any]:
        token = self._token()
        if not token:
            return {"error": "未登录"}
        url = f"{self.BASE}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        return _http_request(url, "POST", data, headers)

    # ─── 分享相关 ─────────────────────────────────────────

    def get_share_tokens(self, share_id: str, pwd_id: str = "") -> Optional[Dict[str, Any]]:
        """获取分享 token"""
        data = {"share_id": share_id, "share_pwd": pwd_id or None}
        result = _http_request(
            f"{self.BASE}/v2/share_link/get_share_token",
            "POST", data
        )
        if "error" in result:
            return None
        return result

    def get_share_files(self, share_id: str, share_token: str) -> List[Dict[str, Any]]:
        """获取分享文件列表"""
        files = []
        page = 1
        while True:
            data = {
                "share_id": share_id,
                "share_token": share_token,
                "parent_file_id": "root",
                "limit": 100,
                "marker": "" if page == 1 else None,
            }
            if page > 1:
                # Simple pagination — just get first page for now
                break

            result = _http_request(
                f"{self.BASE}/adrive/v3/share_link/get_shared_by_me",
                "POST", data
            )
            if "error" in result:
                # Try the share list API instead
                result = _http_request(
                    f"{self.BASE}/v2/file/list_by_share",
                    "POST", data
                )
                if "error" in result:
                    return files

            items = result.get("items", [])
            files.extend(items)
            if not result.get("next_marker"):
                break
            page += 1

        return files

    def get_share_detail(self, share_id: str, share_token: str) -> Dict[str, Any]:
        """获取分享详情"""
        data = {"share_id": share_id}
        return _http_request(
            f"{self.BASE}/v2/share_link/get_share_detail",
            "POST", data
        )

    # ─── 转存 ────────────────────────────────────────────

    def copy_file(self, share_id: str, share_token: str,
                  file_id: str, to_parent_id: str = "root") -> bool:
        """将分享文件转存到用户网盘"""
        token = self._token()
        if not token:
            return False

        drive_id = self._drive_id() or "default"
        data = {
            "file_id": file_id,
            "share_id": share_id,
            "auto_rename": True,
            "to_parent_file_id": to_parent_id,
            "to_drive_id": drive_id,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Share-Token": share_token,
        }
        result = _http_request(
            f"{self.BASE}/adrive/v1/openFile/copy",
            "POST", data, headers
        )
        return "error" not in result

    def copy_files(self, share_id: str, share_token: str,
                   file_ids: List[str], to_parent_id: str = "root") -> List[str]:
        """批量转存，返回新文件的 file_id 列表"""
        new_ids = []
        for fid in file_ids:
            if self.copy_file(share_id, share_token, fid, to_parent_id):
                new_ids.append(fid)
                time.sleep(0.5)  # 避免限流
        return new_ids

    # ─── 下载 ─────────────────────────────────────────────

    def get_download_url(self, file_id: str) -> Optional[str]:
        """获取文件临时下载直链"""
        drive_id = self._drive_id() or "default"
        result = self._request(
            "/adrive/v1/openFile/getDownloadUrl",
            {"file_id": file_id, "drive_id": drive_id}
        )
        if "error" in result:
            return None
        return result.get("download_url")

    def get_video_preview(self, file_id: str) -> Optional[str]:
        """获取视频预览播放地址（更适合流媒体播放）"""
        drive_id = self._drive_id() or "default"
        result = self._request(
            "/v1/openFile/getVideoPreviewPlayInfo",
            {"file_id": file_id, "drive_id": drive_id}
        )
        if "error" in result:
            return None
        play_info = result.get("play_info", {}).get("video_preview_play_info", {})
        return play_info.get("play_url")

    # ─── 文件列表 ─────────────────────────────────────────

    def list_files(self, parent_id: str = "root") -> List[Dict[str, Any]]:
        """列出用户网盘文件"""
        drive_id = self._drive_id() or "default"
        files = []
        page = 1
        marker = ""

        while True:
            result = self._request(
                "/adrive/v3/file/list",
                {
                    "drive_id": drive_id,
                    "parent_file_id": parent_id,
                    "limit": 100,
                    "marker": marker if page > 1 else "",
                    "order_by": "updated_at",
                    "order_direction": "DESC",
                }
            )
            if "error" in result:
                return files

            items = result.get("items", [])
            files.extend(items)
            if not result.get("next_marker"):
                break
            marker = result["next_marker"]
            page += 1

        return files

    def search_file(self, name: str) -> List[Dict[str, Any]]:
        """在用户网盘中搜索文件"""
        drive_id = self._drive_id() or "default"
        result = self._request(
            "/adrive/v3/file/search",
            {
                "drive_id": drive_id,
                "query": f"name:{name}",
                "limit": 20,
            }
        )
        if "error" in result:
            return []
        return result.get("items", [])


# ═══════════════════════════════════════════════════════════
#  夸克网盘 WebAPI
# ═══════════════════════════════════════════════════════════

class QuarkAPI:
    """夸克网盘 WebAPI — 分享解析、保存、下载直链"""

    BASE = "https://drive-pc.quark.cn"
    SHARE_BASE = "https://pan.quark.cn"

    def __init__(self, login_mgr: PanLoginManager):
        self.login_mgr = login_mgr

    def _cookie(self) -> Optional[str]:
        creds = self.login_mgr.get_token("quark")
        if not creds:
            return None
        return creds.get("cookie")

    def _request(self, path: str, method: str = "GET", data: dict = None,
                 base: str = None) -> Optional[Dict[str, Any]]:
        cookie = self._cookie()
        if not cookie:
            return {"error": "未登录"}

        url = f"{base or self.BASE}{path}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://pan.quark.cn/",
            "Origin": "https://pan.quark.cn",
            "Cookie": cookie,
        }
        result = _http_request(url, method, data, headers)

        # Check for 401 / expired cookie
        if result.get("error") and "401" in str(result.get("error")):
            self.login_mgr.mark_quark_expired()
            return None

        return result

    # ─── 分享相关 ─────────────────────────────────────────

    def get_share_info(self, share_id: str) -> Optional[Dict[str, Any]]:
        """获取分享 token 和基本信息"""
        result = _http_request(
            f"{self.SHARE_BASE}/share/shareinfo?share_id={share_id}",
            "GET", headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://pan.quark.cn/",
            }
        )
        if result.get("status") != 200 or not result.get("data"):
            return None
        return result["data"]

    def get_share_files(self, share_id: str, share_token: str) -> List[Dict[str, Any]]:
        """获取分享文件列表"""
        result = _http_request(
            f"{self.SHARE_BASE}/share/sharefile?share_id={share_id}&share_token={share_token}&pwd_id=&page=1&size=50",
            "GET", headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://pan.quark.cn/",
            }
        )
        if not result or result.get("status") != 200:
            return []
        return result.get("data", {}).get("file_list", [])

    # ─── 保存分享 ─────────────────────────────────────────

    def save_share(self, share_id: str, share_token: str,
                   fid: str, save_path: str = "/") -> bool:
        """将分享文件保存到用户网盘"""
        data = {
            "share_id": share_id,
            "share_token": share_token,
            "fid_list": [fid],
            "save_dir_path": save_path,
            "save_dir_fid": "0",
            "pwd_id": "",
            "scene": "pc",
        }
        result = self._request("/1/clouddrive/share/sharePageSave", "POST", data)
        if not result:
            return False
        return result.get("status") == 200 or result.get("code") == 0

    # ─── 下载 ─────────────────────────────────────────────

    def get_download_url(self, fid: str) -> Optional[str]:
        """获取文件临时下载直链"""
        data = {"fid": fid}
        result = self._request("/1/clouddrive/file/download", "POST", data)
        if not result or result.get("status") != 200:
            return None
        return result.get("data", {}).get("download_url")

    # ─── 文件列表 ─────────────────────────────────────────

    def list_files(self, pdir_fid: str = "0") -> List[Dict[str, Any]]:
        """列出用户网盘文件"""
        files = []
        page = 1
        while True:
            data = {
                "pdir_fid": pdir_fid,
                "prune": 1,
                "_page_size": 100,
                "_page": page,
                "_sort": "file_type:asc,updated_at:desc",
                "_fetch_total": 1,
                "_fetch_banner": 0,
                "_fetch_share": 0,
                "_fetch_save": 0,
                "_fetch_thumbnail": 0,
                "_fetch_video_tag": 0,
                "_fetch_star": 0,
            }
            result = self._request("/1/clouddrive/file/sort", "POST", data)
            if not result or result.get("status") != 200:
                return files

            items = result.get("data", {}).get("list", [])
            files.extend(items)
            total = result.get("data", {}).get("total", 0)
            if len(files) >= total:
                break
            page += 1

        return files

    def search_file(self, name: str) -> List[Dict[str, Any]]:
        """在用户网盘中搜索文件"""
        data = {
            "keyword": name,
            "page": 1,
            "size": 20,
            "category": "all",
            "sort_rule": "file_name",
            "filter_type": 0,
        }
        result = self._request("/1/clouddrive/file/search", "POST", data)
        if not result or result.get("status") != 200:
            return []
        return result.get("data", {}).get("list", [])


# ═══════════════════════════════════════════════════════════
#  百度网盘 PCS API
# ═══════════════════════════════════════════════════════════

class BaiduAPI:
    """百度网盘 PCS API — 分享解析、转存、下载直链"""

    BASE = "https://pan.baidu.com/rest/2.0"
    SHARE_BASE = "https://pan.baidu.com/share"

    def __init__(self, login_mgr: PanLoginManager):
        self.login_mgr = login_mgr

    def _cookies(self) -> Optional[str]:
        creds = self.login_mgr.get_token("baidu")
        if not creds:
            return None
        bdu_val = creds.get("BDUSS", "")
        if not bdu_val:
            return None
        parts = [f"BDUSS={bdu_val}"]
        if creds.get("STOKEN"):
            parts.append(f"STOKEN={creds['STOKEN']}")
        if creds.get("PTOKEN"):
            parts.append(f"PTOKEN={creds['PTOKEN']}")
        return "; ".join(parts)

    def _request(self, path: str, method: str = "GET", data: dict = None,
                 params: dict = None, base: str = None) -> Optional[Dict[str, Any]]:
        cookies = self._cookies()
        if not cookies:
            return {"error": "未登录"}

        url = f"{base or self.BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Cookie": cookies,
        }
        return _http_request(url, method, data, headers)

    # ─── 分享相关 ─────────────────────────────────────────

    def get_share_info(self, share_id: str, pwd: str = None) -> Optional[Dict[str, Any]]:
        """获取分享文件列表"""
        params = {"shareid": share_id, "uk": "", "page": 1, "num": 100, "order": "time"}
        if pwd:
            params["pwd"] = pwd

        result = _http_request(
            f"{self.SHARE_BASE}/link?{urllib.parse.urlencode(params)}",
            "GET", headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": f"https://pan.baidu.com/s/{share_id}",
            }
        )
        if result.get("errno") != 0:
            return None
        return result

    # ─── 转存 ─────────────────────────────────────────────

    def save_share(self, share_id: str, pwd: str, file_list: List[Dict[str, Any]],
                   save_path: str = "/") -> List[int]:
        """转存分享文件到用户网盘，返回保存成功的 fs_id 列表"""
        saved_ids = []
        for f in file_list:
            data = {
                "shareid": share_id,
                "from": "1",
                "ondup": "newcopy",
                "path": save_path,
                "fid_list": json.dumps([str(f.get("fs_id", f.get("fs_id")))]),
            }
            if pwd:
                data["sekey"] = f.get("sekey", "")

            result = self._request(
                "/xpan/file?method=filemanager&opera=transfer",
                "POST", data, params={"app_id": "250528"}
            )
            if result and result.get("errno") == 0:
                info_list = result.get("info", {}).get("info", [])
                if info_list:
                    saved_ids.append(info_list[0].get("fs_id"))
        return saved_ids

    # ─── 下载 ─────────────────────────────────────────────

    def get_download_url(self, fs_id: str) -> Optional[str]:
        """获取文件临时下载直链"""
        params = {
            "app_id": "250528",
            "channel": "chunlei",
            "clienttype": "0",
            "df": "sh",
            "dfid": fs_id,
            "dtype": "1",
            "from": "1",
            "logid": str(int(time.time() * 1000)),
            "seid": str(int(time.time() * 1000)),
        }
        result = self._request("/xpan/file?method=rapidupload", "GET", params=params)
        if not result:
            return None

        # 百度网盘下载需要特殊处理 — 获取直链
        # 通过 /api/download 接口
        params["method"] = "download"
        params["path"] = "/"  # 需要完整路径
        result = self._request("/api/download", "GET", params=params)
        if result and "error" not in str(result):
            return result.get("redirectURL") or result.get("download_url")
        return None

    def get_file_download(self, path: str) -> Optional[str]:
        """通过文件路径获取下载直链"""
        params = {
            "path": path,
            "type": "dlink",
        }
        result = self._request("/xpan/file?method=download&app_id=250528", "GET", params=params)
        if result and result.get("errno") == 0:
            return result.get("dlink")
        return None

    # ─── 文件列表 ─────────────────────────────────────────

    def list_files(self, path: str = "/") -> List[Dict[str, Any]]:
        """列出用户网盘文件"""
        result = self._request(
            "/xpan/file?method=list&app_id=250528",
            "GET", params={"path": path, "num": 100, "order": "time"}
        )
        if not result or result.get("errno") != 0:
            return []
        return result.get("list", [])


# ═══════════════════════════════════════════════════════════
#  天翼云盘 API
# ═══════════════════════════════════════════════════════════

class TianyiAPI:
    """天翼云盘 API — 分享解析、转存、下载直链"""

    BASE = "https://cloud.189.cn/api"
    SHARE_BASE = "https://cloud.189.cn"

    def __init__(self, login_mgr: PanLoginManager):
        self.login_mgr = login_mgr

    def _cookie_header(self) -> str:
        return self.login_mgr.tianyi_cookie_header()

    def _request(self, path: str, method: str = "GET", data: dict = None,
                 params: dict = None, base: str = None) -> Optional[Dict[str, Any]]:
        cookie = self._cookie_header()
        if not cookie:
            return {"error": "未登录"}

        url = f"{base or self.BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://cloud.189.cn/",
            "Cookie": cookie,
        }
        return _http_request(url, method, data, headers)

    # ─── 分享相关 ─────────────────────────────────────────

    def get_share_info(self, share_id: str, pwd: str = None) -> Optional[Dict[str, Any]]:
        """获取分享文件列表"""
        params = {
            "shareId": share_id,
            "pageNo": 1,
            "pageSize": 100,
        }
        if pwd:
            params["password"] = pwd

        result = self._request(
            "/portal/share/list.action",
            "GET", params=params, base=self.SHARE_BASE
        )
        if not result or result.get("result") != 0:
            return None
        return result

    # ─── 转存 ─────────────────────────────────────────────

    def save_share(self, share_id: str, file_ids: List[str],
                   folder_id: str = "root", pwd: str = None) -> List[str]:
        """转存分享文件到用户网盘，返回保存成功的 fileId 列表"""
        saved_ids = []
        for fid in file_ids:
            params = {
                "shareId": share_id,
                "fileId": fid,
                "folderId": folder_id,
                "fileName": "",
            }
            if pwd:
                params["password"] = pwd

            result = self._request(
                "/portal/share/save.action",
                "GET", params=params, base=self.SHARE_BASE
            )
            if result and result.get("result") == 0:
                data = result.get("data", {})
                file_id = data.get("fileId") or data.get("fileIdList", [None])[0]
                if file_id:
                    saved_ids.append(file_id)
        return saved_ids

    # ─── 下载 ─────────────────────────────────────────────

    def get_download_url(self, file_id: str) -> Optional[str]:
        """获取文件临时下载直链"""
        result = self._request(
            "/portal/download.action",
            "GET", params={"fileId": file_id}
        )
        if not result or result.get("result") != 0:
            return None
        return result.get("data", {}).get("url") or result.get("data", {}).get("downloadUrl")

    # ─── 文件列表 ─────────────────────────────────────────

    def list_files(self, folder_id: str = "root") -> List[Dict[str, Any]]:
        """列出用户网盘文件"""
        result = self._request(
            "/portal/folder/listFiles.action",
            "GET", params={"categoryId": "0", "folderId": folder_id, "pageSize": 100}
        )
        if not result or result.get("result") != 0:
            return []
        return result.get("data", {}).get("fileInfos", {}).get("list", [])

    def search_file(self, name: str) -> List[Dict[str, Any]]:
        """在用户网盘中搜索文件"""
        result = self._request(
            "/portal/search/searchByName.action",
            "GET", params={"keyword": name, "pageSize": 20}
        )
        if not result or result.get("result") != 0:
            return []
        return result.get("data", {}).get("fileInfos", {}).get("list", [])


# ═══════════════════════════════════════════════════════════
#  迅雷云盘 API
# ═══════════════════════════════════════════════════════════

class XunleiAPI:
    """迅雷云盘 API — 分享解析、转存、下载直链"""

    BASE = "https://pan.xunlei.com"

    def __init__(self, login_mgr: PanLoginManager):
        self.login_mgr = login_mgr

    def _cookie_header(self) -> str:
        return self.login_mgr.xunlei_cookie_header()

    def _request(self, path: str, method: str = "GET", data: dict = None,
                 params: dict = None) -> Optional[Dict[str, Any]]:
        cookie = self._cookie_header()
        if not cookie:
            return {"error": "未登录"}

        url = f"{self.BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://pan.xunlei.com/",
            "Cookie": cookie,
        }
        return _http_request(url, method, data, headers)

    # ─── 分享相关 ─────────────────────────────────────────

    def get_share_info(self, share_id: str, pwd: str = None) -> Optional[Dict[str, Any]]:
        """获取分享文件列表"""
        params = {"shareId": share_id}
        if pwd:
            params["password"] = pwd

        result = self._request(
            "/api/share/getShareInfo",
            "GET", params=params
        )
        if not result or result.get("code") != 0 or result.get("ret") != 0:
            return None
        return result

    # ─── 转存 ─────────────────────────────────────────────

    def save_share(self, share_id: str, file_ids: List[str],
                   folder_id: str = "0", pwd: str = None) -> List[str]:
        """转存分享文件到用户网盘"""
        saved_ids = []
        for fid in file_ids:
            data = {
                "shareId": share_id,
                "fileId": fid,
                "saveDir": folder_id,
            }
            if pwd:
                data["password"] = pwd

            result = self._request(
                "/api/share/saveShareFile",
                "POST", data
            )
            if result and (result.get("code") == 0 or result.get("ret") == 0):
                save_info = result.get("data", {})
                file_id = save_info.get("fileId") or save_info.get("saveFileId")
                if file_id:
                    saved_ids.append(str(file_id))
        return saved_ids

    # ─── 下载 ─────────────────────────────────────────────

    def get_download_url(self, file_id: str) -> Optional[str]:
        """获取文件临时下载直链"""
        result = self._request(
            "/api/file/download",
            "GET", params={"fileId": file_id}
        )
        if not result or result.get("code") != 0 or result.get("ret") != 0:
            return None
        data = result.get("data", {})
        return data.get("downloadUrl") or data.get("url") or data.get("link")

    # ─── 文件列表 ─────────────────────────────────────────

    def list_files(self, folder_id: str = "0") -> List[Dict[str, Any]]:
        """列出用户网盘文件"""
        result = self._request(
            "/api/file/list",
            "GET", params={"dirId": folder_id, "page": 1, "pageSize": 100}
        )
        if not result or result.get("code") != 0 or result.get("ret") != 0:
            return []
        return result.get("data", {}).get("files", result.get("data", {}).get("list", []))

    def search_file(self, name: str) -> List[Dict[str, Any]]:
        """在用户网盘中搜索文件"""
        result = self._request(
            "/api/file/search",
            "GET", params={"keyword": name, "page": 1, "pageSize": 20}
        )
        if not result or result.get("code") != 0 or result.get("ret") != 0:
            return []
        return result.get("data", {}).get("files", result.get("data", {}).get("list", []))
