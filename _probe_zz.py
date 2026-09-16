# -*- coding: utf-8 -*-
"""探测 000922 中证红利指数 的行情源可用性（本地 + GitHub Actions 通用）

需要确认两件事：
  1) 盘中（14:40）能否拿到 000922 的实时价
  2) 日线源 stock_zh_index_daily_tx 盘中是否已包含当日 K 线（决定 MA250 怎么算）
不涉及任何密钥。
"""
import sys
import time
import json
from datetime import datetime

import requests

RESULT = {}


def t(name, fn):
    t0 = time.time()
    try:
        v = fn()
        ok = v is not None
        RESULT[name] = {"ok": ok, "elapsed": round(time.time() - t0, 2), "value": v}
        print(f"[{'OK ' if ok else 'FAIL'}] {name}  ({round(time.time()-t0,2)}s)  -> {v}")
    except Exception as e:
        RESULT[name] = {"ok": False, "elapsed": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}
        print(f"[FAIL] {name}  ({round(time.time()-t0,2)}s)  -> {type(e).__name__}: {e}")


def tencent_rt(code="sh000922"):
    """腾讯实时行情（直接 HTTP，不走 akshare）"""
    r = requests.get(f"http://qt.gtimg.cn/q={code}", timeout=10,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    r.encoding = "gbk"
    txt = r.text.strip()
    if "=" not in txt or len(txt) < 30:
        return None
    payload = txt.split("=", 1)[1].strip().strip('";')
    f = payload.split("~")
    return {"code": code, "字段数": len(f), "名称": f[1] if len(f) > 1 else None,
            "当前价": f[3] if len(f) > 3 else None, "昨收": f[4] if len(f) > 4 else None,
            "时间": f[30] if len(f) > 30 else None}


def sina_rt(code="sh000922"):
    """新浪实时行情（需要 Referer）"""
    r = requests.get(f"https://hq.sinajs.cn/list={code}", timeout=10,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    r.encoding = "gbk"
    txt = r.text.strip()
    if '="' not in txt:
        return None
    payload = txt.split('="', 1)[1].strip().strip('";')
    if not payload:
        return None
    f = payload.split(",")
    return {"code": code, "字段数": len(f), "名称": f[0], "今开": f[1], "昨收": f[2],
            "当前价": f[3], "日期": f[-3] if len(f) >= 3 else None, "时间": f[-2] if len(f) >= 2 else None}


def ak_sina_spot():
    import akshare as ak
    df = ak.stock_zh_index_spot_sina()
    hit = df[df["代码"].astype(str).str.contains("000922")]
    return {"总行数": len(df), "列": list(df.columns), "命中000922": hit.to_dict("records")[:1]}


def ak_em_spot():
    import akshare as ak
    try:
        df = ak.stock_zh_index_spot_em(symbol="中证系列指数")
    except TypeError:
        df = ak.stock_zh_index_spot_em()
    hit = df[df["代码"].astype(str).str.contains("000922")]
    return {"总行数": len(df), "命中000922": hit.to_dict("records")[:1]}


def ak_tx_daily():
    import akshare as ak
    df = ak.stock_zh_index_daily_tx(symbol="sh000922")
    tail = df.tail(3)[["date", "close"]].astype(str).to_dict("records")
    return {"总行数": len(df), "列": list(df.columns), "最后3行": tail}


def ak_sina_daily():
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol="sh000922")
    return {"总行数": len(df), "最后3行": df.tail(3).astype(str).to_dict("records")}


def ak_etf_rt():
    """515080 ETF 实时价（下单要用）"""
    import akshare as ak
    try:
        df = ak.fund_etf_spot_em()
        hit = df[df["代码"].astype(str) == "515080"]
        return {"总行数": len(df), "命中515080": hit.to_dict("records")[:1]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


print("=" * 100)
print(f"探测时间（本机）: {datetime.now().isoformat(timespec='seconds')}")
print("=" * 100)
print("--- A. 000922 实时行情 ---")
t("腾讯实时 qt.gtimg.cn/sh000922", tencent_rt)
t("新浪实时 hq.sinajs.cn/sh000922", sina_rt)
t("akshare stock_zh_index_spot_sina", ak_sina_spot)
t("akshare stock_zh_index_spot_em", ak_em_spot)
print("--- B. 000922 日线源（看盘中是否含当日K线）---")
t("akshare stock_zh_index_daily_tx", ak_tx_daily)
t("akshare stock_zh_index_daily(新浪)", ak_sina_daily)
print("--- C. 515080 ETF 实时价 ---")
t("akshare fund_etf_spot_em", ak_etf_rt)

print("\n" + "=" * 100)
print("汇总:", json.dumps({k: v.get("ok") for k, v in RESULT.items()}, ensure_ascii=False))
print("=" * 100)
