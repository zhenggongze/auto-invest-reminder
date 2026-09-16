"""临时探测：中证红利指数 腾讯源 在 GitHub Actions 可用性"""
import akshare as ak
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 1. akshare 腾讯源
try:
    df = ak.stock_zh_index_daily_tx(symbol="sh000922")
    print(f"[akshare tx sh000922] OK rows={len(df)} last={df.tail(1).to_dict('records')}")
except Exception as e:
    print(f"[akshare tx sh000922] FAIL {type(e).__name__}: {str(e)[:150]}")

# 2. 腾讯直连接口
try:
    r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000922,day,,,20,qfq", timeout=15, headers=UA)
    d = r.json().get("data", {}).get("sh000922", {})
    k = d.get("day") or d.get("qfqday") or []
    print(f"[tencent direct] code={r.status_code} rows={len(k)} last={k[-1] if k else None}")
except Exception as e:
    print(f"[tencent direct] FAIL {type(e).__name__}: {str(e)[:150]}")
