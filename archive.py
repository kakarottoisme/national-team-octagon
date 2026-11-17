"""Archive the current ETF minutes after close. Importing does not fetch or write."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from backend_common import ETF_UNIVERSE, estimated_large_minutes, iso_market_date, parse_minutes
from backend_io import write_json_atomic

ROOT = Path(__file__).resolve().parent
E = list(ETF_UNIVERSE)


def g(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def build_archive(fetch=None):
    """Return (date, summary, minutes) without touching the filesystem."""
    fetch = fetch or g
    minutes, summary, date = {}, {}, None
    for name, code in E:
        raw = fetch(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}")["data"][code]
        parsed = parse_minutes(raw, code)
        date = parsed["date"]
        minutes[code] = {key: parsed[key] for key in ("t", "p", "a", "pre")}
        summary[name] = estimated_large_minutes(parsed["a"])
    iso_date = iso_market_date(date)
    summary["合计"] = round(sum(summary.values()), 2)
    kline = fetch("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000001,day,,,40,qfq")["data"]["sh000001"]
    kline = kline.get("day") or kline.get("qfqday")
    indices = [i for i, row in enumerate(kline) if row[0] == iso_date]
    if indices:
        i = indices[0]
        close, previous = float(kline[i][2]), float(kline[i-1][2])
        summary["上证收盘"] = close
        summary["上证涨跌幅"] = round((close / previous - 1) * 100, 2)
    return iso_date, summary, {"date": iso_date, "etfs": minutes}


def main(data_dir=None, fetch=None):
    date, summary, minutes = build_archive(fetch=fetch)
    directory = Path(data_dir) if data_dir is not None else ROOT / "data"
    history_path = directory / "history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {}
    history[date] = summary
    write_json_atomic(history_path, dict(sorted(history.items())), ensure_ascii=False, indent=1)
    # Keep the historical archive's ASCII JSON encoding and compact separators.
    write_json_atomic(directory / "minutes" / f"{date}.json", minutes, separators=(",", ":"))
    print(date, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
