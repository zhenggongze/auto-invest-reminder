#!/usr/bin/env python3
"""
合并定投提醒脚本（515450红利低波50ETF + 纳斯达克100指数 + PE/PB估值）
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

ETF_CODE = "sh515450"
ETF_NAME = "515450红利低波50ETF"
ETF_MA_DAYS = 250
ETF_PRICE_UNIT = "元"

NASDAQ_CODE = ".NDX"
NASDAQ_NAME = "纳斯达克100指数"
NASDAQ_MA_DAYS = 200
NASDAQ_PRICE_UNIT = "点"
NASDAQ_YEAR_DAYS = 252

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
REQUEST_TIMEOUT = 10

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
BEIJING_TZ = timezone(timedelta(hours=8))


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

def fetch_etf_data(code):
    import akshare as ak
    df = ak.fund_etf_hist_sina(symbol=code)
    return df


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

def build_message(etf_result, nasdaq_result, logger):
    etf_date = _safe_get(etf_result, "analysis_date", "") if etf_result else ""
    nasdaq_date = _safe_get(nasdaq_result, "analysis_date", "") if nasdaq_result else ""
    title_date = etf_date or nasdaq_date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    lines = []
    lines.append(f"【Trae】定投偏离度 - {title_date}")
    lines.append("")

    lines.append(_build_etf_section(etf_result))
    lines.append("---")
    lines.append(_build_nasdaq_section(nasdaq_result))

    return "\n".join(lines)


def _safe_get(d, key, default="N/A"):
    if d is None:
        return default
    return d.get(key, default)


def _build_etf_section(result):
    lines = []
    lines.append("### 515450红利低波50ETF")

    if result is None:
        lines.append("")
        lines.append("- 状态：数据获取失败")
        lines.append("- 建议：持有不动")
        return "\n".join(lines)

    price = result.get("current_price", "N/A")
    ma_val = result.get("ma_value", "N/A")
    dev = result.get("deviation", 0)
    unit = result.get("price_unit", "元")
    status, advice = result.get("status", ""), result.get("advice", "")

    lines.append("")
    lines.append(f"- 当前价格：{price}{unit}")
    lines.append(f"- 250日均线：{ma_val}{unit}")
    lines.append(f"- 偏离度：{dev:+.1f}%" if isinstance(dev, (int, float)) else f"- 偏离度：{dev}")
    lines.append(f"- 状态：{status}")
    lines.append(f"- 建议：{advice}")
    lines.append("")
    lines.append("> 偏离度≤6%可以100%胜率allin持有60天胜率收益6%，出现日期2022年10-11月、2023年12月-2024年1月、2024年9月，以及最近的 2026年6月底-7月初")

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

def write_status_file(date_str, etf_success, nasdaq_success, push_success,
                      errors, summary, logger):
    status = {
        "任务名称": "合并定投提醒（515450 ETF + 纳斯达克100指数 + PE/PB）",
        "执行日期": date_str,
        "是否成功": etf_success and nasdaq_success and push_success,
        "摘要信息": summary,
        "错误信息": errors if errors else None,
        "时间戳": datetime.now(BEIJING_TZ).isoformat(),
    }
    status_file = os.path.join(LOGS_DIR, f"combined_{date_str}_status.json")
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    logger.info(f"状态文件已保存: {status_file}")


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

    errors = []
    etf_result = None
    nasdaq_result = None
    etf_success = False
    nasdaq_success = False
    push_success = False

    try:
        # --- 获取 515450 ETF 数据 ---
        logger.info("--- 获取 515450 ETF 数据 ---")
        df_etf, err = fetch_with_retry(fetch_etf_data, ETF_CODE, ETF_NAME, logger)
        if df_etf is not None:
            try:
                metrics = calc_metrics(df_etf, ETF_MA_DAYS, ETF_PRICE_UNIT, False, logger)
                status, advice = judge_status(metrics["should_invest"], metrics["deviation"])
                metrics["status"] = status
                metrics["advice"] = advice
                etf_result = metrics
                etf_success = True
                logger.info(f"{ETF_NAME} 分析完成: {status}")
            except Exception as e:
                logger.error(f"{ETF_NAME} 指标计算失败: {e}")
                errors.append(f"{ETF_NAME}: {e}")
        else:
            logger.error(f"{ETF_NAME} 数据获取失败: {err}")
            errors.append(f"{ETF_NAME}: {err}")

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
        message = build_message(etf_result, nasdaq_result, logger)
        logger.info("推送消息已构建")
        logger.debug(f"消息内容:\n{message}")

        # --- 写入日志备用渠道 ---
        _write_log_backup(etf_result, nasdaq_result, logger)

        # --- PushDeer 推送 ---
        push_success, push_resp = send_pushdeer(message, logger)

        if not push_success:
            errors.append(f"PushDeer推送失败: {push_resp}")
            logger.warning("PushDeer 推送失败，已通过日志备用渠道保存完整分析结果")

    except Exception as e:
        logger.error(f"主流程异常: {e}", exc_info=True)
        errors.append(f"主流程异常: {e}")

    # --- 写入状态文件 ---
    summary_parts = []
    summary_parts.append(f"ETF: {'成功' if etf_success else '失败'}")
    summary_parts.append(f"纳斯达克: {'成功' if nasdaq_success else '失败'}")
    summary_parts.append(f"推送: {'成功' if push_success else '失败'}")
    summary = " | ".join(summary_parts)

    try:
        write_status_file(date_str, etf_success, nasdaq_success,
                         push_success, errors, summary, logger)
    except Exception as e:
        logger.error(f"状态文件写入失败: {e}")

    logger.info("=" * 50)
    final_status = "成功" if (etf_success and nasdaq_success and push_success) else "部分失败"
    logger.info(f"脚本执行完成 - {final_status}")
    logger.info(f"摘要: {summary}")
    logger.info("=" * 50)

    return 0 if (etf_success and nasdaq_success and push_success) else 1


def _write_log_backup(etf_result, nasdaq_result, logger):
    try:
        log_file = os.path.join(LOGS_DIR, "combined_notifications.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"日志备用渠道 - {datetime.now(BEIJING_TZ).isoformat()}\n")
            f.write("=" * 60 + "\n")

            if etf_result:
                f.write(f"[515450ETF] 日期={_safe_get(etf_result, 'analysis_date')}, ")
                f.write(f"价格={_safe_get(etf_result, 'current_price')}{ETF_PRICE_UNIT}, ")
                f.write(f"均线={_safe_get(etf_result, 'ma_value')}{ETF_PRICE_UNIT}, ")
                f.write(f"偏离度={_safe_get(etf_result, 'deviation')}%, ")
                f.write(f"状态={_safe_get(etf_result, 'status')}, ")
                f.write(f"建议={_safe_get(etf_result, 'advice')}\n")
            else:
                f.write("[515450ETF] 数据获取失败\n")

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
