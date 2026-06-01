#!/usr/bin/env python3
"""保存迅雷云盘 cookie"""
from pan_login import PanLoginManager

cookie = "sessionid=cs001.0BF0412D5F9D9C1DB0D2A9D4388D4AB3; userid=173001; usernewno=66786688; XLA_CI=6328d6797638d5cc4ebc29228191e4c4; deviceid=wdi10.39eed724816bae147612a2e62175d5d13a3e10845ac3f91556dfd56e6aa2e3ce; xl_fp_rt=1779983066395"

mgr = PanLoginManager()
if mgr.xunlei_set_cookie(cookie):
    print("✅ 迅雷云盘 cookie 已保存")
    print(f"登录状态: {'✅ 已登录' if mgr.xunlei_check() else '❌ 未登录'}")
else:
    print("❌ 保存失败")

print("\n=== 全部网盘状态 ===")
for k, v in mgr.all_status().items():
    s = '✅ 已登录' if v['logged_in'] else '❌ 未登录'
    print(f"  {v['name']}: {s}")
