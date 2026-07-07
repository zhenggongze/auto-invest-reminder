#!/usr/bin/env python3
"""探测 danjuan API 对 NDX 的原始返回结构"""
import json, requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# PE history
try:
    r = requests.get("https://danjuanfunds.com/djapi/index_eva/pe_history/NDX?day=all", timeout=15, headers=HEADERS)
    data = r.json()
    print("=== PE history ===")
    print("status:", r.status_code)
    print("keys:", list(data.keys()))
    if "data" in data and isinstance(data["data"], dict):
        print("data keys:", list(data["data"].keys()))
        for k, v in data["data"].items():
            if isinstance(v, list):
                print(f"data.{k}: list len={len(v)}")
                if v:
                    print("  sample:", json.dumps(v[0], ensure_ascii=False)[:300])
                    print("  last:", json.dumps(v[-1], ensure_ascii=False)[:300])
            else:
                print(f"data.{k}: {str(v)[:200]}")
    else:
        print("raw data:", json.dumps(data.get("data"), ensure_ascii=False)[:500])
except Exception as e:
    print("PE ERROR:", e)

print()
print("=" * 50)
print()

# PB history
try:
    r2 = requests.get("https://danjuanfunds.com/djapi/index_eva/pb_history/NDX?day=all", timeout=15, headers=HEADERS)
    data2 = r2.json()
    print("=== PB history ===")
    print("status:", r2.status_code)
    print("keys:", list(data2.keys()))
    if "data" in data2 and isinstance(data2["data"], dict):
        print("data keys:", list(data2["data"].keys()))
        for k, v in data2["data"].items():
            if isinstance(v, list):
                print(f"data.{k}: list len={len(v)}")
                if v:
                    print("  sample:", json.dumps(v[0], ensure_ascii=False)[:300])
                    print("  last:", json.dumps(v[-1], ensure_ascii=False)[:300])
            else:
                print(f"data.{k}: {str(v)[:200]}")
    else:
        print("raw data:", json.dumps(data2.get("data"), ensure_ascii=False)[:500])
except Exception as e:
    print("PB ERROR:", e)
