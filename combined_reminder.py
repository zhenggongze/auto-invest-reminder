#!/usr/bin/env python3
"""
中证红利偏离度策略提醒脚本
  信号源：000922 中证红利指数（腾讯源日线 + 腾讯实时价）
  实盘标的：515080 中证红利ETF
  策略：偏离度首次 ≤ -7% 满仓买入 → 持有到偏离度首次 ≥ 0% 全部卖出（不看持有时长）
  附：纳斯达克100指数 + PE/PB 估值
推送渠道：PushDeer（markdown格式）

每轮对话强制流程：
  Skill检查 → 编码 → 语法验证 → 导入验证 → 环境自检
"""

import pkgutil
if not hasattr(pkgutil, 'ImpImporter'):
    pkgutil.ImpImporter = pkgutil.zipimporter

import requests
import pandas as pd
import numpy as np
import logging
import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

# ============================================================
# 配置常量
# ============================================================

PUSHDEER_KEY = os.environ.get("PUSHDEER_KEY", "")
PUSHDEER_URL = "https://api2.pushdeer.com/message/push"

# --- 中证红利策略（信号源 = 000922 指数，实盘标的 = 515080 ETF）---
ZZ_INDEX_CODE = "sh000922"          # 000922 中证红利指数（信号源）
ZZ_INDEX_NAME = "中证红利指数(000922)"
ZZ_ETF_CODE = "sh515080"            # 515080 中证红利ETF（实盘下单标的）
ZZ_ETF_NAME = "515080中证红利ETF"
ZZ_MA_DAYS = 250                    # 250 日均线
ZZ_BUY_THRESHOLD = -7.0             # 买入阈值：偏离度首次 ≤ -7%
ZZ_SELL_THRESHOLD = 0.0             # 卖出阈值：偏离度首次 ≥ 0%
ZZ_PRICE_UNIT = "点"
ZZ_ETF_PRICE_UNIT = "元"
# 腾讯行情（云端实测：000922 实时唯一可用源，日线用腾讯 stock_zh_index_daily_tx）
TX_QUOTE_BASE = "http://qt.gtimg.cn/q="
TX_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
              "Referer": "https://gu.qq.com/"}

# --- 持仓状态存放：GitHub Repository Variable（仓库公开，状态不入库）---
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "zhenggongze/auto-invest-reminder")
GH_VAR_NAME = "ZZ_POSITION"
GH_PAT = os.environ.get("GH_PAT", "")   # 由 workflow 通过 Secrets 注入

# 空状态模板
EMPTY_POSITION = {
    "状态": "空仓",          # 空仓 / 持仓
    "待确认": False,         # 是否有待次日复核的信号
    "信号类型": None,        # 买入 / 卖出
    "信号日": None,          # 信号触发日 YYYY-MM-DD
    "信号日盘中偏离度": None,
    "买入日": None,          # 已确认的买入日
    "买入价": None,          # 已确认的买入价（指数点位）
    "买入偏离度": None,
    "更新于": None,
}

NASDAQ_CODE = ".NDX"
NASDAQ_NAME = "纳斯达克100指数"
NASDAQ_MA_DAYS = 200
NASDAQ_PRICE_UNIT = "点"
NASDAQ_YEAR_DAYS = 252

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
REQUEST_TIMEOUT = 10

# DRY_RUN=1 时：不推送、不写变量，只打印（本地测试用）
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
BEIJING_TZ = timezone(timedelta(hours=8))

# 推送时间窗（北京时间）：仅 14:00 之后允许推送。
# 背景：cron-job.org 曾存在两个调度任务（上午 ~10:30 旧任务 + 14:40 新任务）。
#       旧任务若未彻底删除，会在上午抢先推送。此处用时间窗兜底，
#       确保只有下午 14:40 的正确调度才会真正发出通知，拦截上午的旧任务。
PUSH_WINDOW_START_HOUR = 14


# ============================================================
# 日志配置
# ============================================================

def setup_logging():
    os.makedirs(LOGS_DIR, exist_ok=True)

    logger = logging.getLogger("combined_reminder")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    log_file = os.path.join(LOGS_DIR, "combined_notifications.log")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ============================================================
# 通用重试函数
# ============================================================

def fetch_with_retry(fetch_func, code, name, logger):
    for attempt in range(MAX_RETRIES):
        try:
            df = fetch_func(code)
            if df is None or df.empty:
                raise ValueError(f"{name} 返回了空数据")
            logger.info(f"{name} 数据获取成功，共 {len(df)} 条记录（第 {attempt + 1} 次尝试）")
            return df, None
        except Exception as e:
            logger.warning(f"{name} 第 {attempt + 1} 次获取失败: {e}")
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.info(f"{name} 将在 {delay} 秒后重试...")
                time.sleep(delay)
    return None, f"获取失败，已重试 {MAX_RETRIES} 次"


# ============================================================
# 数据获取
# ============================================================

