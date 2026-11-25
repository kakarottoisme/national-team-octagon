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


def add_index_minutes(minutes, directory, fetch=None):
    """One shared merge path for the CLI and scheduled archive entry.

    Closed same-day cached data is preferred; no optional fetch is necessary if
    all seven native minute series were already saved by the live workflow.
    """
    import datetime as dt
    from backfill_index_minutes import canonical_date, is_complete, merge_indices
    from index_quotes import INDEX_UNIVERSE, fetch_index_minutes
    date = canonical_date(minutes['date'])
    directory = Path(directory)
    result = minutes
    for cached in (directory/'minutes'/f'{date}.json', directory/'live.json'):
        try:
            candidate = json.loads(cached.read_text(encoding='utf-8'))
            if canonical_date(candidate.get('date', '')) != date:
                continue
            if cached.name == 'live.json':
                stamp = dt.datetime.fromisoformat(candidate['updated'])
                if stamp.date().isoformat() != date or stamp.time() < dt.time(15):
                    continue
            for code, row in (candidate.get('index_minutes') or {}).items():
                native = dict(row)
                native.setdefault('interval_minutes', 1)
                native.setdefault('source', 'tencent_minutes')
                native.setdefault('source_url', 'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code='+code)
                native.setdefault('price_type', 'minute_quote')
                native.setdefault('interpolated', False)
                result = merge_indices(result, {code:native})
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            continue
    complete = lambda: all(code in result.get('index_minutes', {}) and
                           result['index_minutes'][code].get('interval_minutes', 1) == 1 and
                           is_complete(result['index_minutes'][code], 1)
                           for _, code in INDEX_UNIVERSE)
    if not complete():
        for code, row in fetch_index_minutes(date.replace('-', ''), fetch=fetch).items():
            try:
                native = dict(row)
                native.update(interval_minutes=1, source='tencent_minutes',
                              source_url='https://web.ifzq.gtimg.cn/appstock/app/minute/query?code='+code,
                              price_type='minute_quote', interpolated=False)
                result = merge_indices(result, {code:native})
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
    return result


def validate_complete(summary, minutes, date):
    """Business completeness, not file existence or a successful process exit."""
    import math
    from backfill_index_minutes import canonical_date, is_complete
    from index_quotes import INDEX_UNIVERSE
    if canonical_date(minutes.get('date', '')) != date:
        raise ValueError('八宫格存档日期不匹配')
    required = [f'{m//60:02d}{m%60:02d}' for start,end in ((570,690),(780,900))
                for m in range(start,end+1)]
    if set(minutes.get('etfs', {})) != {code for _,code in E}:
        raise ValueError('七ETF存档不完整')
    calculated = {}
    for name, code in E:
        row = minutes['etfs'][code]
        if (not set(required).issubset(row.get('t', [])) or
                row['t'] != sorted(set(row['t'])) or
                not len(row['t']) == len(row.get('p', [])) == len(row.get('a', []))):
            raise ValueError(code+' 交易分钟不完整或不对齐')
        if 'date' in row and canonical_date(row['date']) != date:
            raise ValueError(code+' 分时日期不匹配')
        for value in [row.get('pre'), *row['p']]:
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(code+' 价格/前日基准无效')
        for value in row['a']:
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
                raise ValueError(code+' 分钟成交额无效')
        calculated[name] = estimated_large_minutes(row['a'])
    calculated['合计'] = round(sum(calculated.values()),2)
    if any(summary.get(key) != value for key,value in calculated.items()):
        raise ValueError('ETF分钟重算摘要不一致')
    if (not isinstance(summary.get('上证收盘'),(int,float)) or summary['上证收盘'] <= 0 or
            not math.isfinite(summary['上证收盘']) or
            not isinstance(summary.get('上证涨跌幅'),(int,float)) or not math.isfinite(summary['上证涨跌幅'])):
        raise ValueError('上证同日收盘或涨跌幅无效')
    for _,code in INDEX_UNIVERSE:
        row = minutes.get('index_minutes', {}).get(code, {})
        if (row.get('interval_minutes',1) != 1 or row.get('interpolated',False) or
                not row or canonical_date(row.get('date','')) != date or not is_complete(row,1)):
            raise ValueError(code+' 同日完整1分钟指数存档缺失')
    return True


def main(data_dir=None, fetch=None, include_indices=True):
    date, summary, minutes = build_archive(fetch=fetch)
    directory = Path(data_dir) if data_dir is not None else ROOT / "data"
    if include_indices:
        minutes = add_index_minutes(minutes, directory, fetch=fetch)
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
