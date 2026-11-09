#!/usr/bin/env python3
# 计算"同时点对齐"基线：20 个完整交易日里，每个 5 分钟时点已走完全天成交额的平均比例。
# 只写 preview-data/turnover-baseline.json，不碰 data/、正式页、正式脚本。
# 口径来源：新浪 5 分钟 K 线（amount 为该 5 分钟成交额，单位元）。只用作"到此刻为止"的分母，
# 不把半天成交额直接跟全天均额比。
import json
import re
import statistics
import urllib.request
from datetime import datetime, timedelta, timezone

CODES = [
    "sh510050",
    "sh510300",
    "sz159352",
    "sh510500",
    "sh512100",
    "sz159915",
    "sh588000",
]
DAYS = 20          # 参与平均的完整交易日数
MIN_BARS = 44      # 少于这个 5 分钟 bar 数视为不完整，不参与基线
OUT = "preview-data/turnover-baseline.json"

CST = timezone(timedelta(hours=8))


def fetch(url, tries=3):
    delay = 1.5
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": "https://finance.sina.com.cn",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - 单源失败要可降级
            last = exc
            if attempt == tries - 1:
                break
            import time

            time.sleep(delay)
            delay *= 2
    raise last


def bars(code):
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/"
        f"CN_MarketDataService.getKLineData?symbol={code}&scale=5&ma=no&datalen=1023"
    )
    raw = fetch(url)
    match = re.search(r"var x\((\[.*?\])\)", raw, re.S)
    if not match:
        raise ValueError("无法解析新浪 5 分钟 K 线：" + code)
    return json.loads(match.group(1))


def day_key(bar):
    return bar["day"][:10]


def main():
    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    codes = {}
    errors = {}
    window = None
    for code in CODES:
        try:
            rows = bars(code)
        except Exception as exc:  # noqa: BLE001
            errors[code] = str(exc)[:160]
            continue
        groups = {}
        for bar in rows:
            groups.setdefault(day_key(bar), []).append(bar)
        complete = []
        for day in sorted(groups):
            bs = groups[day]
            # 当天未收盘（bar 未走完）不参与基线，避免把半场当全天。
            if day == today and now.hour * 60 + now.minute < 15 * 60 + 5:
                continue
            if len(bs) >= MIN_BARS:
                complete.append((day, bs))
        complete = complete[-DAYS:]
        if len(complete) < 10:
            errors[code] = "完整交易日不足：%d" % len(complete)
            continue
        curves, totals, tails = [], [], []
        for day, bs in complete:
            bs = sorted(bs, key=lambda b: b["day"])
            amts = [float(b["amount"]) for b in bs]
            total = sum(amts)
            if total <= 0:
                continue
            running, curve = 0.0, []
            for amt in amts:
                running += amt
                curve.append(running / total)
            # 补齐/截断到 48 个时点（正常 48；个别日 47 用最后一点补齐）
            if len(curve) < 48:
                curve = curve + [curve[-1]] * (48 - len(curve))
            curves.append(curve[:48])
            totals.append(total)
            tails.append(sum(amts[-6:]) / total)
        if not curves:
            errors[code] = "无有效成交额样本"
            continue
        mean_curve = [round(statistics.fmean(c[i] for c in curves), 5) for i in range(48)]
        # 单调修正：累计占比必须不下降，个别异常 bar 造成的回落抹平
        for i in range(1, 48):
            if mean_curve[i] < mean_curve[i - 1]:
                mean_curve[i] = mean_curve[i - 1]
        codes[code] = {
            "curve": mean_curve,
            "avg_amt_yi": round(statistics.fmean(totals) / 1e8, 3),
            "tail_share": round(statistics.fmean(tails), 4),
            "sample_days": len(curves),
            "first_day": complete[0][0],
            "last_day": complete[-1][0],
        }
        if window is None:
            window = [complete[0][0], complete[-1][0]]

    payload = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "20个完整交易日 5 分钟成交额的累计占比均值（48 个时点）",
        "avg_amt_source": "新浪 5 分钟 K 线 amount 合计",
        "window": window,
        "sample_days": DAYS,
        "codes": codes,
        "errors": errors,
        "note": (
            "分母=20日均额×该时点历史平均进度；分子=当日到此刻的累计成交额。"
            "只用于同时点比较，不能把半天成交额直接除以全天均额下结论。"
        ),
    }
    import os

    os.makedirs("preview-data", exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(
        "baseline",
        payload["generated_at"],
        "window",
        window,
        "codes",
        len(codes),
        "errors",
        list(errors),
    )


if __name__ == "__main__":
    main()
