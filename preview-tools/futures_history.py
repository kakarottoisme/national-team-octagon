#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview-tools/futures_history.py — CFFEX 股指期货前20会员(中信期货代客)官方历史回补。

纯 import 无网络; 调用 backfill()/collect_daily() 才取数。
仅处理指定 product(IH/IF/IC/IM 由不同 agent 写各自 shard,本模块不合成四品种)。
日期必须来自 CSV header(交易日列),不按 snapshot 猜。
未上榜≠0: 一边缺榜 long/short=null, status=not_ranked; net_short 保守null。
"""
import csv as _csv
import datetime as dt
import hashlib
import io
import json as _json
import os
import re
import time
import urllib.request

CFFEX_URL = "http://www.cffex.com.cn/sj/ccpm/{ym}/{dd}/{p}_1.csv"
MEMBER = "中信期货"
SCHEMA_VERSION = "futures-history-2.0"
# CFFEX 2026 已知假日(weekday非交易日); 来源 CFFEX 2026 休市安排公告。
# 清明 4-06, 劳动 5-01/04/05, 端午 6-19, 中秋 9-25, 国庆 10-01/02/05/06/07。
KNOWN_HOLIDAYS_2026 = {"2026-04-06", "2026-05-01", "2026-05-04", "2026-05-05",
                       "2026-06-19", "2026-09-25",
                       "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"}
HOLIDAY_EVIDENCE = ("CFFEX 2026 market holiday schedule (清明/劳动/端午/中秋/国庆); "
                    "weekday non-trading = known holiday, not a source fetch failure.")
ART_RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                       ".sessions", "RAW_UNUSED")  # 占位;真实raw由调用方指定


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def http_get(url, timeout=15, retries=2, sleep=0.5):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except Exception as e:  # noqa
            last = e
            time.sleep(sleep)
    raise last


def _member(s):
    return re.split(r"[(（]", str(s))[0].strip()


def parse_cffex_csv(raw_bytes, product, expected_date=None):
    """严格按表头列名定位持买/持卖三列组; 找不到真实列定义 -> None(header_invalid)。
    observed union: 仅保留中信任一侧上榜的合约; 双方缺席进 both_absent_contracts(不当0)。
    多日期混在一文件 -> None(mixed_date); td!=expected_date -> None(date_mismatch)。
    返回 (trading_date_or_None, observed_contracts, both_absent_list, error_code_or_None)。"""
    text = raw_bytes.decode("gbk", errors="replace")
    lines = text.splitlines()
    rows = [[x.strip() for x in ln.split(",")] for ln in lines]
    def find(label):
        for r in rows[:4]:
            if label in r:
                return r.index(label)
        return None
    li, si = find("持买单量"), find("持卖单量")
    ti, ci, ri = find("交易日"), find("合约"), find("排名")
    if li is None or si is None or ti is None:
        return None, [], [], "header_invalid"
    col = {"td": ti, "contract": ci if ci is not None else 1,
           "rank": ri if ri is not None else 2,
           "long_member": li - 1, "long": li, "long_chg": li + 1,
           "short_member": si - 1, "short": si, "short_chg": si + 1}
    trading_dates = set()
    all_contracts = {}
    for line in lines:
        row = [x.strip() for x in line.split(",")]
        if len(row) <= max(col.values()) or not str(row[col["td"]]).isdigit():
            continue
        try:
            td = dt.datetime.strptime(str(row[col["td"]]).strip(), "%Y%m%d").date()
        except ValueError:
            continue
        trading_dates.add(td.isoformat())
        contract = str(row[col["contract"]]).strip().upper()
        if not (contract.startswith(product) and contract[len(product):].isdigit()):
            continue
        c = all_contracts.setdefault(contract, {
            "contract": contract, "long": None, "short": None,
            "long_change": None, "short_change": None,
            "long_rank": None, "short_rank": None,
            "long_status": "not_ranked", "short_status": "not_ranked"})
        try:
            if _member(row[col["long_member"]]) == MEMBER:
                c["long"] = int(row[col["long"]]); c["long_change"] = int(row[col["long_chg"]])
                c["long_rank"] = int(row[col["rank"]]); c["long_status"] = "ranked"
            if _member(row[col["short_member"]]) == MEMBER:
                c["short"] = int(row[col["short"]]); c["short_change"] = int(row[col["short_chg"]])
                c["short_rank"] = int(row[col["rank"]]); c["short_status"] = "ranked"
        except (ValueError, IndexError):
            continue
    if len(trading_dates) != 1:
        return None, [], [], "mixed_date"
    td_iso = trading_dates.pop()
    if expected_date and td_iso != expected_date.isoformat():
        return None, [], [], "date_mismatch"
    observed = [c for c in all_contracts.values()
                if c["long_status"] == "ranked" or c["short_status"] == "ranked"]
    both_absent = sorted(c["contract"] for c in all_contracts.values()
                        if c["long_status"] != "ranked" and c["short_status"] != "ranked")
    return td_iso, observed, both_absent, None


def backfill_one_day(product, date, raw_dir=None, fetcher=None):
    """取单日。返回 record dict(source_status)。raw_dir 若给则落原始CSV+sha256。"""
    fetcher = fetcher or http_get
    url = CFFEX_URL.format(ym=date.strftime("%Y%m"), dd=date.strftime("%d"), p=product)
    rec = {"trading_date": None, "source_url": url, "source_sha256": None,
           "source_status": "error", "source_date_evidence": None,
           "contracts": [], "both_absent_contracts": [], "long_observed": None,
           "short_observed": None, "net_short": None, "observed_difference": None,
           "coverage_status": "source_error", "note": ""}
    try:
        st, raw = fetcher(url)
    except Exception as e:  # noqa
        rec["note"] = f"request error: {e}"
        return rec
    # HTTP 5xx = 源错误(保留旧值),只有 404/空/已知假日 = not_published
    if st and st >= 500:
        rec["note"] = f"HTTP {st}; 源错误,保留历史,不当假日"
        return rec
    if st == 404 or not raw or len(raw) < 50:
        rec["source_status"] = "not_published"
        rec["coverage_status"] = "not_published"
        rec["note"] = f"HTTP {st or 0}/空文件; 节假日或未发布(非会员缺榜)"
        return rec
    sha = hashlib.sha256(raw).hexdigest()
    rec["source_sha256"] = sha
    if raw_dir:
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, f"{product}_{date.strftime('%Y%m%d')}.csv"), "wb") as f:
            f.write(raw)
    td, contracts, both_absent, perr = parse_cffex_csv(raw, product, expected_date=date)
    if perr:
        rec["note"] = f"parse error: {perr}"
        rec["coverage_status"] = "source_error"
        return rec
    rec["trading_date"] = td
    rec["source_date_evidence"] = td
    rec["contracts"] = contracts  # observed union(任一侧上榜)
    rec["both_absent_contracts"] = both_absent  # 双方缺席,不可观测,不当0
    rec["source_status"] = "published"
    longs = [c["long"] for c in contracts if c["long"] is not None]
    shorts = [c["short"] for c in contracts if c["short"] is not None]
    rec["long_observed"] = sum(longs) if longs else None
    rec["short_observed"] = sum(shorts) if shorts else None
    # net_short 仅当 observed union 内每个合约两边都上榜; 单边缺 -> null(partial)
    if longs and shorts and len(longs) == len(shorts) == len(contracts):
        rec["net_short"] = sum(shorts) - sum(longs)
    else:
        rec["net_short"] = None
    rec["observed_difference"] = (rec["short_observed"] - rec["long_observed"]
                                  if rec["short_observed"] is not None and rec["long_observed"] is not None
                                  else None)
    if not longs and not shorts:
        rec["coverage_status"] = "member_not_ranked"
    elif rec["net_short"] is not None:
        rec["coverage_status"] = "complete_observed"
    else:
        rec["coverage_status"] = "partial_side"
    return rec


def backfill(product, start, end, raw_dir=None, fetcher=None, sleep=0.3):
    """逐自然日回补 start..end。返回 shard dict。"""
    fetcher = fetcher or http_get
    records = {}
    d = start
    while d <= end:
        rec = backfill_one_day(product, d, raw_dir=raw_dir, fetcher=fetcher)
        iso = d.isoformat()
        records[iso] = rec
        time.sleep(sleep)
        d += dt.timedelta(days=1)
    published = [k for k, v in records.items() if v["source_status"] == "published"]
    shard = {
        "schema_version": SCHEMA_VERSION,
        "member": "中信期货(代客)", "product": product, "source": "CFFEX",
        "range_start": start.isoformat(), "range_end": end.isoformat(),
        "collected_at": _now_iso(),
        "records": records,
        "coverage": {"date_count": len(records), "published": len(published),
                     "missing": [k for k, v in records.items() if v["source_status"] != "published"]}
    }
    return shard


def collect_daily(product, date, out_path, raw_dir=None, fetcher=None):
    """幂等日更: 取单日 -> 读既有shard -> 更新该date record -> 全量保留其余历史 -> 写回。
    只写独立 futures/{product}.json,不碰ETF history;单源失败保留既有值并标旧。"""
    fetcher = fetcher or http_get
    if os.path.exists(out_path):
        shard = _json.load(open(out_path, encoding="utf-8"))
    else:
        shard = {"schema_version": SCHEMA_VERSION, "member": "中信期货(代客)",
                 "product": product, "source": "CFFEX",
                 "range_start": date.isoformat(), "range_end": date.isoformat(),
                 "records": {}}
    before = len(shard.get("records", {}))
    rec = backfill_one_day(product, date, raw_dir=raw_dir, fetcher=fetcher)
    shard.setdefault("records", {})[date.isoformat()] = rec
    dates = sorted(shard["records"])
    shard["range_start"] = dates[0]
    shard["range_end"] = dates[-1]
    shard["collected_at"] = _now_iso()
    if rec["source_status"] == "published":
        validate_shard(shard)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            _json.dump(shard, f, ensure_ascii=False, indent=1)
    return {"product": product, "date": date.isoformat(),
            "source_status": rec["source_status"], "published_before": before,
            "records_after": len(shard["records"])}


def validate_shard(shard):
    assert shard.get("schema_version") == SCHEMA_VERSION, "schema version mismatch"
    assert shard.get("product") in ("IH", "IF", "IC", "IM")
    return True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-dir", default=None)
    args = ap.parse_args()
    s = backfill(args.product, dt.date.fromisoformat(args.start),
                 dt.date.fromisoformat(args.end), raw_dir=args.raw_dir)
    validate_shard(s)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        _json.dump(s, f, ensure_ascii=False, indent=1)
    print(_json.dump(s["coverage"], __import__("sys").stdout))
