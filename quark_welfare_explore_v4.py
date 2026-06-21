#!/usr/bin/env python3
"""
夸克网盘福利中心深度探索 v4 - 终极尝试
1. 模拟夸克App内嵌WebView的真实环境
2. 尝试访问移动端网盘主界面，寻找福利入口
3. 尝试所有可能的福利API端点组合
4. 用Playwright拦截所有请求，寻找隐藏的API
"""
import json
import asyncio
import urllib.request
import urllib.error
import re
from pathlib import Path
from playwright.async_api import async_playwright

CRED_FILE = Path.home() / "services/streaming-server/pan_creds.json"
with open(CRED_FILE, encoding="utf-8") as f:
    creds = json.load(f)

quark_cookie_raw = creds.get("quark", {}).get("cookie", "")
cookie_dict = {}
for part in quark_cookie_raw.split("; "):
    if "=" in part:
        k, v = part.split("=", 1)
        cookie_dict[k.strip()] = v.strip()

cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())


async def try_all_api_combinations():
    """暴力测试所有可能的福利API组合"""
    print("=" * 60)
    print("🔨 暴力测试所有可能的福利API端点")
    print("=" * 60)
    
    bases = [
        "https://drive.quark.cn",
        "https://drive-m.quark.cn",
        "https://pan.quark.cn",
        "https://api.quark.cn",
        "https://api-m.quark.cn",
        "https://openapi.quark.cn",
        "https://h5.quark.cn",
        "https://h5.mobile.quark.cn",
    ]
    
    paths = [
        # 福利相关
        "/welfare/v1/user/coin/balance",
        "/welfare/v1/user/task/list",
        "/welfare/v1/task/list",
        "/welfare/v1/sign/list",
        "/welfare/v1/home",
        "/welfare/v1/user/info",
        "/welfare/v1/coin/info",
        "/welfare/v1/member/list",
        "/welfare/v2/user/coin/balance",
        "/welfare/v2/task/list",
        # 成长值/签到
        "/1/clouddrive/capacity/grow/clock/in",
        "/1/clouddrive/capacity/grow/info",
        "/1/clouddrive/capacity/grow/sign/in",
        "/1/clouddrive/capacity/grow/sign/status",
        "/1/clouddrive/capacity/grow/task/list",
        "/1/clouddrive/capacity/grow/task/do",
        # 会员相关
        "/1/clouddrive/member/privilege/list",
        "/1/clouddrive/member/info",
        "/1/clouddrive/member/task/list",
        # 任务相关
        "/1/clouddrive/task/list",
        "/1/clouddrive/task/do",
        "/1/clouddrive/task/sign/in",
        # 金币相关
        "/1/clouddrive/coin/balance",
        "/1/clouddrive/coin/info",
        "/1/clouddrive/coin/task/list",
        # 其他可能
        "/2/clouddrive/capacity/grow/clock/in",
        "/2/clouddrive/capacity/grow/info",
        "/clouddrive/welfare/coin/balance",
        "/clouddrive/welfare/task/list",
    ]
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Cookie": cookie_header,
        "Referer": "https://pan.quark.cn/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    
    success_count = 0
    json_count = 0
    
    for base in bases:
        for path in paths:
            url = f"{base}{path}"
            try:
                req = urllib.request.Request(url, method="GET", headers=headers)
                resp = urllib.request.urlopen(req, timeout=5)
                data = resp.read().decode("utf-8", errors="replace")
                
                is_json = data.strip().startswith("{") or data.strip().startswith("[")
                status = resp.status
                
                if status == 200:
                    success_count += 1
                    if is_json:
                        json_count += 1
                        try:
                            j = json.loads(data[:1000])
                            print(f"✅ [JSON] {url}")
                            print(f"   → {json.dumps(j, ensure_ascii=False)[:200]}")
                        except:
                            print(f"✅ [JSON?] {url} → {data[:150]}")
                    else:
                        # 可能是HTML壳，检查是否包含福利关键词
                        if any(kw in data for kw in ["福利", "金币", "任务", "签到", "会员"]):
                            print(f"📄 [HTML含关键词] {url}")
                        elif len(data) < 500:
                            print(f"📄 [短HTML] {url} ({len(data)} bytes)")
                            
            except urllib.error.HTTPError as e:
                pass  # 404/403忽略
            except Exception as e:
                pass  # SSL等错误忽略
    
    print(f"\n📊 总计: {success_count} 个200响应, {json_count} 个JSON响应")
    
    # 如果没找到JSON API，尝试带POST的
    print("\n🔨 尝试POST请求...")
    post_paths = [
        ("https://drive.quark.cn/1/clouddrive/capacity/grow/clock/in", {"clock_in": True}),
        ("https://drive.quark.cn/1/clouddrive/task/sign/in", {}),
        ("https://pan.quark.cn/welfare/v1/user/task/do", {"task_id": "daily_sign"}),
    ]
    
    for url, data in post_paths:
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(url, data=body, method="POST", headers={
                **headers,
                "Content-Type": "application/json",
            })
            resp = urllib.request.urlopen(req, timeout=5)
            result = resp.read().decode("utf-8", errors="replace")
            print(f"POST {url}: {resp.status} → {result[:200]}")
        except Exception as e:
            print(f"POST {url}: {e}")


