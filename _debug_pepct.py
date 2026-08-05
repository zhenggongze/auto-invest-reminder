#!/usr/bin/env python3
"""对比蛋卷官方PE百分位 vs 我们的计算"""
import json, requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 1. 完整dump pe_history响应结构
url = "https://danjuanfunds.com/djapi/index_eva/pe_history/NDX?day=all"
r = requests.get(url, timeout=15, headers=HEADERS)
data = r.json()
print("=== pe_history 顶层keys ===")
print(list(data.keys()))
d = data.get("data", {})
print("=== data keys ===")
print(list(d.keys()) if isinstance(d, dict) else type(d))
for k, v in d.items() if isinstance(d, dict) else []:
    if isinstance(v, list):
        print(f"  {k}: list len={len(v)}, first={json.dumps(v[0], ensure_ascii=False)[:150] if v else 'empty'}")
    elif isinstance(v, dict):
        print(f"  {k}: dict keys={list(v.keys())[:10]}")
    else:
        print(f"  {k}: {str(v)[:100]}")

# 2. 尝试官方估值接口
print()
print("=== 尝试官方估值接口 ===")
for ep in [
    "https://danjuanfunds.com/djapi/index_eva/pe/NDX",
    "https://danjuanfunds.com/djapi/index_eva/valuation/NDX",
    "https://danjuanfunds.com/djapi/index_eva/ndx",
    "https://danjuanfunds.com/djapi/index_eva/dj",
]:
    try:
        rr = requests.get(ep, timeout=10, headers=HEADERS)
        print(f"\n{ep}: HTTP {rr.status_code}")
        try:
            j = rr.json()
            print(json.dumps(j, ensure_ascii=False)[:800])
        except:
            print(rr.text[:300])
    except Exception as e:
        print(f"{ep}: ERROR {e}")
