"""临时探测：中证红利在 GitHub Actions（生产环境）可用的数据源"""
import akshare as ak
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def probe(name, fn):
    try:
        df = fn()
        print(f"[{name}] OK rows={len(df)} last={df.tail(1).to_dict('records')}")
    except Exception as e:
        print(f"[{name}] FAIL {type(e).__name__}: {str(e)[:160]}")


print("===== 1. 中证红利指数 000922 =====")
probe("index_zh_a_hist(000922)", lambda: ak.index_zh_a_hist(symbol="000922", period="daily", start_date="20240101", end_date="20261231"))
probe("stock_zh_index_daily_em(sh000922)", lambda: ak.stock_zh_index_daily_em(symbol="sh000922"))
probe("stock_zh_index_daily(sh000922)", lambda: ak.stock_zh_index_daily(symbol="sh000922"))

print("===== 2. 中证红利ETF / 红利低波ETF =====")
probe("fund_etf_hist_sina(sh515080 中证红利ETF)", lambda: ak.fund_etf_hist_sina(symbol="sh515080"))
probe("fund_etf_hist_sina(sh512890 红利低波ETF)", lambda: ak.fund_etf_hist_sina(symbol="sh512890"))

print("===== 3. 蛋卷中证红利估值 =====")
for dt in ["pe", "pb"]:
    try:
        r = requests.get(f"https://danjuanfunds.com/djapi/index_eva/{dt}_history/SH000922?day=all", timeout=15, headers=UA)
        items = r.json().get("data", {}).get(f"index_eva_{dt}_growths", [])
        print(f"[蛋卷 SH000922 {dt}] code={r.status_code} rows={len(items)} last={items[-1] if items else None}")
    except Exception as e:
        print(f"[蛋卷 SH000922 {dt}] FAIL {type(e).__name__}: {str(e)[:130]}")
