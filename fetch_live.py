"""Fetch seven ETF minute series and SSE daily closes into data/live.json.

Importing this module is safe. Network and writes only happen through main().
The historical script entry command, request policy and JSON contract are kept.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.request
from pathlib import Path

from backend_common import ETF_UNIVERSE, estimated_large_minutes, iso_market_date, parse_minutes, quote_direction
from backend_io import write_json_atomic

ROOT = Path(__file__).resolve().parent
E = list(ETF_UNIVERSE)  # Compatibility alias used by existing callers.


def g(url):
    """Preserve the original three tries / 20s timeout / 1.5,3s backoff."""
    delay = 1.5
    last = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"})
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8", "ignore"))
        except Exception as error:
            last = error
            if attempt == 2:
                break
            time.sleep(delay)
            delay *= 2
    raise last


def build_payload(fetch=None, now=None):
    """Build data without writing. Inject responses/time for deterministic tests."""
    fetch = fetch or g
    etfs, estimates, date, turnover = {}, {}, None, {}
    for name, code in E:
        raw = fetch(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}")["data"][code]
        minutes = parse_minutes(raw, code)
        date = minutes["date"]
        direction = quote_direction(raw, code, minutes)
        etfs[code] = {"t": minutes["t"], "p": minutes["p"], "a": minutes["a"], "date": date,
                      "pre": minutes["pre"], "amt": minutes["amt"], **direction}
        estimates[name] = estimated_large_minutes(minutes["a"])
        turnover[name] = {"code": code, "amt": minutes["amt"], "net_buy": direction["net_buy"], "px": direction["px"]}
    estimates["合计"] = round(sum(estimates.values()), 2)
    try:
        kline = fetch("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000001,day,,,60,qfq")["data"]["sh000001"]
        sse = [row[:6] for row in (kline.get("day") or kline.get("qfqday"))]
    except Exception:
        kline = fetch("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&fields1=f1&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&lmt=60&end=20500101")
        sse = [row.split(",") for row in kline["data"]["klines"]]
    iso_date = iso_market_date(date)
    matching = [i for i, row in enumerate(sse) if row[0] == iso_date]
    if matching and matching[0] > 0:
        i = matching[0]
        close, previous = float(sse[i][2]), float(sse[i-1][2])
        estimates["上证收盘"] = close
        estimates["上证涨跌幅"] = round((close / previous - 1) * 100, 2)
    now = now or datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    net_values = [row["net_buy"] for row in turnover.values() if row["net_buy"] is not None]
    overview = {
        "total_amt_yi": round(sum(row["amt"] for row in turnover.values()), 2),
        "net_buy_yi": round(sum(net_values), 3) if net_values else None,
        "covered": len(net_values), "per": turnover,
        "note": "主动净买入=(外盘-内盘)/(外盘+内盘)×累计成交额，逐笔方向的推算；"
                "不是资金净流入、不是ETF申赎、不是任何机构身份或国家队买入。",
    }
    return {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "date": date, "etfs": etfs,
            "estimates": estimates, "sse": sse, "turnover": overview}


def main(output=None, fetch=None, now=None):
    payload = build_payload(fetch=fetch, now=now)
    write_json_atomic(output or ROOT / "data" / "live.json", payload, ensure_ascii=False, separators=(",", ":"))
    print(payload["updated"], payload["date"], payload["estimates"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