async def explore_main_pan_interface():
    """探索夸克网盘主界面，寻找福利入口"""
    print("\n" + "=" * 60)
    print("🏠 探索夸克网盘主界面")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        # 用更真实的iPhone UA
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                "Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".quark.cn", "path": "/", "sameSite": "Lax"}
            for k, v in cookie_dict.items()
        ])
        
        page = await context.new_page()
        
        # 拦截所有请求
        all_api_calls = []
        
        async def on_request(request):
            url = request.url
            if any(kw in url for kw in [".quark.cn", "drive", "pan"]) and \
               not any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".svg", ".woff"]):
                all_api_calls.append({
                    "url": url,
                    "method": request.method,
                    "post_data": request.post_data[:300] if request.post_data else None,
                })
        
        page.on("request", on_request)
        
        # 访问网盘主界面
        print("\n🔗 访问 https://pan.quark.cn/ ...")
        await page.goto("https://pan.quark.cn/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)
        
        # 截图
        await page.screenshot(path="/tmp/quark_main_interface.png")
        
        # 获取页面文本
        text = await page.evaluate("() => document.body.innerText")
        print(f"页面文本: {text[:500]}")
        
        # 尝试点击可能的入口
        for selector in ['a[href*="welfare"]', 'a[href*="member"]', 'button:has-text("福利")', 
                          'button:has-text("会员")', 'button:has-text("签到")',
                          '[data-testid*="welfare"]', '[data-testid*="task"]']:
            try:
                el = await page.query_selector(selector)
                if el:
                    print(f"找到元素: {selector}")
                    await el.click()
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path=f"/tmp/quark_clicked_{selector[:20]}.png")
            except:
                pass
        
        # 尝试访问移动端网盘
        print("\n🔗 尝试访问移动端网盘 https://drive.quark.cn/ ...")
        await page.goto("https://drive.quark.cn/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path="/tmp/quark_drive_main.png")
        
        text2 = await page.evaluate("() => document.body.innerText")
        print(f"移动端页面文本: {text2[:500]}")
        
        # 尝试访问h5页面
        print("\n🔗 尝试访问h5页面 https://h5.quark.cn/ ...")
        try:
            await page.goto("https://h5.quark.cn/", wait_until="networkidle", timeout=15000)
            await page.wait_for_timeout(3000)
            await page.screenshot(path="/tmp/quark_h5.png")
        except Exception as e:
            print(f"h5访问失败: {e}")
        
        await browser.close()
        
        # 分析API调用
        print(f"\n📊 捕获 {len(all_api_calls)} 个API请求:")
        for call in all_api_calls:
            print(f"  {call['method']} {call['url']}")
            if call['post_data']:
                print(f"    POST: {call['post_data']}")
        
        # 保存结果
        with open("/tmp/quark_main_api_calls.json", "w", encoding="utf-8") as f:
            json.dump(all_api_calls, f, ensure_ascii=False, indent=2)
        
        return all_api_calls


async def try_uc_browser_context():
    """尝试模拟UC浏览器内核（夸克基于UC内核）"""
    print("\n" + "=" * 60)
    print("🌐 模拟UC浏览器内核环境")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        # 使用UC浏览器风格的UA
        uc_ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/100.0.0 Mobile Safari/537.36 UCBS/3.0 "
            "Quark/5.0.0.1"
        )
        
        context = await browser.new_context(
            user_agent=uc_ua,
            viewport={"width": 390, "height": 844},
        )
        
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".quark.cn", "path": "/", "sameSite": "Lax"}
            for k, v in cookie_dict.items()
        ])
        
        page = await context.new_page()
        
        # 拦截请求
        api_calls = []
        async def on_request(request):
            url = request.url
            if any(kw in url for kw in [".quark.cn", "drive"]) and \
               not any(ext in url for ext in [".js", ".css", ".png", ".jpg"]):
                api_calls.append({"url": url, "method": request.method})
        
        page.on("request", on_request)
        
        # 尝试访问
        urls_to_try = [
            "https://pan.quark.cn/",
            "https://drive.quark.cn/",
            "https://my.quark.cn/",
        ]
        
        for url in urls_to_try:
            try:
                print(f"\n🔗 {url}")
                await page.goto(url, wait_until="networkidle", timeout=20000)
                await page.wait_for_timeout(3000)
                text = await page.evaluate("() => document.body.innerText")
                print(f"  文本: {text[:300]}")
                await page.screenshot(path=f"/tmp/quark_uc_{url.split('//')[1].replace('/', '_')}.png")
            except Exception as e:
                print(f"  失败: {e}")
        
        await browser.close()
        
        print(f"\n📊 UC模式下捕获 {len(api_calls)} 个API请求")
        for call in api_calls:
            print(f"  {call['method']} {call['url']}")


async def main():
    # 1. 暴力测试API
    await try_all_api_combinations()
    
    # 2. 探索主界面
    await explore_main_pan_interface()
    
    # 3. 模拟UC浏览器
    await try_uc_browser_context()


if __name__ == "__main__":
    asyncio.run(main())
