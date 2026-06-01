"""
阿里云盘浏览器自动化引擎 — 通过 Playwright 持久化浏览器配置文件，
实现分享链接的自动保存（转存）到用户网盘。

工作流程：
1. 启动持久化 Chromium 配置文件（~/.hermes/aliyun-browser/）
2. 首次使用需手动登录 aliyundrive.com
3. 导航到分享链接 → 自动点击"保存"按钮
4. 选择保存路径 → 等待保存完成
5. 返回保存结果，供后续 CLI 下载使用

用户只需登录一次，之后全自动。

注意：is_logged_in() 已迁移到 pan_login.PanLoginManager.aliyun_check()
此模块仅保留浏览器自动化功能（首次登录 + API 转存失败时的兜底）
"""

import os
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Playwright 导入（延迟加载，避免启动时阻塞）
_playwright = None
_chromium = None


def _ensure_playwright():
    global _playwright, _chromium
    if _playwright is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _chromium = _playwright.chromium


class AliyunBrowserEngine:
    """阿里云盘浏览器自动化引擎 — 仅保留首次登录 + save_share 兜底"""

    PROFILE_DIR = Path.home() / ".hermes" / "aliyun-browser"
    STATUS_FILE = PROFILE_DIR / "status.json"

    SHARE_DOMAINS = ["aliyundrive.com", "alipan.com"]

    def __init__(self):
        self._ensure_profile_dir()

    def _ensure_profile_dir(self):
        self.PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 登录检查已迁移到 pan_login.PanLoginManager.aliyun_check()
    # ------------------------------------------------------------------

    def is_logged_in(self) -> bool:
        """已废弃 — 请使用 PanLoginManager.aliyun_check()"""
        try:
            from pan_login import PanLoginManager
            return PanLoginManager().aliyun_check()
        except Exception:
            return False

    def prompt_login(self) -> Dict[str, Any]:
        """返回登录指引（用户需手动在弹出的浏览器中登录一次）"""
        return {
            "needs_login": True,
            "message": (
                "阿里云盘需要登录一次（仅首次）。"
                "浏览器将在后台打开，请访问 aliyundrive.com 登录你的账号，"
                "登录完成后关闭浏览器即可。"
            ),
        }

    def open_browser_for_login(self) -> Optional[str]:
        """打开有头浏览器供用户登录（返回 cdp_url 供监控）"""
        try:
            _ensure_playwright()
            browser = _chromium.launch_persistent_context(
                str(self.PROFILE_DIR),
                headless=False,
                channel="chrome",
                ignore_default_args=["--disable-extensions"],
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            if browser.pages:
                page = browser.pages[0]
            else:
                page = browser.new_page()
            page.goto("https://www.alipan.com/", timeout=15000)

            # 返回连接信息，让调用方知道浏览器已打开
            # 注意：这个浏览器会保持打开直到用户手动关闭
            cdp_url = getattr(browser, "context", None)
            return "Browser opened for login. Please log in to aliyundrive.com, then close the browser window."
        except Exception as e:
            logger.error(f"Failed to open login browser: {e}")
            return None

    def save_share(self, share_url: str, password: Optional[str] = None) -> Dict[str, Any]:
        """
        自动保存阿里云盘分享链接到用户网盘。

        流程：
        1. 打开持久化浏览器（已登录）
        2. 导航到分享链接
        3. 如有密码，自动输入
        4. 点击"保存"按钮
        5. 选择保存目录（默认根目录）
        6. 等待保存完成

        Returns:
            {'success': bool, 'saved_files': [...], 'error': str}
        """
        try:
            _ensure_playwright()
        except Exception as e:
            return {"success": False, "saved_files": [], "error": f"Playwright 不可用: {e}"}

        browser = None
        try:
            browser = _chromium.launch_persistent_context(
                str(self.PROFILE_DIR),
                headless=True,
                channel="chrome",
                ignore_default_args=["--disable-extensions"],
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            page = browser.pages[0] if browser.pages else browser.new_page()

            # 1. 导航到分享链接
            logger.info(f"Opening Aliyun share: {share_url}")
            page.goto(share_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # 2. 检查是否需要密码
            if password:
                try:
                    # 尝试输入密码
                    pwd_input = page.query_selector('input[placeholder*="密码"], input[placeholder*="提取"], input[type="text"]')
                    if pwd_input:
                        pwd_input.fill(password)
                        # 点击确认/提交按钮
                        confirm_btn = page.query_selector('button:has-text("确定"), button:has-text("提交"), button:has-text("查看")')
                        if confirm_btn:
                            confirm_btn.click()
                            page.wait_for_timeout(3000)
                        else:
                            # 尝试按回车
                            pwd_input.press("Enter")
                            page.wait_for_timeout(3000)
                except Exception as e:
                    logger.warning(f"Password input failed: {e}")

            # 3. 检查是否已登录
            url_after = page.url
            if "login" in url_after.lower() or "passport" in url_after.lower():
                return {
                    "success": False,
                    "saved_files": [],
                    "error": "阿里云盘未登录，请先运行登录流程",
                    "needs_login": True,
                }

            # 4. 查找并点击"保存"按钮
            # 阿里云盘分享页的保存按钮可能有多种样式
            save_clicked = False
            save_selectors = [
                'button:has-text("保存")',
                'button:has-text("转存")',
                '[class*="save"]:has-text("保存")',
                '[class*="Save"]',
                'a:has-text("保存")',
                # 尝试通过图标+文字组合
                'div:has-text("保存到"), span:has-text("保存到")',
            ]

            for selector in save_selectors:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        save_clicked = True
                        logger.info(f"Clicked save button with selector: {selector}")
                        break
                except Exception:
                    continue

            if not save_clicked:
                # 尝试更宽泛的搜索
                try:
                    # 查找包含"保存"字样的可点击元素
                    all_buttons = page.query_selector_all('button, [role="button"], a')
                    for btn in all_buttons:
                        try:
                            text = btn.text_content() or ""
                            if "保存" in text or "转存" in text:
                                btn.click()
                                save_clicked = True
                                logger.info(f"Clicked save button: {text}")
                                break
                        except Exception:
                            continue
                except Exception as e:
                    logger.warning(f"Broad save button search failed: {e}")

            if not save_clicked:
                return {
                    "success": False,
                    "saved_files": [],
                    "error": "未找到保存按钮，页面可能已变化或文件不可保存",
                }

            # 5. 等待保存弹窗，选择保存目录
            page.wait_for_timeout(2000)

            # 通常弹窗会默认选择根目录，直接点击确认
            confirm_selectors = [
                'button:has-text("确定")',
                'button:has-text("确认保存")',
                'button:has-text("保存")',
                '[class*="confirm"] button',
            ]

            confirm_clicked = False
            for selector in confirm_selectors:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        confirm_clicked = True
                        logger.info(f"Clicked confirm button: {selector}")
                        break
                except Exception:
                    continue

            if not confirm_clicked:
                # 尝试按回车确认
                try:
                    page.keyboard.press("Enter")
                    confirm_clicked = True
                except Exception:
                    pass

            # 6. 等待保存完成提示
            page.wait_for_timeout(3000)

            # 检查保存结果
            content = page.content()

            # 成功标志
            success_indicators = ["保存成功", "转存成功", "save success", "success"]
            fail_indicators = ["保存失败", "转存失败", "失败", "error"]

            for indicator in success_indicators:
                if indicator in content:
                    logger.info(f"Save succeeded: {indicator}")
                    return {
                        "success": True,
                        "saved_files": ["通过浏览器保存成功"],
                        "error": None,
                    }

            for indicator in fail_indicators:
                if indicator in content:
                    return {
                        "success": False,
                        "saved_files": [],
                        "error": f"保存失败: {indicator}",
                    }

            # 如果没有明确的成功/失败提示，但保存按钮已点击，假设成功
            # 因为阿里云盘有时不显示明确的成功提示
            if save_clicked and confirm_clicked:
                logger.info("Save buttons clicked, assuming success (no explicit confirmation)")
                return {
                    "success": True,
                    "saved_files": ["通过浏览器保存（自动确认）"],
                    "error": None,
                }

            return {
                "success": False,
                "saved_files": [],
                "error": "无法确认保存结果",
            }

        except Exception as e:
            logger.error(f"Aliyun browser save failed: {e}", exc_info=True)
            return {"success": False, "saved_files": [], "error": str(e)}
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    def close_browser(self):
        """关闭浏览器（如果需要）"""
        pass  # 每次操作后自动关闭
