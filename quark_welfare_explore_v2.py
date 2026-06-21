#!/usr/bin/env python3
"""
夸克网盘福利中心深度探索 v2
1. 用Playwright模拟手机WebView访问福利中心
2. 尝试已知的夸克移动端API端点
3. 拦截所有网络请求寻找福利相关API
"""
import json
import asyncio
import urllib.request
import urllib.error
from pathlib import Path
from playwright.async_api import async_playwright

# 读取cookie
CRED_FILE = Path.home() / "services/streaming-server/pan_creds.json"
with open(CRED_FILE, encoding="utf-8") as f:
    creds = json.load(f)

quark_cookie_raw = creds.get("quark", {}).get("cookie", "")
cookie_dict = {}
for part in quark_cookie_raw.split("; "):
    if "=" in part:
        k, v = part.split("=", 1)
        cookie_dict[k.strip()] = v.strip()

# ─── 第一部分：尝试已知的夸克移动端API ───
async def test_quark_mobile_apis():
    print("=" * 60)
    print("🔍 测试夸克移动端福利相关API端点")
    print("=" * 60)
    
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    
    # 已知的夸克API端点
    api_tests = [
        # 签到相关
        ("GET", "https://drive-m.quark.cn/1/clouddrive/capacity/grow/clock/in"),
        ("GET", "https://drive-m.quark.cn/1/clouddrive/capacity/grow/info"),
        # 金币/福利相关
        ("GET", "https://pan.quark.cn/welfare/v1/user/coin/balance"),
        ("GET", "https://pan.quark.cn/welfare/v1/user/task/list"),
        ("POST", "https://pan.quark.cn/welfare/v1/user/task/do", {"task_id": "test"}),
        ("GET", "https://pan.quark.cn/welfare/v1/member/privilege/list"),
        # 任务相关
        ("GET", "https://drive-pc.quark.cn/1/clouddrive/member/privilege/list"),
        # 移动端专属
        ("GET", "https://drive-m.quark.cn/1/clouddrive/file/list"),
        ("GET", "https://drive-m.quark.cn/1/clouddrive/stats"),
        # 福利中心API
        ("GET", "https://pan.quark.cn/welfare/v1/home"),
        ("GET", "https://pan.quark.cn/welfare/v1/task/list"),
        ("GET", "https://pan.quark.cn/welfare/v1/sign/list"),
        ("GET", "https://pan.quark.cn/welfare/v1/coin/info"),
        # 夸克APP内嵌WebView可能访问的
        ("GET", "https://pan.quark.cn/welfare/v2/home"),
        ("GET", "https://pan.quark.cn/welfare/v2/task/list"),
        # 尝试带参数的
        ("GET", "https://pan.quark.cn/welfare/v1/task/list?scene=welfare_center"),
        ("GET", "https://pan.quark.cn/welfare/v1/user/info"),
    ]
    
    results = []
    for item in api_tests:
        if len(item) == 3:
            method, url, _ = item
        else:
            method, url = item
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "Cookie": cookie_header,
                "Referer": "https://pan.quark.cn/",
            }
            req = urllib.request.Request(url, method=method, headers=headers)
            resp = urllib.request.urlopen(req, timeout=10)
            data = resp.read().decode("utf-8", errors="replace")
            status = resp.status
            # 尝试解析JSON
            try:
                json_data = json.loads(data[:2000])
                preview = json.dumps(json_data, ensure_ascii=False)[:300]
            except:
                preview = data[:300]
            results.append({"url": url, "status": status, "preview": preview, "ok": True})
            print(f"✅ [{status}] {url}")
            print(f"   → {preview[:200]}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            results.append({"url": url, "status": e.code, "preview": body, "ok": False})
            print(f"❌ [{e.code}] {url} → {body[:150]}")
        except Exception as e:
            results.append({"url": url, "status": 0, "preview": str(e)[:200], "ok": False})
            print(f"⚠️  [ERR] {url} → {str(e)[:150]}")
    
    # 保存结果
    with open("/tmp/quark_api_test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果已保存: /tmp/quark_api_test_results.json")
    return results


async def explore_with_playwright():
    print("\n" + "=" * 60)
    print("🌐 Playwright 手机WebView探索福利中心")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        # 模拟iPhone
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
            java_script_enabled=True,
        )
        
        # 注入cookie
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".quark.cn", "path": "/", "sameSite": "Lax"}
            for k, v in cookie_dict.items()
        ])
        
        page = await context.new_page()
        
        # 拦截所有请求
        all_requests = []
        async def on_request(request):
            url = request.url
            if "quark" in url or "drive" in url:
                all_requests.append({
                    "url": url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                })
        
        page.on("request", on_request)
        
        responses = []
        async def on_response(response):
            url = response.url
            if "quark" in url or "drive" in url:
                try:
                    body = await response.body()
                    text = body.decode("utf-8", errors="replace")[:500]
                except:
                    text = ""
                responses.append({
                    "url": url,
                    "status": response.status,
                    "type": response.request.resource_type,
                    "body_preview": text,
                })
        
        page.on("response", on_response)
        
        # 访问夸克网盘
        print("\n🔗 访问 https://pan.quark.cn/ ...")
        await page.goto("https://pan.quark.cn/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)
        
        # 截图
        await page.screenshot(path="/tmp/quark_mobile_home.png")
        
        # 获取页面内容
        title = await page.title()
        print(f"页面标题: {title}")
        
        # 尝试点击登录后的用户入口
        try:
            # 查找用户头像或"我的"按钮
            my_buttons = await page.get_by_role("button", name="我的").all()
            if my_buttons:
                print(f"找到 {len(my_buttons)} 个'我的'按钮，点击第一个...")
                await my_buttons[0].click()
                await page.wait_for_timeout(3000)
                await page.screenshot(path="/tmp/quark_mobile_profile.png")
        except Exception as e:
            print(f"查找'我的'按钮失败: {e}")
        
        # 尝试直接访问福利中心
        try:
            print("\n🔗 尝试访问 https://pan.quark.cn/welfare ...")
            await page.goto("https://pan.quark.cn/welfare", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)
            await page.screenshot(path="/tmp/quark_mobile_welfare.png")
            
            # 获取页面文本
            body_text = await page.content()
            print(f"页面HTML长度: {len(body_text)}")
            
            # 查找福利中心相关元素
            for kw in ["福利", "金币", "签到", "任务", "会员", "兑换", "浏览", "视频"]:
                elements = await page.get_by_text(kw).all()
                if elements:
                    print(f"  关键词 '{kw}': {len(elements)} 个元素")
        except Exception as e:
            print(f"访问福利中心失败: {e}")
        
        # 尝试访问移动端API页面
        try:
            print("\n🔗 尝试访问移动端网盘首页 ...")
            await page.goto("https://drive-m.quark.cn/", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
            await page.screenshot(path="/tmp/quark_drive_mobile.png")
        except Exception as e:
            print(f"访问移动端失败: {e}")
        
        await browser.close()
        
        # 保存请求日志
        with open("/tmp/quark_mobile_requests.json", "w", encoding="utf-8") as f:
            json.dump({"requests": all_requests, "responses": responses}, f, ensure_ascii=False, indent=2)
        
        print(f"\n📊 捕获 {len(all_requests)} 个请求, {len(responses)} 个响应")
        
        # 筛选福利相关的响应
        welfare_responses = [r for r in responses if any(kw in r["url"] for kw in ["welfare", "coin", "task", "sign", "member", "grow", "capacity"])]
        print(f"🎯 福利相关响应: {len(welfare_responses)} 个")
        for r in welfare_responses:
            print(f"  [{r['status']}] {r['url']}")
            if r["body_preview"]:
                print(f"    → {r['body_preview'][:200]}")
        
        return responses


async def main():
    # 先测试已知API
    await test_quark_mobile_apis()
    # 再用Playwright探索
    await explore_with_playwright()


if __name__ == "__main__":
    asyncio.run(main())
