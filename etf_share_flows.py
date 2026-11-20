#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽基 ETF 份额与全市场申赎估算。

份额只接受带统计日的官方发布：上交所 query.sse.com.cn 的 STAT_DATE 必须等于
请求日，TOT_VOL 单位为万份。深交所基金列表只有「当前规模」，文件本身不带份额
统计日，只追加未核日期快照，不写入交易日、不参与申赎估算。

估算净流入（亿元）= (当日份额 − 前一官方交易日份额) × 当日成交均价。
成交均价 = 东财日 K 成交额 / (成交量 × 100)。这是全市场申购赎回，不能单独
归因国家队，也不是分钟成交额代理。缺份额、缺前值、缺成交量时结果为 null，
不补、不跨缺口。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import backend_io

ROOT = Path(__file__).resolve().parent
HISTORY_PATH = ROOT / "data" / "etf_share_history.json"
FLOWS_PATH = ROOT / "data" / "etf_share_flows.json"
SZSE_PATH = ROOT / "data" / "etf_szse_snapshot.json"
SCHEMA = "etf-share-flows-1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SSE_URL = ("https://query.sse.com.cn/commonQuery.do?isPagination=true"
           "&pageHelp.pageSize=10000&pageHelp.pageNo=1&pageHelp.beginPage=1"
           "&pageHelp.cacheSize=1&pageHelp.endPage=1"
           "&sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE={date}")
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SZSE_URL = ("https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx"
            "&CATALOGID=1000_lf&TABKEY=tab1&random=0.5")
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")
LARGE_MOVE = Decimal("0.25")

# 第三方观察名单 16 只，另加他们漏掉的 159352。顺序即页面默认顺序。
WATCH = (
    {"code": "510300", "exch": "SH", "name": "沪深300ETF华泰柏瑞", "full": "华泰柏瑞沪深300ETF"},
    {"code": "510310", "exch": "SH", "name": "沪深300ETF易方达", "full": "易方达沪深300ETF"},
    {"code": "510330", "exch": "SH", "name": "沪深300ETF华夏", "full": "华夏沪深300ETF"},
    {"code": "159919", "exch": "SZ", "name": "沪深300ETF嘉实", "full": "嘉实沪深300ETF"},
    {"code": "510050", "exch": "SH", "name": "上证50ETF", "full": "华夏上证50ETF"},
    {"code": "510180", "exch": "SH", "name": "上证180ETF", "full": "华安上证180ETF"},
    {"code": "510500", "exch": "SH", "name": "中证500ETF南方", "full": "南方中证500ETF"},
    {"code": "512500", "exch": "SH", "name": "中证500ETF华夏", "full": "华夏中证500ETF"},
    {"code": "512100", "exch": "SH", "name": "中证1000ETF南方", "full": "南方中证1000ETF"},
    {"code": "159845", "exch": "SZ", "name": "中证1000ETF华夏", "full": "华夏中证1000ETF"},
    {"code": "560010", "exch": "SH", "name": "中证1000ETF广发", "full": "广发中证1000ETF"},
    {"code": "159629", "exch": "SZ", "name": "中证1000ETF富国", "full": "富国中证1000ETF"},
    {"code": "588000", "exch": "SH", "name": "科创50ETF", "full": "华夏上证科创板50ETF"},
    {"code": "588080", "exch": "SH", "name": "科创板50ETF易方达", "full": "易方达上证科创板50ETF"},
    {"code": "159915", "exch": "SZ", "name": "创业板ETF易方达", "full": "易方达创业板ETF"},
    {"code": "159977", "exch": "SZ", "name": "创业板ETF天弘", "full": "天弘创业板ETF"},
    {"code": "159352", "exch": "SZ", "name": "中证A500ETF南方", "full": "南方中证A500ETF"},
)
BY_CODE = {item["code"]: item for item in WATCH}
SH_CODES = tuple(item["code"] for item in WATCH if item["exch"] == "SH")
METHOD = ("估算净流入 =（当日官方份额 − 前一官方交易日份额）× 当日成交均价；"
          "成交均价 = 成交额 / 成交量。这是全市场申购赎回，不能单独归因国家队，"
          "也不是资金观察页的分钟成交额代理。")


