#!/usr/bin/env python3
"""天翼云盘Python直接登录"""
import requests, json, os, re
from urllib.parse import quote

ACCOUNT = "18995289100@189.cn"
PASSWORD=*** = os.path.expanduser("~/Library/Application Support/cloudpan189/cookies.json")

def login():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    
    # 1. 访问登录页获取captchaToken
    print("获取登录页参数...")
    r = s.get("https://open.e.189.cn/api/logbox/oauth2/loginHtml.do?redirectURL=https%3A%2F%2Fcloud.189.cn%2F", timeout=15)
    
    # 提取captchaToken
    m = re.search(r'"captchaToken"\s*:\s*"([^"]+)"', r.text)
    captcha_token = m.group(1) if m else ""
    print(f"captchaToken: {captcha_token[:30]}..." if captcha_token else "未找到captchaToken")
    
    # 2. 提交登录
    print("提交登录...")
    r = s.post("https://open.e.189.cn/api/logbox/oauth2/loginSubmit.do", data={
        "appKey": "cloud",
        "account": ACCOUNT,
        "password": PASSWORD,
        "validateCode": "",
        "captchaToken": captcha_token,
        "returnUrl": "https://cloud.189.cn/",
        "mailSuffix": "@189.cn",
        "dynamicCheck": "false",
        "clientType": "1",
        "cb": "callback",
    }, timeout=15)
    
    print(f"响应: {r.text[:500]}")
    
    try:
        data = r.json()
        result = data.get("result")
        msg = data.get("msg", "")
        print(f"result={result}, msg={msg}")
        
        if result == 0:
            print("登录成功！")
            redirect = data.get("redirectUrl", "")
            if redirect:
                r2 = s.get(redirect, allow_redirects=True, timeout=15)
                print(f"重定向后: {r2.status_code}")
        elif "验证码" in msg:
            print("需要验证码，尝试用clientType=5免验证码...")
            r = s.post("https://open.e.189.cn/api/logbox/oauth2/loginSubmit.do", data={
                "appKey": "cloud",
                "account": ACCOUNT,
                "password": PASSWORD,
                "validateCode": "",
                "captchaToken": captcha_token,
                "returnUrl": "https://cloud.189.cn/",
                "mailSuffix": "@189.cn",
                "dynamicCheck": "false",
                "clientType": "5",
                "cb": "callback",
            }, timeout=15)
            print(f"第二次响应: {r.text[:500]}")
            data = r.json()
            print(f"result={data.get('result')}, msg={data.get('msg', '')}")
            if data.get("result") == 0:
                redirect = data.get("redirectUrl", "")
                if redirect:
                    s.get(redirect, allow_redirects=True, timeout=15)
        
        # 3. 测试API
        print("\n测试云盘API...")
        r = s.get("https://cloud.189.cn/api/portal/folder/listFiles.action",
                  params={"categoryId": "0", "folderId": "root", "pageSize": "10"}, timeout=10)
        try:
            api_data = r.json()
            print(f"API result: {api_data.get('result')}, msg: {api_data.get('errorMsg', api_data.get('msg', ''))}")
            if api_data.get("result") == 0:
                files = api_data.get("data", {}).get("fileInfos", {}).get("list", [])
                print(f"文件数: {len(files)}")
                for f in files[:5]:
                    print(f"  - {f.get('fileName', '?')} ({f.get('fileSize', '?')})")
        except:
            print(f"非JSON: {r.text[:200]}")
        
        # 4. 保存cookie
        print(f"\n保存 {len(s.cookies)} 个cookie...")
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain or "", "path": c.path or "/", "secure": c.secure} for c in s.cookies]
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
        with open(COOKIE_FILE, "w") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f"已保存到 {COOKIE_FILE}")
        for c in s.cookies:
            d = c.domain or ""
            if "189" in d or "21cn" in d:
                v = c.value[:50] + "..." if len(c.value) > 50 else c.value
                print(f"  {c.name} = {v} (domain={d})")
    
    except Exception as e:
        print(f"解析失败: {e}")
        print(f"原始响应: {r.text[:300]}")

if __name__ == "__main__":
    login()
