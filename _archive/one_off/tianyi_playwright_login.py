#!/usr/bin/env python3
"""天翼云盘Playwright浏览器自动化登录"""
import json, os, time
from playwright.sync_api import sync_playwright

ACCOUNT = "18995289100@189.cn"
PW = "YYyy13947835."
COOKIE_FILE = os.path.expanduser("~/Library/Application Support/cloudpan189/cookies.json")
STATE_DIR = os.path.expanduser("~/services/streaming-server/tianyi-state")

def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    
    with sync_playwright() as p:
        # 使用持久化上下文保存登录状态
        ctx = p.chromium.launch_persistent_context(
            STATE_DIR,
            headless=False,
            slow_mo=200,
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            accept_downloads=True,
        )
        
        page = ctx.new_page()
        
        # 检查是否已登录
        print("检查登录状态...")
        page.goto("https://cloud.189.cn/", timeout=30000)
        page.wait_for_timeout(3000)
        
        # 检查是否在登录页
        if "login" in page.url.lower() or "open.e.189.cn" in page.url:
            print("需要登录，正在填写...")
            page.goto("https://cloud.189.cn/", timeout=30000)
            page.wait_for_timeout(3000)
            
            # 查找登录入口
            try:
                # 点击登录按钮
                login_btn = page.wait_for_selector('a:has-text("登录"), button:has-text("登录")', timeout=5000)
                if login_btn:
                    login_btn.click()
                    page.wait_for_timeout(2000)
            except:
                pass
            
            # 查找登录表单
            page.wait_for_timeout(2000)
            
            # 尝试各种选择器
            for selector in [
                'input[name="userName"]',
                'input#userName', 
                'input[placeholder*="手机"]',
                'input[placeholder*="用户"]',
                'input[type="text"]',
            ]:
                try:
                    el = page.wait_for_selector(selector, timeout=1000)
                    if el and el.is_visible():
                        el.click()
                        el.fill(ACCOUNT)
                        print(f"已填写账号 (selector: {selector})")
                        break
                except:
                    continue
            
            page.wait_for_timeout(500)
            
            # 填写密码
            try:
                pw_el = page.wait_for_selector('input[type="password"]', timeout=3000)
                if pw_el and pw_el.is_visible():
                    pw_el.click()
                    pw_el.fill(PW)
                    print("已填写密码")
            except:
                print("未找到密码输入框")
            
            # 点击登录
            page.wait_for_timeout(500)
            try:
                submit_btn = page.wait_for_selector('button:has-text("登录"), button[type="submit"]', timeout=3000)
                if submit_btn and submit_btn.is_visible():
                    submit_btn.click()
                    print("已点击登录")
            except:
                print("未找到登录按钮")
            
            # 等待登录完成
            page.wait_for_timeout(5000)
        
        print("\n请确认登录成功（如有验证码请手动输入）")
        print("登录后按回车继续...")
        
        # 保存状态
        ctx.storage_state(path=os.path.join(STATE_DIR, "state.json"))
        
        # 提取cookies
        cookies = ctx.cookies()
        with open(COOKIE_FILE, "w") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        
        print(f"\n已保存 {len(cookies)} 个cookies")
        for c in cookies:
            if "189" in c.get("domain", "") or "21cn" in c.get("domain", ""):
                v = c.get("value", "")[:50]
                print(f"  {c['name']} = {v} (domain={c.get('domain','')})")
        
        # 测试API
        print("\n测试API...")
        try:
            resp = page.request.get("https://cloud.189.cn/api/portal/folder/listFiles.action?categoryId=0&folderId=root&pageSize=5", timeout=10000)
            data = resp.json()
            result = data.get("result")
            if result == 0:
                files = data.get("data", {}).get("fileInfos", {}).get("list", [])
                print(f"API成功! 文件数: {len(files)}")
                for f in files[:5]:
                    print(f"  - {f.get('fileName', '?')}")
            else:
                print(f"API: result={result}, msg={data.get('errorMsg', '')}")
        except Exception as e:
            print(f"API测试失败: {e}")
        
        print("\n浏览器将保持打开，确认没问题后关闭即可。")
        print("按回车退出...")

if __name__ == "__main__":
    main()
