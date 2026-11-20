"""Backfill exact index minute endpoints into existing ETF archives.

Display-only enrichment. Does not reconstruct ETF data, interpolate, rewrite
summaries or read any account/strategy file. The CLI stages all source responses
and candidates first, and refuses to apply unless every requested day/index has
one complete source series.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import datetime as dt
import json
import math
from pathlib import Path
import re
import urllib.request

from backend_io import write_json_atomic
from index_quotes import INDEX_UNIVERSE

ROOT = Path(__file__).resolve().parent


def canonical_date(value):
    """Accept native YYYYMMDD or archive ISO date; validate calendar syntax."""
    raw = str(value)
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, pattern).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError("Invalid source date")


def expected_times(interval):
    """Completed bars: morning (09:30,11:30], afternoon (13:00,15:00]."""
    if interval not in (1, 5):
        raise ValueError("Only exact 1/5-minute endpoints are supported")
    points = []
    for start, end in ((570, 690), (780, 900)):
        for minute in range(start + interval, end + 1, interval):
            points.append(f"{minute // 60:02d}{minute % 60:02d}")
    return points


def normalize_index_series(series, date):
    """Validate one dated series; never invent missing minutes."""
    if not isinstance(series, dict) or canonical_date(str(series.get("date", ""))) != canonical_date(str(date)):
        raise ValueError("Index date mismatch")
    ticks, prices = series.get("t", []), series.get("p", [])
    if not ticks or len(ticks) != len(prices):
        raise ValueError("Empty or misaligned index series")
    out_t, out_p = [], []
    for tick, price in zip(ticks, prices):
        tick = re.sub(r"\D", "", str(tick))
        if len(tick) != 4 or int(tick[:2]) > 23 or int(tick[2:]) > 59:
            raise ValueError("Invalid index time")
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid index price")
        if out_t and tick <= out_t[-1]:
            raise ValueError("Unordered or duplicate index minute")
        out_t.append(tick)
        out_p.append(price)
    out = copy.deepcopy(series)
    out.update(date=canonical_date(str(date)), t=out_t, p=out_p)
    return out


def is_complete(series, interval):
    try:
        valid = normalize_index_series(series, series["date"])
        return set(expected_times(interval)).issubset(valid["t"])
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def sina_url(code, interval):
    count = 1970 if interval == 1 else 1023
    return ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/"
            "CN_MarketDataService.getKLineData?symbol=" + code
            + f"&scale={interval}&ma=no&datalen={count}")


def parse_sina_history(text, interval, source_url, collected_at):
    """Return complete per-day close endpoints only; reject ambiguous duplicates."""
    start, end = text.find("("), text.rfind(")")
    rows = json.loads(text[start + 1:end] if start >= 0 else text)
    if not isinstance(rows, list) or not rows:
        raise ValueError("Source returned no index bars")
    days = {}
    for row in rows:
        stamp = dt.datetime.strptime(row["day"], "%Y-%m-%d %H:%M:%S")
        if stamp.second:
            raise ValueError("Non-minute endpoint")
        day, tick = stamp.strftime("%Y-%m-%d"), stamp.strftime("%H%M")
        if tick not in expected_times(interval):
            continue
        price = float(row["close"])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid historical close")
        values = days.setdefault(day, {})
        if tick in values and values[tick] != price:
            raise ValueError("Conflicting source rows")
        values[tick] = price
    result = {}
    for day, values in days.items():
        if sorted(values) != expected_times(interval):
            continue
        result[day] = {
            "date": day, "t": sorted(values),
            "p": [values[tick] for tick in sorted(values)],
            "interval_minutes": interval, "price_type": "bar_close",
            "source": "sina_kline", "source_url": source_url,
            "collected_at": collected_at, "interpolated": False,
        }
    return result


def merge_indices(archive, additions):
    """Merge display-only rows; keep existing complete higher-resolution records."""
    day = canonical_date(str(archive["date"]))
    result = copy.deepcopy(archive)
    indices = copy.deepcopy(result.get("index_minutes") or {})
    for code, series in additions.items():
        validated = normalize_index_series(series, day)
        grain = int(validated.get("interval_minutes", 1))
        if not is_complete(validated, grain):
            raise ValueError("Incomplete candidate index series")
        previous = indices.get(code)
        if previous:
            previous_grain = int(previous.get("interval_minutes", 1))
            if canonical_date(str(previous.get("date", ""))) == day and is_complete(previous, previous_grain) and previous_grain <= grain:
                continue
        indices[code] = validated
    result["index_minutes"] = indices
    return result


def backfill(data_dir, staging_dir, apply=False):
    data_dir, staging_dir = Path(data_dir), Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    history = json.loads((data_dir / "history.json").read_text(encoding="utf-8"))
    dates = sorted(history)
    if not dates:
        raise ValueError("No archived trading dates")
    archives = {}
    for day in dates:
        path = data_dir / "minutes" / f"{day}.json"
        if not path.is_file():
            raise ValueError("ETF minute archive missing: " + day)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if canonical_date(str(payload.get("date", ""))) != day:
            raise ValueError("Archive date differs from filename: " + day)
        archives[day] = payload
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    jobs = [(code, interval) for _, code in INDEX_UNIVERSE for interval in (1, 5)]

    def get(job):
        code, interval = job
        url = sina_url(code, interval)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        with urllib.request.urlopen(request, timeout=20) as response:
            text = response.read().decode("utf-8")
        (staging_dir / f"{code}-{interval}min-source.jsonp").write_text(text, encoding="utf-8")
        return code, interval, parse_sina_history(text, interval, url, stamp)

    sources = {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        for code, interval, rows in pool.map(get, jobs):
            sources[code, interval] = rows
    candidates, entries, missing = {}, [], []
    for day, archive in archives.items():
        additions = {}
        for name, code in INDEX_UNIVERSE:
            series = sources[code, 1].get(day) or sources[code, 5].get(day)
            if series is None:
                missing.append({"date": day, "code": code})
                continue
            additions[code] = series
        if len(additions) != len(INDEX_UNIVERSE):
            continue
        candidate = merge_indices(archive, additions)
        for name, code in INDEX_UNIVERSE:
            series = candidate["index_minutes"][code]
            entries.append({"date": day, "code": code, "name": name,
                            "interval_minutes": series.get("interval_minutes", 1),
                            "points": len(series["t"]), "source_url": series.get("source_url")})
        candidates[day] = candidate
        write_json_atomic(staging_dir / f"{day}.json", candidate, separators=(",", ":"))
    report = {"window": [dates[0], dates[-1]], "archive_days": len(dates),
              "covered_days": len(candidates), "index_count": len(INDEX_UNIVERSE),
              "entries": entries, "missing": missing, "applied": False}
    write_json_atomic(staging_dir / "report.json", report, ensure_ascii=False, indent=2)
    if missing:
        raise ValueError("Not all archived index series could be recovered; no production file written")
    if apply:
        for day, payload in candidates.items():
            if payload.get("etfs") != archives[day].get("etfs"):
                raise ValueError("ETF archive must not change")
            write_json_atomic(data_dir / "minutes" / f"{day}.json", payload, separators=(",", ":"))
        report["applied"] = True
        write_json_atomic(staging_dir / "report.json", report, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = backfill(args.data_dir, args.staging_dir, apply=args.apply)
    print(json.dumps({key: result[key] for key in ("window", "archive_days", "covered_days", "index_count", "missing", "applied")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
