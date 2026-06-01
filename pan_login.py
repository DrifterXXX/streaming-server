"""
pan_login.py — 网盘统一登录管理器
==============================
单一 JSON 配置文件持久化所有网盘凭证，支持自动刷新、过期检测。

支持的网盘：
  - 阿里云盘 (aliyun) — access_token + refresh_token
  - 百度网盘 (baidu)  — BDUSS + STOKEN
  - 夸克网盘 (quark)  — cookie

使用方式：
    from pan_login import PanLoginManager
    mgr = PanLoginManager()
    if mgr.is_logged_in("aliyun"):
        token = mgr.get_token("aliyun")
"""

import os
import json
import time
import logging
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

CRED_DIR = Path.home() / "services/streaming-server"
CRED_FILE = CRED_DIR / "pan_creds.json"


class PanLoginManager:
    """统一网盘登录管理器 — 持久化凭证、自动刷新、过期检测"""

    PROVIDERS = ("aliyun", "baidu", "quark", "tianyi", "xunlei")
    PROVIDER_NAMES = {"aliyun": "阿里云盘", "baidu": "百度网盘", "quark": "夸克网盘", "tianyi": "天翼云盘", "xunlei": "迅雷云盘"}

    def __init__(self):
        CRED_DIR.mkdir(parents=True, exist_ok=True)
        self._creds = self._load()
        self._lock = __import__("threading").Lock()

    # ─── 凭证存取 ───────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        if CRED_FILE.exists():
            try:
                with open(CRED_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load creds: {e}")
        return {}

    def _save(self):
        with self._lock:
            with open(CRED_FILE, "w", encoding="utf-8") as f:
                json.dump(self._creds, f, ensure_ascii=False, indent=2)

    def get_creds(self, provider: str) -> Optional[Dict[str, Any]]:
        return self._creds.get(provider)

    def save_creds(self, provider: str, creds: Dict[str, Any]):
        self._creds[provider] = creds
        self._save()

    def clear_creds(self, provider: str):
        if provider in self._creds:
            del self._creds[provider]
            self._save()

    def get_token(self, provider: str) -> Optional[Dict[str, Any]]:
        """返回凭证 dict（含 access_token 等字段，视 provider 而定）"""
        return self.get_creds(provider)

    # ─── 公共登录状态检查 ─────────────────────────────────

    def is_logged_in(self, provider: str) -> bool:
        if provider == "aliyun":
            return self.aliyun_check()
        if provider == "baidu":
            return self.baidu_check()
        if provider == "quark":
            return self.quark_check()
        if provider == "tianyi":
            return self.tianyi_check()
        if provider == "xunlei":
            return self.xunlei_check()
        return False

    def refresh_token(self, provider: str) -> bool:
        """尝试刷新 token，返回是否成功"""
        if provider == "aliyun":
            return self._aliyun_refresh()
        return False  # baidu/quark 不支持自动 refresh

    def login_prompt(self, provider: str) -> str:
        """返回登录指引文本"""
        name = self.PROVIDER_NAMES.get(provider, provider)
        prompts = {
            "aliyun": (
                f"【{name}登录指南】\n"
                "方式一（推荐）：执行 aliyunpan login 扫码登录\n"
                "方式二：通过浏览器手动登录 aliyundrive.com\n"
                "   然后运行 python3 -c \"from pan_login import extract_aliyun_cookies; extract_aliyun_cookies()\"\n"
                "   从浏览器配置文件提取 cookie"
            ),
            "baidu": (
                f"【{name}登录指南】\n"
                "执行 BaiduPCS-Go login 扫码登录\n"
                "然后运行 python3 -c \"from pan_login import sync_baidu_cookies; sync_baidu_cookies()\"\n"
                "   从 BaiduPCS-Go 配置文件同步 token"
            ),
            "quark": (
                f"【{name}登录指南】\n"
                "1. 浏览器打开 https://pan.quark.cn 并登录\n"
                "2. 按 F12 → Application → Cookies → pan.quark.cn\n"
                "3. 复制完整 Cookie 值\n"
                "4. 执行: quarkpan auth set-cookie \"你的cookie\"\n"
            ),
            "tianyi": (
                f"【{name}登录指南】\n"
                "1. 浏览器打开 https://cloud.189.cn 并登录\n"
                "2. Cookie 会自动保存到 ~/Library/Application Support/cloudpan189/cookies.json\n"
                "3. 系统会自动同步 cookie，无需额外操作\n"
                "如果没有 cookie 文件，请运行 python3 tianyi_browser_login.py 手动登录"
            ),
            "xunlei": (
                f"【{name}登录指南】\n"
                "1. 浏览器打开 https://pan.xunlei.com 并登录\n"
                "2. 按 F12 → Network → 刷新 → 查看任意请求的 Cookie\n"
                "3. 复制 sessionid 等 cookie 值\n"
                "4. 运行 python3 -c \"from pan_login import PanLoginManager; "
                "PanLoginManager().xunlei_set_cookie('你的cookie字符串')\""
            ),
        }
        return prompts.get(provider, f"【{name}】暂不支持自动登录配置")

    def all_status(self) -> Dict[str, Dict[str, Any]]:
        """返回所有网盘的登录状态"""
        status = {}
        for provider in self.PROVIDERS:
            name = self.PROVIDER_NAMES[provider]
            status[provider] = {
                "name": name,
                "logged_in": self.is_logged_in(provider),
            }
        return status

    # ─── 阿里云盘 ──────────────────────────────────────────

    def aliyun_check(self) -> bool:
        """检查阿里云盘 token 是否有效，过期则尝试自动刷新"""
        creds = self.get_creds("aliyun")
        if not creds:
            return self._aliyun_from_cli()

        access_token = creds.get("access_token", "")
        if not access_token:
            return False

        expires_at = creds.get("expires_at", 0)
        now = time.time()

        # Token 已过期或即将过期（提前 5 分钟刷新）
        if now >= expires_at - 300:
            refresh_token = creds.get("refresh_token", "")
            if refresh_token:
                return self._aliyun_refresh()
            return False

        # Token 有效，直接返回
        return True

    def _aliyun_refresh(self) -> bool:
        """通过 refresh_token 获取新的 access_token"""
        creds = self.get_creds("aliyun")
        if not creds:
            return False

        refresh_token = creds.get("refresh_token", "")
        if not refresh_token:
            return False

        try:
            data = json.dumps({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
            req = urllib.request.Request(
                "https://open.aliyundrive.com/oauth/refresh_token",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                new_token = json.loads(resp.read())

            expires_in = new_token.get("expires_in", 86400)
            self.save_creds("aliyun", {
                **creds,
                "access_token": new_token.get("access_token", ""),
                "expires_at": time.time() + expires_in,
            })
            logger.info("Aliyun token refreshed successfully")
            return True
        except Exception as e:
            logger.warning(f"Aliyun token refresh failed: {e}")
            return False

    def _aliyun_from_cli(self) -> bool:
        """从 aliyunpan CLI 配置文件同步 token 到 pan_creds.json"""
        import shutil
        paths = [
            Path("/opt/homebrew/Cellar/aliyunpan/0.3.9/bin/aliyunpan_config.json"),
            Path.home() / ".config" / "aliyunpan" / "aliyunpan_config.json",
            Path.home() / "Library" / "Application Support" / "aliyunpan" / "aliyunpan_config.json",
        ]
        config_path = None
        for p in paths:
            if p.exists():
                config_path = p
                break
        if not config_path:
            try:
                import subprocess
                r = subprocess.run(["which", "aliyunpan"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    cfg = Path(r.stdout.strip()).parent / "aliyunpan_config.json"
                    if cfg.exists():
                        config_path = cfg
            except Exception:
                pass
        if not config_path:
            return False

        try:
            with open(config_path) as f:
                config = json.load(f)
            user = config.get("userList", [{}])[0]
            openapi = user.get("openapiToken", {})
            access_token = openapi.get("accessToken", "")
            refresh_token = openapi.get("refreshToken", "")
            if not access_token:
                return False

            expires_in = openapi.get("expiresIn", 86400)
            issued_at = openapi.get("issuedAt", int(time.time()))
            self.save_creds("aliyun", {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": issued_at + expires_in,
                "nickname": user.get("nickname", ""),
                "drive_id": user.get("activeDriveId", ""),
            })
            logger.info("Aliyun token synced from CLI config")
            return True
        except Exception as e:
            logger.warning(f"Failed to sync aliyun from CLI: {e}")
            return False

    def aliyun_save_cookies(self, session_id: str, t_token: str):
        """从浏览器提取的 cookie 保存为阿里云盘凭证（需要配合 OpenAPI 转换）"""
        self.save_creds("aliyun", {
            "session_id": session_id,
            "t_token": t_token,
            "source": "browser",
            "updated_at": datetime.now().isoformat(),
        })

    # ─── 百度网盘 ──────────────────────────────────────────

    def baidu_check(self) -> bool:
        """检查百度网盘 BDUSS 是否有效"""
        creds = self.get_creds("baidu")
        if not creds or not creds.get("BDUSS"):
            return self._baidu_from_cli()

        bdu_val = creds.get("BDUSS", "")
        if not bdu_val or len(bdu_val) < 10:
            return False

        # BDUSS 有效期较长（约 30 天），检查 updated_at
        updated_at = creds.get("updated_at", "")
        if updated_at:
            try:
                dt = datetime.fromisoformat(updated_at)
                age_days = (datetime.now() - dt).days
                if age_days > 25:
                    return self._baidu_from_cli()
            except Exception:
                pass

        return True

    def _baidu_from_cli(self) -> bool:
        """从 BaiduPCS-Go 配置文件同步 BDUSS"""
        import shutil
        bin_path = shutil.which("BaiduPCS-Go")
        if not bin_path:
            return False

        # BaiduPCS-Go 配置文件通常在 bin 同目录
        config_path = Path(bin_path).parent / "BaiduPCS-Go_config.json"
        if not config_path.exists():
            # 尝试其他位置
            for p in [
                Path.home() / ".config" / "BaiduPCS-Go" / "BaiduPCS-Go_config.json",
                Path.home() / ".BaiduPCS-Go" / "BaiduPCS-Go_config.json",
            ]:
                if p.exists():
                    config_path = p
                    break

        if not config_path:
            return False

        try:
            with open(config_path) as f:
                config = json.load(f)

            cookies = config.get("Cookies", [])
            if not cookies:
                return False

            # 取第一个账号的 cookie
            cookie_map = cookies[0]
            bdu_val = cookie_map.get("BDUSS", "")
            if not bdu_val:
                return False

            self.save_creds("baidu", {
                "BDUSS": bdu_val,
                "STOKEN": cookie_map.get("STOKEN", ""),
                "PTOKEN": cookie_map.get("PTOKEN", ""),
                "uid": str(cookie_map.get("UID", "")),
                "updated_at": datetime.now().isoformat(),
            })
            logger.info("Baidu BDUSS synced from BaiduPCS-Go config")
            return True
        except Exception as e:
            logger.warning(f"Failed to sync baidu from CLI: {e}")
            return False

    def sync_baidu_cookies(self):
        """手动触发从 BaiduPCS-Go 同步 cookie"""
        return self._baidu_from_cli()

    # ─── 夸克网盘 ──────────────────────────────────────────

    def quark_check(self) -> bool:
        """检查夸克 cookie 是否有效，过期则尝试从 CLI 同步"""
        creds = self.get_creds("quark")
        if not creds or not creds.get("cookie"):
            return self._quark_from_cli()

        cookie = creds.get("cookie", "")
        if not cookie or len(cookie) < 10:
            return False

        # 检查更新时间
        updated_at = creds.get("updated_at", "")
        if updated_at:
            try:
                dt = datetime.fromisoformat(updated_at)
                age_days = (datetime.now() - dt).days
                if age_days > 30:
                    logger.info("Quark cookie is old, attempting refresh from CLI")
                    return self._quark_from_cli()
            except Exception:
                pass

        return True

    def _quark_from_cli(self) -> bool:
        """从 quarkpan CLI cookie 文件同步"""
        import sys
        if sys.platform == "darwin":
            cookie_file = Path.home() / "Library" / "Application Support" / "quarkpan" / "cookie.txt"
        else:
            cookie_file = Path.home() / ".config" / "quarkpan" / "cookie.txt"

        if not cookie_file.exists():
            return False

        try:
            cookie = cookie_file.read_text().strip()
            if not cookie or len(cookie) < 10:
                return False

            self.save_creds("quark", {
                "cookie": cookie,
                "updated_at": datetime.now().isoformat(),
            })
            logger.info("Quark cookie synced from quarkpan config")
            return True
        except Exception as e:
            logger.warning(f"Failed to sync quark from CLI: {e}")
            return False

    def quark_set_cookie(self, cookie: str) -> bool:
        """手动设置夸克 cookie"""
        if not cookie or len(cookie) < 10:
            return False
        self.save_creds("quark", {
            "cookie": cookie,
            "updated_at": datetime.now().isoformat(),
        })
        return True

    # ─── HTTP API 辅助 ────────────────────────────────────

    def quark_is_expired(self) -> bool:
        """检查夸克 cookie 是否已确认过期（由 API 层在收到 401 时调用）"""
        return False

    def mark_quark_expired(self):
        """标记夸克 cookie 已过期"""
        creds = self.get_creds("quark")
        if creds:
            creds["expired"] = True
            self.save_creds("quark", creds)

    # ─── 天翼云盘 ──────────────────────────────────────────

    TIANYI_COOKIE_FILE = Path.home() / "Library" / "Application Support" / "cloudpan189" / "cookies.json"

    def tianyi_check(self) -> bool:
        """检查天翼云盘 cookie 是否有效"""
        creds = self.get_creds("tianyi")
        if not creds or not creds.get("cookies"):
            return self._tianyi_from_file()

        cookies = creds.get("cookies", [])
        if not cookies:
            return False

        # 检查过期时间
        updated_at = creds.get("updated_at", "")
        if updated_at:
            try:
                dt = datetime.fromisoformat(updated_at)
                age_days = (datetime.now() - dt).days
                if age_days > 30:
                    logger.info("Tianyi cookie is old, attempting refresh")
                    return self._tianyi_from_file()
            except Exception:
                pass

        return True

    def _tianyi_from_file(self) -> bool:
        """从天翼云盘 cookie 文件同步"""
        if not self.TIANYI_COOKIE_FILE.exists():
            return False

        try:
            with open(self.TIANYI_COOKIE_FILE) as f:
                cookies = json.load(f)

            if not cookies or not isinstance(cookies, list):
                return False

            # 过滤有效 cookie
            valid = [c for c in cookies if c.get("name") and c.get("value")]
            if not valid:
                return False

            self.save_creds("tianyi", {
                "cookies": valid,
                "updated_at": datetime.now().isoformat(),
            })
            logger.info(f"Tianyi cookies synced from file ({len(valid)} cookies)")
            return True
        except Exception as e:
            logger.warning(f"Failed to sync tianyi from file: {e}")
            return False

    def tianyi_cookie_header(self) -> str:
        """构建天翼云盘 Cookie header 字符串"""
        creds = self.get_creds("tianyi")
        if not creds:
            return ""
        cookies = creds.get("cookies", [])
        parts = []
        for c in cookies:
            name = c.get("name", "")
            value = c.get("value", "")
            if name and value:
                parts.append(f"{name}={value}")
        return "; ".join(parts)

    # ─── 迅雷云盘 ──────────────────────────────────────────

    def xunlei_check(self) -> bool:
        """检查迅雷云盘 cookie 是否有效"""
        creds = self.get_creds("xunlei")
        if not creds or not creds.get("cookie"):
            return False

        cookie = creds.get("cookie", "")
        if not cookie or len(cookie) < 10:
            return False

        # 检查过期时间
        updated_at = creds.get("updated_at", "")
        if updated_at:
            try:
                dt = datetime.fromisoformat(updated_at)
                age_days = (datetime.now() - dt).days
                if age_days > 30:
                    logger.info("Xunlei cookie is old")
                    return False
            except Exception:
                pass

        return True

    def xunlei_set_cookie(self, cookie: str) -> bool:
        """手动设置迅雷云盘 cookie"""
        if not cookie or len(cookie) < 10:
            return False
        self.save_creds("xunlei", {
            "cookie": cookie,
            "updated_at": datetime.now().isoformat(),
        })
        logger.info("Xunlei cookie saved")
        return True

    def xunlei_cookie_header(self) -> str:
        """返回迅雷云盘 Cookie 字符串"""
        creds = self.get_creds("xunlei")
        if not creds:
            return ""
        return creds.get("cookie", "")


# ─── 浏览器 Cookie 提取辅助函数 ────────────────────────────

def extract_aliyun_cookies():
    """从 Playwright 浏览器配置文件提取阿里云盘 cookie"""
    profile_dir = Path.home() / ".hermes" / "aliyun-browser"
    cookies_file = profile_dir / "Cookies"

    if not cookies_file.exists():
        print("未找到浏览器配置文件，请先通过浏览器登录一次")
        return False

    try:
        import sqlite3
        conn = sqlite3.connect(str(cookies_file))
        cursor = conn.execute(
            "SELECT name, value FROM cookies WHERE host_key LIKE '%aliyundrive.com%' OR host_key LIKE '%alipan.com%'"
        )
        rows = cursor.fetchall()
        conn.close()

        session_id = None
        t_token = None
        for name, value in rows:
            if name == "SESSIONID":
                session_id = value
            elif name == "t":
                t_token = value

        if session_id and t_token:
            mgr = PanLoginManager()
            mgr.aliyun_save_cookies(session_id, t_token)
            print(f"阿里云盘 Cookie 提取成功: SESSIONID={session_id[:10]}... t={t_token[:10]}...")
            return True
        else:
            print(f"未找到有效 cookie (SESSIONID={bool(session_id)}, t={bool(t_token)})")
            return False
    except Exception as e:
        print(f"提取 cookie 失败: {e}")
        return False


def sync_baidu_cookies():
    """从 BaiduPCS-Go 同步百度网盘 cookie"""
    mgr = PanLoginManager()
    if mgr._baidu_from_cli():
        print("百度网盘 BDUSS 同步成功")
        return True
    print("百度网盘 BDUSS 同步失败，请确认 BaiduPCS-Go 已登录")
    return False


if __name__ == "__main__":
    import sys
    mgr = PanLoginManager()
    print("\n=== 网盘登录状态 ===")
    for provider in mgr.PROVIDERS:
        name = mgr.PROVIDER_NAMES[provider]
        logged_in = mgr.is_logged_in(provider)
        status = "✅ 已登录" if logged_in else "❌ 未登录"
        print(f"  {name}: {status}")

        if not logged_in and "--login" in sys.argv:
            print(f"\n{name} 登录指引:")
            print(mgr.login_prompt(provider))