def bj_now(now=None):
    if now is not None:
        return now
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def empty_history():
    return {"schema": SCHEMA, "days": {}, "quotes": {}, "blocked_dates": []}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_text(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def write_if_changed(path: Path, payload) -> bool:
    text = dump_text(payload)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    backend_io.write_text_atomic(path, text)
    return True


def http_get(url, headers=None, timeout=25, binary=False):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return response.status, raw if binary else raw.decode("utf-8")


def parse_sse_payload(obj, requested_date: str) -> dict:
    """只要 STAT_DATE 与请求日一致的份额。返回 {code: 万份原文字符串}。"""
    rows = ((obj or {}).get("pageHelp") or {}).get("data") or (obj or {}).get("result") or []
    out = {}
    for row in rows:
        if str(row.get("STAT_DATE") or "").strip() != requested_date:
            continue
        code = str(row.get("SEC_CODE") or "").strip()
        vol = row.get("TOT_VOL")
        if code not in BY_CODE or BY_CODE[code]["exch"] != "SH":
            continue
        if vol in (None, "", "-"):
            continue
        text = str(vol).replace(",", "").strip()
        try:
            Decimal(text)
        except Exception:
            continue
        out[code] = text
    return out


def parse_kline_payload(obj) -> dict:
    """东财日 K。返回 {YYYY-MM-DD: {close, volume_hand, amount_yuan}}，原文字符串。"""
    lines = (((obj or {}).get("data") or {}).get("klines")) or []
    out = {}
    for line in lines:
        parts = str(line).split(",")
        if len(parts) < 7 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
            continue
        close, volume, amount = parts[2], parts[5], parts[6]
        try:
            Decimal(close)
            Decimal(volume)
            Decimal(amount)
        except Exception:
            continue
        out[parts[0]] = {"close": close, "volume_hand": volume, "amount_yuan": amount}
    return out


def _xlsx_cells(row_xml: str) -> list:
    cells = []
    for match in re.finditer(r"<c([^>]*)>(.*?)</c>", row_xml, re.S):
        attrs, body = match.group(1), match.group(2)
        inline = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
        if inline:
            cells.append(re.sub(r"<[^>]+>", "", inline.group(1)))
            continue
        value = re.search(r"<v>(.*?)</v>", body)
        cells.append(value.group(1) if value else "")
    return cells


def parse_szse_xlsx(raw: bytes) -> dict:
    """深交所当前规模。没有份额统计日。返回 {code: {name, scale_shares, nav}}。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        sheet = book.read("xl/worksheets/sheet1.xml").decode("utf-8", "replace")
    rows = re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S)
    if not rows:
        return {}
    header = [cell.strip() for cell in _xlsx_cells(rows[0])]
    def column(*names):
        for name in names:
            for index, title in enumerate(header):
                if name in title:
                    return index
        return None
    code_at = column("基金代码", "代码")
    name_at = column("基金简称", "简称")
    scale_at = column("当前规模")
    nav_at = column("净值")
    if code_at is None or scale_at is None:
        return {}
    out = {}
    for row in rows[1:]:
        cells = _xlsx_cells(row)
        if code_at >= len(cells) or scale_at >= len(cells):
            continue
        code = str(cells[code_at]).strip().split(".")[0]
        if not re.fullmatch(r"\d{6}", code) or code not in BY_CODE or BY_CODE[code]["exch"] != "SZ":
            continue
        scale = str(cells[scale_at]).replace(",", "").strip()
        try:
            Decimal(scale)
        except Exception:
            continue
        nav = None
        if nav_at is not None and nav_at < len(cells) and cells[nav_at] not in ("", "-", None):
            nav_text = str(cells[nav_at]).replace(",", "").strip()
            try:
                Decimal(nav_text)
                nav = nav_text
            except Exception:
                nav = None
        name = cells[name_at].strip() if name_at is not None and name_at < len(cells) else BY_CODE[code]["name"]
        out[code] = {"name": name, "scale_shares": scale, "nav": nav}
    return out


def shares_yi_text(wan: str) -> str:
    """与按万份/10000 再四舍五入到 4 位一致，供和交易所原值对照。"""
    return format(round(float(wan) / 10000.0, 4), ".4f")


def _q(value: Decimal, places: Decimal) -> str:
    return format(value.quantize(places, rounding=ROUND_HALF_UP), "f")


def vwap_of(quote) -> Decimal | None:
    if not quote:
        return None
    volume = Decimal(quote["volume_hand"])
    if volume <= 0:
        return None
    return Decimal(quote["amount_yuan"]) / (volume * Decimal(100))


def inflow_yi(wan_now, wan_prev, quote) -> str | None:
    price = vwap_of(quote)
    if wan_now is None or wan_prev is None or price is None:
        return None
    delta_shares = (Decimal(wan_now) - Decimal(wan_prev)) * Decimal(10000)
    return _q(delta_shares * price / Decimal(100000000), Q4)


def share_delta_yi(wan_now, wan_prev) -> str | None:
    if wan_now is None or wan_prev is None:
        return None
    return _q((Decimal(wan_now) - Decimal(wan_prev)) / Decimal(10000), Q4)


def aum_yi(wan, quote) -> str | None:
    if wan is None or not quote or quote.get("close") in (None, ""):
        return None
    return _q(Decimal(wan) / Decimal(10000) * Decimal(quote["close"]), Q2)


def calendar_dates(history) -> list:
    """官方交易日 = 至少一只沪市标的有同日 STAT_DATE 的日期，加上请求失败日（缺口）。"""
    days = history.get("days") or {}
    confirmed = []
    for day, rows in days.items():
        if any((rows.get(code) or {}).get("stat_date") == day and (rows.get(code) or {}).get("tot_vol_wan")
               for code in SH_CODES):
            confirmed.append(day)
    blocked = [day for day in history.get("blocked_dates") or [] if day not in confirmed]
    return sorted(set(confirmed) | set(blocked))


def wan_on(history, day, code):
    row = ((history.get("days") or {}).get(day) or {}).get(code) or {}
    if row.get("stat_date") != day:
        return None
    return row.get("tot_vol_wan")


def window_bounds(calendar, as_of, n):
    """返回 [起点, ..., as_of] 共 n+1 个官方交易日；不足或 as_of 不在日历则为 None。"""
    if as_of not in calendar:
        return None
    pos = calendar.index(as_of)
    if pos < n:
        return None
    return calendar[pos - n:pos + 1]


def series_complete(history, code, window) -> bool:
    return bool(window) and all(wan_on(history, day, code) is not None for day in window)


def daily_inflow(history, code, day, prev) -> str | None:
    quote = ((history.get("quotes") or {}).get(day) or {}).get(code)
    return inflow_yi(wan_on(history, day, code), wan_on(history, prev, code), quote)


def sum_texts(values) -> str | None:
    if not values or any(item is None for item in values):
        return None
    return _q(sum((Decimal(item) for item in values), Decimal(0)), Q4)


def build_flows(history, szse=None, generated_at=None) -> dict:
    calendar = [day for day in calendar_dates(history) if day not in set(history.get("blocked_dates") or [])]
    # 有缺口的失败日不作为 as_of，但会让跨过它的窗口失去「每日都有值」。
    blocked = set(history.get("blocked_dates") or [])
    full_calendar = calendar_dates(history)
    as_of = calendar[-1] if calendar else None
    etfs = []
    per_day_inflows = {day: [] for day in full_calendar}
    for meta in WATCH:
        code = meta["code"]
        quote = ((history.get("quotes") or {}).get(as_of) or {}).get(code) if as_of else None
        wan = wan_on(history, as_of, code) if as_of else None
        item = {
            "code": code,
            "name": meta["name"],
            "full": meta["full"],
            "exch": meta["exch"],
            "shares_status": "official_confirmed" if wan is not None else (
                "pending_unverifiable" if meta["exch"] == "SZ" else "unpublished"),
            "stat_date": as_of if wan is not None else None,
            "tot_vol_wan": wan,
            "shares_yi": shares_yi_text(wan) if wan is not None else None,
            "close": quote.get("close") if quote else None,
            "vwap": _q(vwap_of(quote), Q4) if vwap_of(quote) is not None else None,
            "aum_yi": aum_yi(wan, quote),
            "share_source": (SSE_URL.format(date=as_of) if meta["exch"] == "SH" and as_of else None),
            "quote_source": KLINE_URL if quote else None,
        }
        deltas = {}
        inflows = {}
        for n, label in ((1, "1d"), (5, "5d"), (20, "20d")):
            window = window_bounds(full_calendar, as_of, n) if as_of else None
            if not series_complete(history, code, window) or any(day in blocked for day in (window or [])):
                deltas[label] = None
                inflows[label] = None
                item[f"coverage_{label}"] = None if meta["exch"] == "SZ" else "窗口缺官方份额或含未判明的请求失败日"
            else:
                deltas[label] = share_delta_yi(wan_on(history, window[-1], code), wan_on(history, window[0], code))
                steps = [daily_inflow(history, code, window[i], window[i - 1]) for i in range(1, len(window))]
                inflows[label] = sum_texts(steps)
                item[f"coverage_{label}"] = None if inflows[label] is not None else "窗口内有日期缺少成交均价"
            base = wan_on(history, window[0], code) if window else None
            now_wan = wan_on(history, window[-1], code) if window else None
            if deltas[label] is not None and base not in (None, "0") and abs((Decimal(now_wan) - Decimal(base)) / Decimal(base)) >= LARGE_MOVE:
                item[f"anomaly_{label}"] = "large_move_unverified"
            else:
                item[f"anomaly_{label}"] = None
        item["delta_1d_yi"] = deltas["1d"]
        item["delta_5d_yi"] = deltas["5d"]
        item["delta_20d_yi"] = deltas["20d"]
        item["inflow_1d_yi"] = inflows["1d"]
        item["inflow_5d_yi"] = inflows["5d"]
        item["inflow_20d_yi"] = inflows["20d"]
        series = []
        for index, day in enumerate(full_calendar):
            value = wan_on(history, day, code)
            series.append(shares_yi_text(value) if value is not None else None)
            if index == 0 or day in blocked:
                per_day_inflows[day].append(None)
            else:
                per_day_inflows[day].append(daily_inflow(history, code, day, full_calendar[index - 1]))
        item["share_series"] = series
        if meta["exch"] == "SZ":
            snap = ((szse or {}).get("latest") or {}).get("rows") or {}
            row = snap.get(code)
            item["undated_snapshot_yi"] = shares_yi_text(str(Decimal(row["scale_shares"]) / Decimal(10000))) if row else None
            item["undated_snapshot_note"] = "深交所当前规模无统计日，不参与申赎估算" if row else None
        etfs.append(item)

    aggregate = []
    covered = []
    cumulative = []
    running = Decimal(0)
    running_known = False
    for day in full_calendar:
        values = per_day_inflows[day]
        known = [item for item in values if item is not None]
        if known:
            total = _q(sum((Decimal(item) for item in known), Decimal(0)), Q4)
            aggregate.append(total)
            covered.append(len(known))
            running += Decimal(total)
            running_known = True
            cumulative.append(_q(running, Q4))
        else:
            aggregate.append(None)
            covered.append(0)
            cumulative.append(None if not running_known else None)

    def summary(field):
        values = [item[field] for item in etfs if item[field] is not None]
        if not values:
            return None, 0
        return _q(sum((Decimal(item) for item in values), Decimal(0)), Q2 if field == "aum_yi" else Q4), len(values)

    inflow_1d, n1 = summary("inflow_1d_yi")
    inflow_5d, n5 = summary("inflow_5d_yi")
    inflow_20d, n20 = summary("inflow_20d_yi")
    aum, na = summary("aum_yi")
    latest_sz = (szse or {}).get("latest")
    return {
        "schema": SCHEMA,
        "method": METHOD,
        "generated_at": generated_at or (history.get("updated_at")),
        "as_of": as_of,
        "as_of_confirmed_by": "sse_stat_date" if as_of else None,
        "backfill_start": full_calendar[0] if full_calendar else None,
        "backfill_end": full_calendar[-1] if full_calendar else None,
        "trading_days": len([day for day in full_calendar if day not in blocked]),
        "dates": full_calendar,
        "summary": {
            "inflow_1d_yi": inflow_1d,
            "inflow_1d_covered": n1,
            "inflow_5d_yi": inflow_5d,
            "inflow_5d_covered": n5,
            "inflow_20d_yi": inflow_20d,
            "inflow_20d_covered": n20,
            "aum_yi": aum,
            "aum_covered": na,
            "universe": len(WATCH),
            "aum_note": "只加总统计日与收盘价同一天的标的。深市未核日期快照不计入。",
        },
        "aggregate": {"inflow_yi": aggregate, "covered": covered, "cumulative_yi": cumulative},
        "etfs": etfs,
        "szse_snapshot": {
            "collected_at": None if not latest_sz else latest_sz.get("collected_at"),
            "date_status": "undated",
            "note": "深交所基金列表的当前规模没有份额统计日。只留快照，不写成某个交易日，也不参与申赎估算。",
            "source": SZSE_URL,
            "rows": {} if not latest_sz else latest_sz.get("rows") or {},
        },
    }


def fetch_sse(date_str, get=http_get):
    status, text = get(SSE_URL.format(date=date_str), headers={"User-Agent": UA, "Referer": "https://www.sse.com.cn/"})
    if status != 200:
        raise RuntimeError(f"SSE HTTP {status}")
    payload = json.loads(text)
    rows = ((payload.get("pageHelp") or {}).get("data")) or []
    return len(rows), parse_sse_payload(payload, date_str)


def fetch_kline(code, beg, end, get=http_get):
    exch = BY_CODE[code]["exch"]
    secid = ("1." if exch == "SH" else "0.") + code
    query = ("?secid={secid}&klt=101&fqt=0&fields1=f1,f2,f3,f4,f5,f6"
             "&fields2=f51,f52,f53,f54,f55,f56,f57&beg={beg}&end={end}").format(
        secid=secid, beg=beg.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    status, text = get(KLINE_URL + query, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    if status != 200:
        raise RuntimeError(f"kline HTTP {status} {code}")
    return parse_kline_payload(json.loads(text))


def fetch_szse(get=http_get):
    status, raw = get(SZSE_URL, headers={"User-Agent": UA, "Referer": "https://fund.szse.cn/"}, binary=True)
    if status != 200:
        raise RuntimeError(f"SZSE HTTP {status}")
    return parse_szse_xlsx(raw)


def merge_sse_day(history, day, shares):
    bucket = history.setdefault("days", {}).setdefault(day, {})
    for code, wan in shares.items():
        bucket[code] = {"tot_vol_wan": wan, "stat_date": day, "source": "sse",
                        "sec_name": BY_CODE[code]["name"]}
    blocked = [item for item in history.get("blocked_dates") or [] if item != day]
    history["blocked_dates"] = blocked


def merge_quotes(history, code, rows):
    quotes = history.setdefault("quotes", {})
    for day, row in rows.items():
        quotes.setdefault(day, {})[code] = {
            "close": row["close"], "volume_hand": row["volume_hand"],
            "amount_yuan": row["amount_yuan"], "source": "eastmoney_push2his",
        }


def record_szse(snapshot_doc, rows, collected_at):
    entry = {"collected_at": collected_at, "date_status": "undated",
             "note": "文件无份额统计日，collected_at 只是抓取时间。", "rows": rows}
    history = snapshot_doc.setdefault("snapshots", [])
    latest = snapshot_doc.get("latest")
    if latest and latest.get("rows") == rows:
        return snapshot_doc, False
    history.append(entry)
    snapshot_doc["schema"] = "etf-szse-snapshot-1"
    snapshot_doc["latest"] = entry
    return snapshot_doc, True


def daterange(beg: dt.date, end: dt.date):
    day = beg
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += dt.timedelta(days=1)


def backfill(beg: dt.date, end: dt.date, get=http_get, sleep=time.sleep, now=None):
    history = load_json(HISTORY_PATH, empty_history())
    for code in BY_CODE:
        merge_quotes(history, code, fetch_kline(code, beg, end, get=get))
        sleep(0.25)
    pending = []
    for day in daterange(beg, end):
        text = day.strftime("%Y-%m-%d")
        existing = history.get("days", {}).get(text) or {}
        if all(existing.get(code, {}).get("stat_date") == text for code in SH_CODES):
            continue
        pending.append(text)

    def pull(text):
        sleep(0.15)
        count, shares = fetch_sse(text, get=get)
        return count, shares

    added, empty, blocked = [], [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {text: pool.submit(pull, text) for text in pending}
    for text, future in futures.items():
        error = future.exception()
        if error is not None:
            history.setdefault("blocked_dates", [])
            if text not in history["blocked_dates"]:
                history["blocked_dates"].append(text)
            blocked.append(text)
            continue
        count, shares = future.result()
        if count == 0:
            empty.append(text)
            history["blocked_dates"] = [item for item in history.get("blocked_dates") or [] if item != text]
        elif shares:
            merge_sse_day(history, text, shares)
            added.append(text)
    stamp = bj_now(now).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    history["updated_at"] = stamp
    history["schema"] = SCHEMA
    szse_doc = load_json(SZSE_PATH, {"schema": "etf-szse-snapshot-1", "snapshots": []})
    szse_error = None
    try:
        rows = fetch_szse(get=get)
        szse_doc, _ = record_szse(szse_doc, rows, stamp)
    except Exception as exc:
        rows = {}
        szse_error = f"{type(exc).__name__}: {exc}"
    flows = build_flows(history, szse_doc, generated_at=stamp)
    changed = write_if_changed(HISTORY_PATH, history)
    changed = write_if_changed(SZSE_PATH, szse_doc) or changed
    changed = write_if_changed(FLOWS_PATH, flows) or changed
    return {"added": added, "empty_days": len(empty), "blocked": blocked,
            "szse_rows": len(rows), "szse_error": szse_error, "as_of": flows.get("as_of"),
            "changed": changed, "trading_days": flows.get("trading_days")}


def update_recent(lookback_days=12, get=http_get, sleep=time.sleep, now=None):
    """补最近若干自然日。已有且一致的日期会再读一次最新日，修订才覆盖。"""
    today = bj_now(now).date()
    history = load_json(HISTORY_PATH, empty_history())
    known = sorted(history.get("days") or {})
    start = today - dt.timedelta(days=lookback_days)
    if known:
        start = min(start, dt.date.fromisoformat(known[-1]))
    quote_start = dt.date.fromisoformat(known[0]) if known else start
    for code in BY_CODE:
        try:
            merge_quotes(history, code, fetch_kline(code, quote_start, today, get=get))
        except Exception:
            pass
        sleep(0.2)
    added = []
    for day in daterange(start, today):
        text = day.strftime("%Y-%m-%d")
        try:
            count, shares = fetch_sse(text, get=get)
        except Exception:
            history.setdefault("blocked_dates", [])
            if text not in history["blocked_dates"]:
                history["blocked_dates"].append(text)
            continue
        if count == 0:
            history["blocked_dates"] = [item for item in history.get("blocked_dates") or [] if item != text]
            continue
        if shares:
            previous = {code: (history.get("days", {}).get(text) or {}).get(code, {}).get("tot_vol_wan")
                        for code in shares}
            if any(previous.get(code) != wan for code, wan in shares.items()):
                merge_sse_day(history, text, shares)
                added.append(text)
            else:
                history["blocked_dates"] = [item for item in history.get("blocked_dates") or [] if item != text]
        sleep(0.3)
    stamp = bj_now(now).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    szse_doc = load_json(SZSE_PATH, {"schema": "etf-szse-snapshot-1", "snapshots": []})
    try:
        szse_doc, szse_changed = record_szse(szse_doc, fetch_szse(get=get), stamp)
    except Exception:
        szse_changed = False
    if not added and not szse_changed and history.get("updated_at"):
        flows = build_flows(history, szse_doc, generated_at=history.get("updated_at"))
        changed = write_if_changed(FLOWS_PATH, flows)
        return {"added": [], "as_of": flows.get("as_of"), "changed": changed}
    history["updated_at"] = stamp
    history["schema"] = SCHEMA
    flows = build_flows(history, szse_doc, generated_at=stamp)
    changed = write_if_changed(HISTORY_PATH, history)
    changed = write_if_changed(SZSE_PATH, szse_doc) or changed
    changed = write_if_changed(FLOWS_PATH, flows) or changed
    return {"added": added, "as_of": flows.get("as_of"), "changed": changed}


def rebuild_only():
    history = load_json(HISTORY_PATH, empty_history())
    szse_doc = load_json(SZSE_PATH, {"schema": "etf-szse-snapshot-1", "snapshots": []})
    flows = build_flows(history, szse_doc, generated_at=history.get("updated_at"))
    changed = write_if_changed(FLOWS_PATH, flows)
    return {"as_of": flows.get("as_of"), "changed": changed, "trading_days": flows.get("trading_days")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="宽基 ETF 份额与全市场申赎估算")
    parser.add_argument("--backfill", nargs=2, metavar=("BEG", "END"), help="YYYY-MM-DD YYYY-MM-DD，只补沪市官方份额")
    parser.add_argument("--rebuild", action="store_true", help="只按已有历史重算，不联网")
    args = parser.parse_args(argv)
    if args.rebuild:
        result = rebuild_only()
    elif args.backfill:
        beg = dt.date.fromisoformat(args.backfill[0])
        end = dt.date.fromisoformat(args.backfill[1])
        result = backfill(beg, end)
    else:
        result = update_recent()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
