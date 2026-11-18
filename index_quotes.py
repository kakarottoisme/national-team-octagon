"""Optional index minute quotes for display only; no strategy inputs or side effects."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import json
import math
import urllib.request

INDEX_UNIVERSE = (
    ("上证50", "sh000016"), ("沪深300", "sh000300"),
    ("中证A500", "sh000510"), ("中证500", "sh000905"),
    ("中证1000", "sh000852"), ("创业板指", "sz399006"),
    ("科创50", "sh000688"),
)


def parse_index_minutes(payload, code, expected_date):
    raw = payload["data"][code]["data"]
    if str(raw.get("date")) != str(expected_date):
        raise ValueError("Index quote date does not match ETF snapshot")
    times, prices = [], []
    for row in raw.get("data", []):
        fields = row.split()
        if len(fields) < 2:
            continue
        price = float(fields[1])
        if not math.isfinite(price):
            continue
        times.append(fields[0])
        prices.append(price)
    return {"date": raw["date"], "t": times, "p": prices}


def fetch_index_minutes(expected_date, fetch=None):
    """Parallel optional source requests. One failure cannot stop ETF publication."""
    def get(code):
        url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + code
        try:
            if fetch is None:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"})
                with urllib.request.urlopen(request, timeout=8) as response:
                    payload = json.loads(response.read().decode("utf-8", "ignore"))
            else:
                payload = fetch(url)
            return code, parse_index_minutes(payload, code, expected_date)
        except Exception:
            return code, None
    with ThreadPoolExecutor(max_workers=7) as pool:
        results = list(pool.map(get, [code for _, code in INDEX_UNIVERSE]))
    return {code: value for code, value in results if value is not None}
