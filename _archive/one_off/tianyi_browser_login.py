#!/usr/bin/env python3
"""天翼云盘浏览器自动化登录 - 提取cookie"""
import asyncio, json, os, sys
from playwright.async_api import async_playwright

ACCOUNT = "18995289100@189.cn"
PASSWORD = "YYyy13947835."
PASSWORD = "YYyy13947835."
COOKIE_FILE = os.path.expanduser("~/Library/Application Support/cloudpan189/cookies.json")
USERDATA = os.path.expanduser("~/services/streaming-server/tianyi-browser-profile")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            USERDATA,
            headless=False,
            slow_mo=300,
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN"
        )
        page = await browser.new_page()

        print("正在打开天翼云盘登录页...")
        await page.goto("https://cloud.189.cn/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # 尝试填写登录表单
        try:
            # 查找用户名输入框
            inputs = page.locator('input[type="text"], input[type="tel"], input[name*="user"], input[name*="phone"], input[name*="account"], input[placeholder*="用户"], input[placeholder*="手机"], input[placeholder*="账号"]')
            if await inputs.count() > 0:
                await inputs.first.fill(ACCOUNT)
                print(f"已填写账号: {ACCOUNT}")

            # 查找密码输入框
            pw_inputs = page.locator('input[type="password"], input[name*="pass"]')
            if await pw_inputs.count() > 0:
                await pw_inputs.first.fill(PASSWORD)
                print("已填写密码")

            # 查找登录按钮
            btn = page.locator('button:has-text("登录"), button:has-text("登入"), a:has-text("登录"), input[value*="登录"]')
            if await btn.count() > 0:
                await btn.first.click()
                print("已点击登录按钮")
                await page.wait_for_timeout(5000)
            else:
                print("未找到登录按钮，请手动点击")
        except Exception as e:
            print(f"自动填写失败: {e}")
            print("请在浏览器中手动登录")

        print("\n等待登录完成... (页面加载后按回车继续)")
        print("如果弹出验证码，请手动输入")
        input()

        # 提取cookie
        cookies = await browser.context.cookies()
        important_cookies = {}
        for c in cookies:
            domain = c.get("domain", "")
            if "189.cn" in domain or "21cn.com" in domain:
                important_cookies[c["name"]] = c["value"]

        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
        with open(COOKIE_FILE, "w") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

        print(f"\n已保存 {len(cookies)} 个cookie到 {COOKIE_FILE}")
        print("关键cookie:")
        for k, v in important_cookies.items():
            val_preview = v[:30] + "..." if len(v) > 30 else v
            print(f"  {k}: {val_preview}")

        # 测试API
        print("\n测试API访问...")
        try:
            response = await page.goto("https://cloud.189.cn/api/portal/listFolderFile.action?categoryId=0&folderId=root&sortRule=0&pageSize=10&pageNum=1", wait_until="domcontentloaded", timeout=10000)
            body = await response.text()
            data = json.loads(body)
            if data.get("result") == 0:
                print("API访问成功！已登录。")
                files = data.get("data", {}).get("fileInfos", {}).get("list", [])
                print(f"根目录文件数: {len(files)}")
            else:
                print(f"API返回: {data}")
        except Exception as e:
            print(f"API测试失败: {e}")

        print("\n浏览器将保持打开，确认没问题后关闭即可。")
        input("按回车退出...")
        await browser.close()

asyncio.run(main())
