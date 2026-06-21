#!/usr/bin/env python3
"""
夸克网盘福利中心深度探索 v3
1. 获取福利中心SPA的完整HTML，提取JS bundle
2. 用Playwright等待JS执行后捕获真实的API调用
3. 尝试从JS bundle中提取API端点
"""
import json
import re
import asyncio
import urllib.request
import urllib.error
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


async def extract_js_from_spa():
    """获取福利中心SPA的HTML，提取JS bundle URL"""
    print("=" * 60)
    print("📦 提取福利中心SPA的JS Bundle")
    print("=" * 60)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Cookie": cookie_header,
    }
    
    resp = urllib.request.urlopen(urllib.request.Request("https://pan.quark.cn/welfare", headers=headers), timeout=15)
    html = resp.read().decode("utf-8", errors="replace")
    
    # 保存HTML
    with open("/tmp/quark_welfare_spa.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML长度: {len(html)}")
    
    # 提取所有script标签
    script_urls = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
    print(f"\n找到 {len(script_urls)} 个JS文件:")
    for url in script_urls:
        full_url = url if url.startswith("http") else f"https://pan.quark.cn{url}"
        print(f"  {full_url}")
    
    # 提取内联JS中的API端点
    inline_scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    api_patterns = [
        r'["\'](/[^"\']*?(?:welfare|coin|task|sign|member|grow|capacity)[^"\']*)["\']',
        r'["\']([^"\']*?(?:api\.quark|drive\.quark|pan\.quark)[^"\']*)["\']',
        r'fetch\(["\']([^"\']+)["\']',
        r'\.get\(["\']([^"\']+)["\']',
        r'\.post\(["\']([^"\']+)["\']',
    ]
    
    found_apis = set()
    for script in inline_scripts:
        for pat in api_patterns:
            matches = re.findall(pat, script)
            for m in matches:
                found_apis.add(m)
    
    print(f"\n从内联JS中找到 {len(found_apis)} 个可能的API端点:")
    for api in sorted(found_apis):
        print(f"  {api}")
    
    return html, script_urls, found_apis


async def capture_api_calls_with_playwright():
    """用Playwright加载SPA，等待JS执行后捕获真实API调用"""
    print("\n" + "=" * 60)
    print("🕵️  Playwright 捕获JS执行后的真实API调用")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
        )
        
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".quark.cn", "path": "/", "sameSite": "Lax"}
            for k, v in cookie_dict.items()
        ])
        
        page = await context.new_page()
        
        # 收集所有XHR/fetch请求
        api_calls = []
        
        async def on_request(request):
            url = request.url
            # 只关注API请求（排除静态资源）
            if any(kw in url for kw in ["/v1/", "/v2/", "/api/", "clouddrive", "welfare", "coin", "task", "sign"]):
                if not any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff"]):
                    api_calls.append({
                        "url": url,
                        "method": request.method,
                        "type": request.resource_type,
                        "post_data": request.post_data[:500] if request.post_data else None,
                    })
                    print(f"  📡 {request.method} {url}")
                    if request.post_data:
                        print(f"     POST: {request.post_data[:200]}")
        
        page.on("request", on_request)
        
        # 访问福利中心
        print("\n🔗 访问 https://pan.quark.cn/welfare ...")
        await page.goto("https://pan.quark.cn/welfare", wait_until="domcontentloaded", timeout=30000)
        print("  DOM加载完成，等待JS执行...")
        
        # 等待SPA完全加载
        await page.wait_for_timeout(8000)
        
        # 截图
        await page.screenshot(path="/tmp/quark_welfare_spa_rendered.png")
        
        # 获取页面渲染后的文本
        body_text = await page.evaluate("() => document.body.innerText")
        print(f"\n渲染后页面文本长度: {len(body_text)}")
        print(body_text[:1000])
        
        # 再等一会儿看是否有延迟加载的API
        await page.wait_for_timeout(5000)
        
        await browser.close()
        
        print(f"\n📊 总共捕获 {len(api_calls)} 个API调用")
        
        # 保存结果
        with open("/tmp/quark_api_calls.json", "w", encoding="utf-8") as f:
            json.dump(api_calls, f, ensure_ascii=False, indent=2)
        
        return api_calls


async def try_direct_cookie_api():
    """尝试直接用cookie调用可能存在的真实API"""
    print("\n" + "=" * 60)
    print("🎯 尝试直接调用可能的真实API")
    print("=" * 60)
    
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    
    # 基于SPA分析推测的真实API
    api_tests = [
        # 基于常见夸克API模式
        ("GET", "https://pan.quark.cn/welfare/v1/user/coin/balance", {}),
        ("GET", "https://pan.quark.cn/welfare/v1/task/list", {"scene": "welfare_center"}),
        ("GET", "https://pan.quark.cn/welfare/v1/sign/list", {}),
        ("GET", "https://pan.quark.cn/welfare/v1/home", {}),
        ("GET", "https://pan.quark.cn/welfare/v1/user/info", {}),
        # 尝试带Accept: application/json
        ("GET", "https://pan.quark.cn/welfare/v1/user/coin/balance", {"Accept": "application/json"}),
        # 移动端drive API
        ("GET", "https://drive.quark.cn/1/clouddrive/capacity/grow/clock/in", {}),
        ("GET", "https://drive.quark.cn/1/clouddrive/capacity/grow/info", {}),
        ("GET", "https://drive.quark.cn/1/clouddrive/capacity/grow/sign/in", {}),
        # 尝试不同的base
        ("GET", "https://api.quark.cn/welfare/v1/user/coin/balance", {}),
        ("GET", "https://api-m.quark.cn/welfare/v1/user/coin/balance", {}),
    ]
    
    results = []
    for method, url, extra_headers in api_tests:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "Cookie": cookie_header,
                "Referer": "https://pan.quark.cn/welfare",
                "Origin": "https://pan.quark.cn",
            }
            headers.update(extra_headers)
            
            req = urllib.request.Request(url, method=method, headers=headers)
            resp = urllib.request.urlopen(req, timeout=10)
            data = resp.read().decode("utf-8", errors="replace")
            
            # 检查是否是JSON
            is_json = data.strip().startswith("{") or data.strip().startswith("[")
            
            results.append({"url": url, "status": resp.status, "is_json": is_json, "data": data[:500]})
            
            if is_json:
                try:
                    j = json.loads(data[:2000])
                    print(f"✅ [JSON] {url}")
                    print(f"   → {json.dumps(j, ensure_ascii=False)[:300]}")
                except:
                    print(f"✅ [JSON?] {url} → {data[:200]}")
            else:
                print(f"📄 [HTML] {url} ({resp.status})")
                
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            print(f"❌ [{e.code}] {url} → {body[:150]}")
        except Exception as e:
            print(f"⚠️  [ERR] {url} → {str(e)[:150]}")
    
    return results


async def main():
    # 1. 提取SPA的JS
    html, scripts, apis = await extract_js_from_spa()
    
    # 2. Playwright捕获真实API
    api_calls = await capture_api_calls_with_playwright()
    
    # 3. 尝试直接调用
    direct_results = await try_direct_cookie_api()


if __name__ == "__main__":
    asyncio.run(main())
