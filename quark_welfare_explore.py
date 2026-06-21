#!/usr/bin/env python3
"""
夸克网盘福利中心自动化探索脚本
尝试用Playwright注入cookie模拟登录，探索福利中心API
"""
import json
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

# 从pan_creds.json读取夸克cookie
CRED_FILE = Path.home() / "services/streaming-server/pan_creds.json"
with open(CRED_FILE, encoding="utf-8") as f:
    creds = json.load(f)

quark_cookie_raw = creds.get("quark", {}).get("cookie", "")
print(f"Cookie长度: {len(quark_cookie_raw)}")
print(f"Cookie前100字符: {quark_cookie_raw[:100]}...")

# 解析cookie为dict
cookie_dict = {}
for part in quark_cookie_raw.split("; "):
    if "=" in part:
        k, v = part.split("=", 1)
        cookie_dict[k.strip()] = v.strip()

print(f"\n解析出 {len(cookie_dict)} 个cookie项")
for k in list(cookie_dict.keys())[:10]:
    print(f"  {k} = {cookie_dict[k][:40]}...")

async def explore_welfare():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
            ]
        )
        
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},  # iPhone 14 Pro
        )
        
        # 注入cookie
        await context.add_cookies([
            {
                "name": k,
                "value": v,
                "domain": ".quark.cn",
                "path": "/",
                "sameSite": "Lax"
            }
            for k, v in cookie_dict.items()
        ])
        
        page = await context.new_page()
        
        # 访问夸克网盘
        print("\n🔗 访问 https://pan.quark.cn ...")
        await page.goto("https://pan.quark.cn/", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        
        # 截图
        await page.screenshot(path="/tmp/quark_login_check.png")
        print("📸 已保存登录状态截图: /tmp/quark_login_check.png")
        
        # 检查是否登录成功
        title = await page.title()
        print(f"页面标题: {title}")
        
        # 尝试访问福利中心
        print("\n🔗 访问福利中心 https://pan.quark.cn/welfare ...")
        try:
            await page.goto("https://pan.quark.cn/welfare", wait_until="networkidle")
            await page.wait_for_timeout(3000)
            await page.screenshot(path="/tmp/quark_welfare.png")
            print("📸 已保存福利中心截图: /tmp/quark_welfare.png")
            
            # 获取页面文本
            body_text = await page.inner_text("body")
            print(f"\n页面文本长度: {len(body_text)}")
            print(body_text[:2000])
            
        except Exception as e:
            print(f"访问福利中心失败: {e}")
        
        # 拦截网络请求，寻找API
        print("\n🔍 拦截网络请求...")
        requests_log = []
        
        async def log_request(request):
            url = request.url
            if "quark" in url and any(kw in url for kw in ["api", "welfare", "coin", "task", "sign", "member"]):
                requests_log.append({
                    "url": url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data[:500] if request.post_data else None,
                })
                print(f"  📡 {request.method} {url}")
        
        page.on("request", log_request)
        
        # 重新访问福利中心并捕获API调用
        print("\n🔗 重新访问福利中心捕获API...")
        await page.goto("https://pan.quark.cn/welfare", wait_until="networkidle")
        await page.wait_for_timeout(5000)
        
        print(f"\n📊 捕获到 {len(requests_log)} 个相关API请求:")
        for req in requests_log:
            print(f"  {req['method']} {req['url']}")
            if req['post_data']:
                print(f"    POST数据: {req['post_data'][:200]}")
        
        # 保存所有请求日志
        with open("/tmp/quark_api_log.json", "w", encoding="utf-8") as f:
            json.dump(requests_log, f, ensure_ascii=False, indent=2, default=str)
        print("\n💾 API日志已保存: /tmp/quark_api_log.json")
        
        # 尝试点击福利中心的元素
        print("\n🖱️ 尝试探索福利中心页面元素...")
        elements = await page.query_selector_all("*")
        print(f"页面共有 {len(elements)} 个元素")
        
        # 查找包含"金币"、"任务"、"福利"等关键词的元素
        keywords = ["金币", "任务", "福利", "签到", "会员", "兑换", "浏览", "视频", "广告"]
        for kw in keywords:
            matches = await page.get_by_text(kw).all()
            if matches:
                print(f"  关键词 '{kw}' 找到 {len(matches)} 个元素")
                for m in matches[:3]:
                    try:
                        tag = await m.evaluate("el => el.tagName")
                        print(f"    <{tag}>: {await m.inner_text()[:50]}")
                    except:
                        pass
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(explore_welfare())
