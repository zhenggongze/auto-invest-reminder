#!/usr/bin/env python3
"""调试纳斯达克PE百分位计算逻辑"""
import json, requests, numpy as np

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 1. 获取PE历史
url = "https://danjuanfunds.com/djapi/index_eva/pe_history/NDX?day=all"
r = requests.get(url, timeout=15, headers=HEADERS)
data = r.json()
items = data.get("data", {}).get("index_eva_pe_growths", [])
print("PE items total:", len(items))

# 2. 复现代码的过滤逻辑
history = []
for item in items:
    ts = item.get("ts", 0)
    val = item.get("pe", 0)
    if ts and val:
        history.append({"ts": ts, "value": val})
history.sort(key=lambda x: x["ts"])
print("过滤后 history 数量:", len(history))

current_val = history[-1]["value"] if history else None
print("当前PE:", current_val)

# 3. 检查历史值分布
values = [h["value"] for h in history]
print("min:", min(values), "max:", max(values), "mean:", round(np.mean(values), 2), "median:", round(np.median(values), 2))
print("最新5个:", [round(v, 2) for v in values[-5:]])
print("最早5个:", [round(v, 2) for v in values[:5]])

# 4. 复现百分位计算
lower_count = sum(1 for item in history if item["value"] < current_val)
pct = round((lower_count / len(history)) * 100, 2)
print("当前代码计算的百分位:", pct, "% (", lower_count, "/", len(history), ")")

# 5. numpy标准百分位对比
arr = np.array(values)
for target in [10, 30, 50, 70, 90]:
    print(f"numpy {target}分位:", round(float(np.percentile(arr, target)), 2))

# 6. 打印哪些value>current
above = sum(1 for v in values if v > current_val)
print("高于当前PE的数量:", above, "/", len(values))

# 7. 看看是否有重复ts或异常值
import collections
ts_list = [h["ts"] for h in history]
dups = len(ts_list) - len(set(ts_list))
print("重复ts数:", dups)