def fetch_zz_daily(code):
    """000922 日线（腾讯源）。云端实测：东财断连、新浪停在 2019，只有腾讯可用。"""
    import akshare as ak
    df = ak.stock_zh_index_daily_tx(symbol=code)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def fetch_tx_quote(code, logger=None):
    """腾讯实时行情（000922 指数 / 515080 ETF 同一个接口）。

    云端实测结论（2026-09-16 GitHub Actions）：
      ✅ qt.gtimg.cn          可用（0.78s）
      ❌ stock_zh_index_spot_em 东财断连
      ❌ stock_zh_index_spot_sina 不含 000922
      ❌ hq.sinajs.cn         返回全 0
      ⚠️ fund_etf_spot_em     可用但耗时 36s，弃用
    返回字段取自 `~` 分隔的固定位置。
    """
    resp = requests.get(TX_QUOTE_BASE + code, timeout=REQUEST_TIMEOUT, headers=TX_HEADERS)
    resp.encoding = "gbk"
    txt = resp.text.strip()
    if "=" not in txt:
        raise ValueError(f"腾讯行情格式异常: {txt[:80]}")
    fields = txt.split("=", 1)[1].strip().strip('";').split("~")
    if len(fields) < 35:
        raise ValueError(f"腾讯行情字段不足({len(fields)})，代码可能无效: {code}")
    quote = {
        "name": fields[1],
        "price": float(fields[3]),
        "prev_close": float(fields[4]) if fields[4] else 0.0,
        "ts": fields[30],              # 形如 20260916161448
        "day": fields[30][:8],         # YYYYMMDD
    }
    if logger:
        logger.debug(f"腾讯行情 {code}: {quote}")
    return quote


def fetch_nasdaq_data(code):
    import akshare as ak
    df = ak.index_us_stock_sina(symbol=code)
    return df


# ============================================================
# 指标计算模块
# ============================================================

def calc_metrics(df, ma_days, price_unit, is_index, logger):
    if df is None or df.empty:
        raise ValueError("数据为空，无法计算指标")

    df_sorted = df.sort_values("date").reset_index(drop=True)
    if len(df_sorted) < 60:
        raise ValueError(f"数据不足60天（仅有 {len(df_sorted)} 天），无法计算可靠均线")

    latest = df_sorted.iloc[-1]
    current_price = float(latest["close"])
    analysis_date = str(latest.get("date", "未知"))

    actual_ma_days = min(ma_days, len(df_sorted))
    closes = df_sorted["close"].astype(float).tail(actual_ma_days)
    ma_value = float(np.mean(closes))

    deviation = (current_price - ma_value) / ma_value * 100

    should_invest = current_price < ma_value

    result = {
        "analysis_date": analysis_date,
        "current_price": round(current_price, 2),
        "ma_days": actual_ma_days,
        "ma_value": round(ma_value, 2),
        "deviation": round(deviation, 2),
        "should_invest": should_invest,
        "price_unit": price_unit,
    }

    if is_index:
        recent_year = df_sorted.tail(NASDAQ_YEAR_DAYS)
        high_1y = float(recent_year["close"].max())
        drawdown = (current_price - high_1y) / high_1y * 100
        result["high_1y"] = round(high_1y, 2)
        result["drawdown"] = round(drawdown, 2)

    logger.info(
        f"指标计算完成: 当前价格={current_price:.2f}{price_unit}, "
        f"{actual_ma_days}日均线={ma_value:.2f}{price_unit}, "
        f"偏离度={deviation:+.2f}%"
    )
    return result


def calc_zz_metrics(df_daily, quote, logger):
    """中证红利偏离度：用「历史收盘序列（剔除当日）+ 当日实时价」计算。

    日线源盘中一般不含当日 K 线、收盘后才含。这里统一把「日期 == 当日」那根丢掉
    再追加实时价，两种情况都收敛到同一口径：
        MA250 = 最近 249 个交易日收盘价 + 当日实时价 的算术平均
    """
    if df_daily is None or df_daily.empty:
        raise ValueError("中证红利日线数据为空")

    today = pd.to_datetime(quote["day"], format="%Y%m%d")
    hist = df_daily[df_daily["date"] < today]
    closes = hist["close"].astype(float).tolist()
    need = ZZ_MA_DAYS - 1
    if len(closes) < need:
        raise ValueError(f"历史数据不足：仅 {len(closes)} 根，需要 {need} 根")

    window = closes[-need:] + [quote["price"]]
    ma_value = float(np.mean(window))
    deviation = (quote["price"] - ma_value) / ma_value * 100

    result = {
        "analysis_date": str(today.date()),
        "current_price": round(quote["price"], 2),
        "ma_days": ZZ_MA_DAYS,
        "ma_value": round(ma_value, 2),
        "deviation": round(deviation, 2),
        "price_unit": ZZ_PRICE_UNIT,
        "quote_ts": quote["ts"],
        "daily_last_date": str(df_daily["date"].iloc[-1].date()),
        "daily_last_close": round(float(df_daily["close"].iloc[-1]), 2),
    }
    logger.info(
        f"中证红利指标: 实时价={quote['price']:.2f} 偏离度={deviation:+.2f}% "
        f"| MA250={ma_value:.2f} | 腾讯时间={quote['ts']} | 日线最后一根={result['daily_last_date']}"
    )
    return result


