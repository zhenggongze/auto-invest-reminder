#!/usr/bin/env python3
"""抓蛋卷官方NDX百分位 vs 我们的计算"""
import json, requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 1. 官方 dj 接口 - 找到 NDX 的官方 pe_percentile / pe_over_history
url = "https://danjuanfunds.com/djapi/index_eva/dj"
r = requests.get(url, timeout=15, headers=HEADERS)
data = r.json()
items = data.get("data", {}).get("items", [])
print("items总数:", len(items))
ndx = None
for it in items:
    code = it.get("index_code", "")
    name = it.get("name", "")
    if "NDX" in code.upper() or "纳斯达克" in name or "纳指" in name:
        ndx = it
        print("FOUND:", json.dumps(it, ensure_ascii=False, indent=1)[:1000])
if ndx is None:
    # 打印所有含PE的
    print("\n=== 所有item(前20个) ===")
    for it in items[:20]:
        print(f"  {it.get('index_code')} {it.get('name')}: pe={it.get('pe')}, pe_percentile={it.get('pe_percentile')}, pe_over_history={it.get('pe_over_history')}")

# 2. 我们自己的计算（pe_history）
url2 = "https://danjuanfunds.com/djapi/index_eva/pe_history/NDX?day=all"
r2 = requests.get(url2, timeout=15, headers=HEADERS)
data2 = r2.json()
items2 = data2.get("data", {}).get("index_eva_pe_growths", [])
history = []
for item in items2:
    ts = item.get("ts", 0)
    val = item.get("pe", 0)
    if ts and val:
        history.append({"ts": ts, "value": val})
history.sort(key=lambda x: x["ts"])
current_val = history[-1]["value"] if history else None
lower_count = sum(1 for item in history if item["value"] < current_val)
pct = round((lower_count / len(history)) * 100, 2)
print(f"\n=== 我们的计算 ===")
print(f"当前PE: {current_val}, 百分位: {pct}% ({lower_count}/{len(history)})")