def zz_close_deviation_on_date(df_daily, target_date):
    """算某个历史交易日的「真实收盘偏离度」（用于次日复核信号真伪）。

    返回 (收盘价, 偏离度)，数据缺失时返回 (None, None)。
    """
    d = pd.to_datetime(target_date)
    idx = df_daily.index[df_daily["date"] == d]
    if len(idx) == 0:
        return None, None
    i = int(idx[0])
    if i + 1 < ZZ_MA_DAYS:
        return None, None
    close = float(df_daily["close"].values[i])
    ma = float(np.mean(df_daily["close"].astype(float).values[i + 1 - ZZ_MA_DAYS:i + 1]))
    return close, (close - ma) / ma * 100


# ============================================================
# 持仓状态（GitHub Repository Variable，仓库公开故不入库）
# ============================================================

def _gh_headers():
    return {"Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + GH_PAT,
            "User-Agent": "combined-reminder"}


def read_position(logger):
    """读取持仓状态；不存在或失败时返回空状态模板。"""
    fallback = dict(EMPTY_POSITION)
    if not GH_PAT:
        logger.warning("未配置 GH_PAT，无法读持仓状态（按空仓处理）")
        return fallback
    url = f"https://api.github.com/repos/{GH_REPO}/actions/variables/{GH_VAR_NAME}"
    try:
        resp = requests.get(url, headers=_gh_headers(), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            logger.info(f"持仓变量 {GH_VAR_NAME} 不存在，按空仓初始化")
            return fallback
        resp.raise_for_status()
        pos = json.loads(resp.json()["value"])
        merged = dict(EMPTY_POSITION)
        merged.update(pos)
        logger.info(f"持仓状态: {merged['状态']} | 待确认={merged['待确认']} | 买入日={merged['买入日']}")
        return merged
    except Exception as e:
        logger.error(f"读取持仓状态失败（按空仓处理）: {e}")
        return fallback


def write_position(pos, logger):
    if DRY_RUN:
        logger.info(f"[DRY_RUN] 跳过写持仓变量，将写入: {json.dumps(pos, ensure_ascii=False)}")
        return True
    if not GH_PAT:
        logger.error("未配置 GH_PAT，无法写持仓状态")
        return False
    pos = dict(pos)
    pos["更新于"] = datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
    url = f"https://api.github.com/repos/{GH_REPO}/actions/variables"
    name_url = f"{url}/{GH_VAR_NAME}"
    body = {"name": GH_VAR_NAME, "value": json.dumps(pos, ensure_ascii=False)}
    try:
        resp = requests.get(name_url, headers=_gh_headers(), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            r2 = requests.patch(name_url, headers=_gh_headers(), json=body, timeout=REQUEST_TIMEOUT)
        else:
            r2 = requests.post(url, headers=_gh_headers(), json=body, timeout=REQUEST_TIMEOUT)
        if r2.status_code in (201, 204):
            logger.info(f"持仓状态已更新: {json.dumps(pos, ensure_ascii=False)}")
            return True
        logger.error(f"写持仓状态失败: HTTP {r2.status_code} {r2.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"写持仓状态异常: {e}")
        return False


def judge_zz_signal(deviation, pos):
    """信号判定：空仓且偏离度 ≤ -7% → 买入；持仓且偏离度 ≥ 0% → 卖出；否则无信号。"""
    if pos["状态"] == "持仓":
        if deviation >= ZZ_SELL_THRESHOLD:
            return "卖出"
        return None
    if deviation <= ZZ_BUY_THRESHOLD:
        return "买入"
    return None


def review_pending_signal(pos, df_daily, logger):
    """复核上一次的信号：用「信号日的真实收盘偏离度」判定信号真伪。

    返回 (复核结果文本, pos 是否被改动)
    """
    if not pos.get("待确认") or not pos.get("信号日"):
        return None, False

    real_close, real_dev = zz_close_deviation_on_date(df_daily, pos["信号日"])
    if real_dev is None:
        logger.info(f"日线尚无 {pos['信号日']} 的数据（同日重复运行或数据未更新），保留待确认状态")
        return None, False

    kind, sig_day = pos["信号类型"], pos["信号日"]
    if kind == "买入":
        if real_dev <= ZZ_BUY_THRESHOLD:
            pos["状态"] = "持仓"
            pos["买入日"] = sig_day
            pos["买入价"] = round(real_close, 2)
            pos["买入偏离度"] = round(real_dev, 2)
            text = (f"✅ 昨日({sig_day})买入信号复核通过：当日实际收盘偏离度 "
                    f"{real_dev:+.2f}%（≤ {ZZ_BUY_THRESHOLD:.0f}%），已登记为持仓，"
                    f"买入价按收盘 {real_close:.2f} 点")
        else:
            pos["状态"] = "空仓"
            text = (f"⚠️ 昨日({sig_day})买入信号作废：盘中触发但收盘偏离度 "
                    f"{real_dev:+.2f}%（未达 {ZZ_BUY_THRESHOLD:.0f}%），视为闪断，未建仓")
    else:
        if real_dev >= ZZ_SELL_THRESHOLD:
            pos["状态"] = "空仓"
            text = (f"✅ 昨日({sig_day})卖出信号复核通过：当日实际收盘偏离度 "
                    f"{real_dev:+.2f}%（≥ {ZZ_SELL_THRESHOLD:.0f}%），已清仓，等下一次 ≤"
                    f"{ZZ_BUY_THRESHOLD:.0f}%")
            pos["买入日"] = pos["买入价"] = pos["买入偏离度"] = None
        else:
            pos["状态"] = "持仓"
            text = (f"⚠️ 昨日({sig_day})卖出信号作废：盘中触发但收盘偏离度 "
                    f"{real_dev:+.2f}%（未达 {ZZ_SELL_THRESHOLD:.0f}%），继续持有")

    pos["待确认"] = False
    pos["信号类型"] = None
    pos["信号日"] = None
    pos["信号日盘中偏离度"] = None
    return text, True


# ============================================================
# 状态判断模块
# ============================================================

def judge_status(should_invest, deviation):
    abs_dev = abs(deviation)
    if should_invest:
        if abs_dev > 2:
            status = "价格显著低于均线，定投良机"
            advice = "定投买入"
        elif abs_dev > 1:
            status = "价格低于均线，可考虑定投"
            advice = "定投买入"
        else:
            status = "价格略低于均线，正常区间"
            advice = "可小额定投"
    else:
        if abs_dev > 2:
            status = "价格显著高于均线，考虑减仓"
            advice = "持有不动"
        elif abs_dev > 1:
            status = "价格高于均线，正常区间"
            advice = "持有不动"
        else:
            status = "价格接近均线，正常区间"
            advice = "持有不动"

    return status, advice


# ============================================================
# 纳斯达克100 PE/PB 估值获取
# ============================================================

def get_nasdaq_valuation(logger):
    try:
        from nasdaq_valuation_fetcher import get_nasdaq_valuation as gnv
        logger.info("nasdaq_valuation_fetcher 模块已加载，尝试获取估值数据...")
        result = gnv()
        if result:
            return {
                "pe": result.get("pe"),
                "pe_pct": result.get("pe_pct"),
                "pb": result.get("pb"),
                "pb_pct": result.get("pb_pct"),
                "rating": result.get("rating"),
            }
        logger.warning("nasdaq_valuation_fetcher 返回空数据，尝试蛋卷基金API...")
    except ImportError:
        logger.info("nasdaq_valuation_fetcher 模块不可用，尝试蛋卷基金API...")
    except Exception as e:
        logger.warning(f"nasdaq_valuation_fetcher 调用异常: {e}，尝试蛋卷基金API...")

    return _fetch_nasdaq_pe_pb_from_danjuan(logger)


def _fetch_nasdaq_pe_pb_from_danjuan(logger):
    try:
        pe_history, current_pe = _get_danjuan_history("pe", "NDX", logger)
        pb_history, current_pb = _get_danjuan_history("pb", "NDX", logger)

        if pe_history is None and pb_history is None:
            return None

        pe_pct_val = _calc_percentile_from_history(pe_history, current_pe) if pe_history else None
        pb_pct_val = _calc_percentile_from_history(pb_history, current_pb) if pb_history else None
        rating = _calc_rating(pe_pct_val) if pe_pct_val is not None else "未知"

        return {
            "pe": round(current_pe, 2) if current_pe else None,
            "pe_pct": pe_pct_val,
            "pb": round(current_pb, 2) if current_pb else None,
            "pb_pct": pb_pct_val,
            "rating": rating,
        }
    except Exception as e:
        logger.warning(f"蛋卷基金API获取NAS100估值失败: {e}")
        return None


def _get_danjuan_history(data_type, dj_code, logger):
    field_map = {"pe": "index_eva_pe_growths", "pb": "index_eva_pb_growths"}
    url = f"https://danjuanfunds.com/djapi/index_eva/{data_type}_history/{dj_code}?day=all"
    try:
        logger.debug(f"请求蛋卷API: {url}")
        resp = requests.get(
            url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get(field_map[data_type], [])
        history = []
        for item in items:
            ts = item.get("ts", 0)
            val = item.get(data_type, 0)
            if ts and val:
                history.append({"ts": ts, "value": val})
        history.sort(key=lambda x: x["ts"])
        current_val = history[-1]["value"] if history else None
        logger.info(f"蛋卷 NDX {data_type.upper()} 获取成功, {len(history)} 条记录, 当前值={current_val}")
        return history, current_val
    except Exception as e:
        logger.warning(f"蛋卷 NDX {data_type.upper()} 获取失败: {e}")
        return None, None


def _calc_percentile_from_history(history, current_value):
    if not history or current_value is None:
        return None
    lower_count = sum(1 for item in history if item["value"] < current_value)
    return round((lower_count / len(history)) * 100, 2)


def _calc_rating(pe_pct):
    if pe_pct is None:
        return "未知"
    if pe_pct > 90:
        return "极度高估"
    elif pe_pct >= 70:
        return "高估"
    elif pe_pct >= 30:
        return "合理"
    elif pe_pct >= 10:
        return "低估"
    else:
        return "极度低估"


# ============================================================
# PushDeer 消息构造
# ============================================================

def build_message(zz_result, nasdaq_result, pos, signal, review_text, logger):
    zz_date = _safe_get(zz_result, "analysis_date", "") if zz_result else ""
    nasdaq_date = _safe_get(nasdaq_result, "analysis_date", "") if nasdaq_result else ""
    title_date = zz_date or nasdaq_date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    lines = []
    lines.append(f"定投偏离度 - {title_date}")
    lines.append("")

    lines.append(_build_zz_section(zz_result, pos, signal, review_text))
    lines.append("---")
    lines.append(_build_nasdaq_section(nasdaq_result))

    return "\n".join(lines)


def _safe_get(d, key, default="N/A"):
    if d is None:
        return default
    return d.get(key, default)


def _build_zz_section(result, pos, signal, review_text):
    lines = []
    lines.append(f"### {ZZ_INDEX_NAME}")
    lines.append("")
    lines.append(f"> 策略：偏离度首次 ≤{ZZ_BUY_THRESHOLD:.0f}% 满仓买入 {ZZ_ETF_NAME}，"
                 f"持有到偏离度首次 ≥{ZZ_SELL_THRESHOLD:.0f}% 全部卖出（不看持有时长）")
    lines.append("")

    if review_text:
        lines.append(review_text)
        lines.append("")

    if result is None:
        lines.append("- 状态：数据获取失败")
        return "\n".join(lines)

    if result.get("holiday"):
        lines.append(f"- 今日休市（腾讯行情无更新，最后一根为 {result.get('daily_last_date')}）")
        lines.append("")
        lines.append("**状态：休市，不判定信号**")
        return "\n".join(lines)

    price = result["current_price"]
    dev = result["deviation"]
    holding = pos["状态"] == "持仓"

    lines.append(f"- 指数现价：{price}{ZZ_PRICE_UNIT}")
    lines.append(f"- 250日均线：{result['ma_value']}{ZZ_PRICE_UNIT}")
    lines.append(f"- **偏离度：{dev:+.2f}%**")

    if holding and pos.get("买入价"):
        cost = pos["买入价"]
        lines.append(f"- 持仓：{pos['买入日']} 买入 @{cost}{ZZ_PRICE_UNIT}，"
                     f"当前浮动 **{(price / cost - 1) * 100:+.2f}%**（按指数点位估算）")

    lines.append("")

    if signal == "买入":
        lines.append("## 🔔 买入信号")
        lines.append(f"偏离度 {dev:+.2f}% 首次 ≤ {ZZ_BUY_THRESHOLD:.0f}%")
        lines.append(f"**今日尾盘（14:40~15:00）满仓买入 {ZZ_ETF_NAME}**")
        lines.append("")
        lines.append("> 明日会用真实收盘价复核；若尾盘拉回则该信号作废")
    elif signal == "卖出":
        lines.append("## 🔔 卖出信号")
        lines.append(f"偏离度 {dev:+.2f}% 首次 ≥ {ZZ_SELL_THRESHOLD:.0f}%")
        lines.append(f"**今日尾盘（14:40~15:00）全部卖出 {ZZ_ETF_NAME}**")
        lines.append("")
        lines.append("> 明日会用真实收盘价复核；若尾盘回落则该信号作废")
    elif holding:
        lines.append(f"**状态：持仓中，等卖出信号**（离卖点还差 "
                     f"{ZZ_SELL_THRESHOLD - dev:.2f} 个百分点）")
    else:
        lines.append(f"**状态：空仓等信号**（离买点还需再跌 "
                     f"{abs(ZZ_BUY_THRESHOLD - dev):.2f} 个百分点）")

    lines.append("")
    lines.append("> 近10年回测（000922，买-7%/卖0%）：10笔 / 胜率90% / 每笔平均+8.02% / "
                 "平均持有41个交易日 / 累计+114.98%")
    return "\n".join(lines)


def _build_nasdaq_section(result):
    lines = []
    lines.append("### 纳斯达克100指数")

    if result is None:
        lines.append("")
        lines.append("- 状态：数据获取失败")
        lines.append("- 建议：持有不动")
        return "\n".join(lines)

    price = result.get("current_price", "N/A")
    ma_val = result.get("ma_value", "N/A")
    dev = result.get("deviation", 0)
    high_1y = result.get("high_1y", "N/A")
    drawdown = result.get("drawdown", "N/A")
    status, advice = result.get("status", ""), result.get("advice", "")

    lines.append("")
    lines.append(f"- 当前价格：{price}点")
    lines.append(f"- 200日均线：{ma_val}点")

    if isinstance(dev, (int, float)):
        lines.append(f"- 偏离度：{dev:+.1f}%")
    else:
        lines.append(f"- 偏离度：{dev}")

    if high_1y != "N/A":
        lines.append(f"- 近1年最高：{high_1y}点")
    if drawdown != "N/A" and isinstance(drawdown, (int, float)):
        lines.append(f"- 回撤幅度：{drawdown:.1f}%")
    else:
        lines.append(f"- 回撤幅度：{drawdown}")

    pe_data = result.get("pe_data")
    if pe_data:
        pe_val = _safe_get(pe_data, "pe")
        pe_pct = _safe_get(pe_data, "pe_pct")
        pb_val = _safe_get(pe_data, "pb")
        pb_pct = _safe_get(pe_data, "pb_pct")
        lines.append(f"- PE：{pe_val}（近10年 {pe_pct}% 百分位）")
        lines.append(f"- PB：{pb_val}（近10年 {pb_pct}% 百分位）")
    else:
        lines.append("- PE/PB：暂无估值数据")

    lines.append(f"- 状态：{status}")
    lines.append(f"- 建议：{advice}")
    lines.append("")
    lines.append("> 回撤：每年都会有15%~20%的回撤，重点关注")
    lines.append("> Allin：偏离度<-10% allin，持有2年最少赚+27.6%，持有4年最少+117%")
    lines.append(">")
    lines.append("> 定投：")
    lines.append(">")
    lines.append("> +15% → 100元/天")
    lines.append(">")
    lines.append("> +5% ~ +15% → 150元/天")
    lines.append(">")
    lines.append("> 0% ~ +5% → 300元/天")
    lines.append(">")
    lines.append("> -5% ~ 0% → 1500元/天")
    lines.append(">")
    lines.append("> -10% ~ -5% → 3000元/天")
    lines.append(">")
    lines.append("> < -10% → 4000元/天 + 梭哈")

    return "\n".join(lines)


# ============================================================
# PushDeer 发送
# ============================================================

def send_pushdeer(full_text, logger):
    payload = {
        "pushkey": PUSHDEER_KEY,
        "text": full_text,
        "type": "markdown",
    }
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"PushDeer 推送中...（第 {attempt + 1} 次尝试）")
            resp = requests.post(PUSHDEER_URL, data=payload, timeout=REQUEST_TIMEOUT)
            result = resp.json()
            success = (result.get("code") == 0 or
                       result.get("content", {}).get("result") == "success")
            if success:
                logger.info("PushDeer 推送成功")
                return True, result
            else:
                logger.warning(f"PushDeer 返回失败: {result}")
        except requests.exceptions.Timeout:
            logger.warning(f"PushDeer 请求超时（{REQUEST_TIMEOUT}s）")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"PushDeer 连接错误: {e}")
        except Exception as e:
            logger.warning(f"PushDeer 请求异常: {e}")

        if attempt < MAX_RETRIES - 1:
            delay = RETRY_DELAYS[attempt]
            logger.info(f"PushDeer 将在 {delay} 秒后重试...")
            time.sleep(delay)

    return False, "推送失败，已达最大重试次数"


# ============================================================
# 状态文件写入
# ============================================================

def write_status_file(date_str, zz_success, nasdaq_success, push_success,
                      errors, summary, logger):
    status = {
        "任务名称": "中证红利偏离度策略提醒（000922 信号 / 515080 实盘 + 纳斯达克100指数）",
        "执行日期": date_str,
        "是否成功": zz_success and nasdaq_success and push_success,
        "摘要信息": summary,
        "错误信息": errors if errors else None,
        "时间戳": datetime.now(BEIJING_TZ).isoformat(),
    }
    status_file = os.path.join(LOGS_DIR, f"combined_{date_str}_status.json")
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    logger.info(f"状态文件已保存: {status_file}")


def has_successful_push_today(date_str, logger):
    """检查当天是否已成功推送过（幂等保护：防止多个调度源同一天重复推送）。

    依据：状态文件 combined_{date_str}_status.json 中"是否成功"为 True。
    状态文件随 GitHub Actions 的"提交日志"步骤入库，下一次运行时能读到。
    """
    status_file = os.path.join(LOGS_DIR, f"combined_{date_str}_status.json")
    if not os.path.exists(status_file):
        return False
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            status = json.load(f)
        return bool(status.get("是否成功", False))
    except Exception as e:
        logger.warning(f"读取状态文件失败（按未推送处理）: {e}")
        return False


def is_in_push_window(beijing_now, logger):
    """推送时间窗校验：仅北京时间 14:00 之后允许推送。

    拦截 cron-job.org 上午 ~10:30 的旧调度任务，确保只有下午 14:40
    的正确调度才会真正发出通知。
    """
    if beijing_now.hour >= PUSH_WINDOW_START_HOUR:
        return True
    logger.warning(
        f"当前北京时间 {beijing_now.strftime('%H:%M')} 早于 "
        f"{PUSH_WINDOW_START_HOUR:02d}:00，属于非推送时段（拦截上午旧任务），跳过推送"
    )
    return False


def run_zz_strategy(pos, beijing_now, logger, errors):
    """执行中证红利策略：取数 → 复核昨日信号 → 判定今日信号 → 落库。

    返回 (成功标志, 日线df, 指标dict, 今日信号, 复核文本, 515080实时行情)
    """
    today_str = beijing_now.strftime("%Y-%m-%d")

    df_zz, err = fetch_with_retry(fetch_zz_daily, ZZ_INDEX_CODE, ZZ_INDEX_NAME, logger)
    if df_zz is None:
        errors.append(f"{ZZ_INDEX_NAME}: {err}")
        return False, None, None, None, None, None

    quote = fetch_tx_quote(ZZ_INDEX_CODE, logger)
    etf_quote = None
    try:
        etf_quote = fetch_tx_quote(ZZ_ETF_CODE, logger)
    except Exception as e:
        logger.warning(f"{ZZ_ETF_NAME} 实时行情获取失败（不影响信号判定）: {e}")

    # 行情日期必须是今天，否则视为休市（避免用陈旧实时价误判信号）
    if quote["day"] != beijing_now.strftime("%Y%m%d"):
        logger.warning(f"腾讯行情日期 {quote['day']} != 今天 {beijing_now.strftime('%Y%m%d')}，"
                       f"判定休市，不判定信号、不改持仓状态")
        stub = {"analysis_date": today_str, "holiday": True,
                "daily_last_date": str(df_zz["date"].iloc[-1].date()), "quote_ts": quote["ts"]}
        return True, df_zz, stub, None, None, etf_quote

    try:
        zz_result = calc_zz_metrics(df_zz, quote, logger)
    except Exception as e:
        logger.error(f"{ZZ_INDEX_NAME} 指标计算失败: {e}")
        errors.append(f"{ZZ_INDEX_NAME}: {e}")
        return False, df_zz, None, None, None, etf_quote

    # 1) 先复核上一次的信号（用信号日的真实收盘偏离度判定真伪）
    review_text, _ = review_pending_signal(pos, df_zz, logger)

    # 2) 再判定今天的信号
    signal = judge_zz_signal(zz_result["deviation"], pos)
    if signal:
        pos["待确认"] = True
        pos["信号类型"] = signal
        pos["信号日"] = today_str
        pos["信号日盘中偏离度"] = zz_result["deviation"]

    # 3) 落库（持仓状态是策略的唯一真相，先写库再推送，避免状态与推送脱节）
    write_position(pos, logger)
    return True, df_zz, zz_result, signal, review_text, etf_quote


# ============================================================
# 主流程
# ============================================================

def main():
    logger = setup_logging()
    beijing_now = datetime.now(BEIJING_TZ)
    date_str = beijing_now.strftime("%Y%m%d")

    logger.info("=" * 50)
    logger.info(f"合并定投提醒脚本启动 - {date_str}")
    logger.info("=" * 50)

    # --- 推送时间窗校验：仅 14:00 后（北京时间）推送，拦截上午旧任务 ---
    if not is_in_push_window(beijing_now, logger):
        logger.info("=" * 50)
        logger.info(f"脚本执行完成 - 非推送时段（北京时间 {beijing_now.strftime('%H:%M')} 早于 14:00），跳过推送")
        logger.info("=" * 50)
        return 0

    errors = []
    zz_result = None
    nasdaq_result = None
    signal = None
    review_text = None
    zz_success = False
    nasdaq_success = False
    push_success = False
    skip_reason = None

    try:
        # --- 中证红利策略：读持仓 → 取数 → 复核昨日信号 → 判定今日信号 ---
        logger.info("--- 中证红利策略（000922 信号 / 515080 实盘）---")
        pos = read_position(logger)
        try:
            zz_success, _, zz_result, signal, review_text, _ = \
                run_zz_strategy(pos, beijing_now, logger, errors)
            if signal:
                logger.info(f"今日信号: {signal}")
            elif review_text:
                logger.info(f"信号复核: {review_text}")
            else:
                logger.info(f"今日无信号（状态={pos['状态']}）")
        except Exception as e:
            logger.error(f"{ZZ_INDEX_NAME} 策略执行失败: {e}", exc_info=True)
            errors.append(f"{ZZ_INDEX_NAME}: {e}")

        # --- 获取纳斯达克100指数数据 ---
        logger.info("--- 获取纳斯达克100指数数据 ---")
        df_nasdaq, err = fetch_with_retry(fetch_nasdaq_data, NASDAQ_CODE, NASDAQ_NAME, logger)
        if df_nasdaq is not None:
            try:
                metrics = calc_metrics(df_nasdaq, NASDAQ_MA_DAYS, NASDAQ_PRICE_UNIT, True, logger)
                status, advice = judge_status(metrics["should_invest"], metrics["deviation"])
                metrics["status"] = status
                metrics["advice"] = advice

                pe_data = get_nasdaq_valuation(logger)
                metrics["pe_data"] = pe_data

                nasdaq_result = metrics
                nasdaq_success = True
                logger.info(f"{NASDAQ_NAME} 分析完成: {status}")
            except Exception as e:
                logger.error(f"{NASDAQ_NAME} 指标计算失败: {e}")
                errors.append(f"{NASDAQ_NAME}: {e}")
        else:
            logger.error(f"{NASDAQ_NAME} 数据获取失败: {err}")
            errors.append(f"{NASDAQ_NAME}: {err}")

        # --- 构建消息 ---
        message = build_message(zz_result, nasdaq_result, pos, signal, review_text, logger)
        logger.info("推送消息已构建")
        logger.debug(f"消息内容:\n{message}")

        # --- 写入日志备用渠道 ---
        _write_log_backup(zz_result, nasdaq_result, pos, logger)

        # --- 幂等保护：当日已成功推送过则跳过（防止多个调度源重复推送） ---
        # 说明：数据源返回最近收盘日数据，盘中无当日K线，
        #       因此不校验"数据日期==当天"，只保证每天最多推送一次。
        if has_successful_push_today(date_str, logger):
            skip_reason = f"今日({date_str})已成功推送过，跳过重复推送（幂等保护）"
            logger.warning(skip_reason)
        elif DRY_RUN:
            skip_reason = "[DRY_RUN] 跳过真实推送"
            logger.info(skip_reason)
        else:
            # --- PushDeer 推送 ---
            push_success, push_resp = send_pushdeer(message, logger)

            if not push_success:
                errors.append(f"PushDeer推送失败: {push_resp}")
                logger.warning("PushDeer 推送失败，已通过日志备用渠道保存完整分析结果")

    except Exception as e:
        logger.error(f"主流程异常: {e}", exc_info=True)
        errors.append(f"主流程异常: {e}")

    if skip_reason:
        # 跳过推送（当日已推送过 或 DRY_RUN）：记录状态文件，优雅退出不算失败
        try:
            write_status_file(date_str, zz_success, nasdaq_success,
                              push_success, errors, f"跳过推送: {skip_reason}", logger)
        except Exception as e:
            logger.error(f"状态文件写入失败: {e}")
        logger.info("=" * 50)
        logger.info(f"脚本执行完成 - 跳过推送（原因: {skip_reason}）")
        logger.info("=" * 50)
        return 0

    # --- 写入状态文件 ---
    summary_parts = []
    summary_parts.append(f"中证红利: {'成功' if zz_success else '失败'}")
    summary_parts.append(f"纳斯达克: {'成功' if nasdaq_success else '失败'}")
    summary_parts.append(f"推送: {'成功' if push_success else '失败'}")
    summary = " | ".join(summary_parts)

    try:
        write_status_file(date_str, zz_success, nasdaq_success,
                         push_success, errors, summary, logger)
    except Exception as e:
        logger.error(f"状态文件写入失败: {e}")

    logger.info("=" * 50)
    final_status = "成功" if (zz_success and nasdaq_success and push_success) else "部分失败"
    logger.info(f"脚本执行完成 - {final_status}")
    logger.info(f"摘要: {summary}")
    logger.info("=" * 50)

    return 0 if (zz_success and nasdaq_success and push_success) else 1


def _write_log_backup(zz_result, nasdaq_result, pos, logger):
    try:
        log_file = os.path.join(LOGS_DIR, "combined_notifications.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"日志备用渠道 - {datetime.now(BEIJING_TZ).isoformat()}\n")
            f.write("=" * 60 + "\n")

            f.write(f"[持仓状态] {json.dumps(pos, ensure_ascii=False)}\n")

            if zz_result:
                if zz_result.get("holiday"):
                    f.write(f"[中证红利] 今日休市，最后一根={_safe_get(zz_result, 'daily_last_date')}\n")
                else:
                    f.write(f"[中证红利] 日期={_safe_get(zz_result, 'analysis_date')}, ")
                    f.write(f"现价={_safe_get(zz_result, 'current_price')}{ZZ_PRICE_UNIT}, ")
                    f.write(f"MA250={_safe_get(zz_result, 'ma_value')}{ZZ_PRICE_UNIT}, ")
                    f.write(f"偏离度={_safe_get(zz_result, 'deviation')}%, ")
                    f.write(f"腾讯时间={_safe_get(zz_result, 'quote_ts')}, ")
                    f.write(f"日线最后一根={_safe_get(zz_result, 'daily_last_date')}\n")
            else:
                f.write("[中证红利] 数据获取失败\n")

            if nasdaq_result:
                f.write(f"[纳斯达克100] 日期={_safe_get(nasdaq_result, 'analysis_date')}, ")
                f.write(f"价格={_safe_get(nasdaq_result, 'current_price')}{NASDAQ_PRICE_UNIT}, ")
                f.write(f"均线={_safe_get(nasdaq_result, 'ma_value')}{NASDAQ_PRICE_UNIT}, ")
                f.write(f"偏离度={_safe_get(nasdaq_result, 'deviation')}%, ")
                f.write(f"近1年最高={_safe_get(nasdaq_result, 'high_1y')}{NASDAQ_PRICE_UNIT}, ")
                f.write(f"回撤={_safe_get(nasdaq_result, 'drawdown')}%, ")
                pe_data = nasdaq_result.get("pe_data")
                if pe_data:
                    f.write(f"PE={_safe_get(pe_data, 'pe')}({_safe_get(pe_data, 'pe_pct')}%), ")
                    f.write(f"PB={_safe_get(pe_data, 'pb')}({_safe_get(pe_data, 'pb_pct')}%), ")
                    f.write(f"评级={_safe_get(pe_data, 'rating')}, ")
                f.write(f"状态={_safe_get(nasdaq_result, 'status')}, ")
                f.write(f"建议={_safe_get(nasdaq_result, 'advice')}\n")
            else:
                f.write("[纳斯达克100] 数据获取失败\n")

            f.write("\n")
        logger.info("日志备用渠道已记录")
    except Exception as e:
        logger.error(f"日志备用渠道写入失败: {e}")


if __name__ == "__main__":
    sys.exit(main())
