#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview-tools/collect_preview.py — 国内官方预览采集器(只读,隔离输出到 preview-data/official/)

设计原则(见 README/schema):
  * 只接日期可确认的官方源: SSE 份额(STAT_DATE) + CFFEX 四品种前20会员(交易日列)。
  * SZSE 快照无日期 -> 抓取仅留痕,份额标记 pending_unverifiable,绝不据此定 as_of / 不参与计算。
  * NAV 未核 -> 本版只算份额增幅(%),不产生金额。
  * 交易日历 = SSE 实际发布过的日期集合;1/5/20 窗口要求两端及区间每日该标的均有官方值,
    否则 growth=null + coverage_reason,不用第三方 legacy 凑数。
  * 单源失败: 保留 history 上一有效值并标 stale,不生成假 0;期货缺席合约≠0。
  * global_sources 若存在则独立调用,全球失败不阻塞国内(failed_nonblocking)。
  * 不 git;不写 data/、正式 html、既有 *.py 与既有工作流。
仅依赖标准库,以便 GitHub ubuntu-latest 零额外依赖运行。
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request
import urllib.error
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OFFICIAL_DIR = os.path.join(REPO, "preview-data", "official")
HIST_PATH = os.path.join(OFFICIAL_DIR, "history.json")
SNAP_PATH = os.path.join(OFFICIAL_DIR, "snapshot-latest.json")
STATUS_PATH = os.path.join(REPO, "preview-data", "status.json")

SCHEMA_VERSION = "1.0.0"
MEMBER = "中信期货"
SPLIT_ALERT_PCT = 0.25          # 单日份额变动超25% -> 拆合待核,growth 留空

# 原16只 + 新增 159352 = 17 只。core7 星标。
WATCH = [
    ("510050", "SH", "上证50ETF", True),
    ("510300", "SH", "沪深300ETF华泰柏瑞", True),
    ("159352", "SZ", "A500ETF南方", True),
    ("510500", "SH", "中证500ETF南方", True),
    ("512100", "SH", "中证1000ETF南方", True),
    ("159915", "SZ", "创业板ETF易方达", True),
    ("588000", "SH", "科创50ETF华夏", True),
    ("510310", "SH", "沪深300ETF易方达", False),
    ("510330", "SH", "沪深300ETF华夏", False),
    ("159919", "SZ", "沪深300ETF嘉实", False),
    ("510180", "SH", "上证180ETF华安", False),
    ("512500", "SH", "中证500ETF华夏", False),
    ("159845", "SZ", "中证1000ETF华夏", False),
    ("560010", "SH", "中证1000ETF广发", False),
    ("159629", "SZ", "中证1000ETF富国", False),
    ("588080", "SH", "科创板50ETF易方达", False),
    ("159977", "SZ", "创业板ETF天弘", False),
]
PRODUCTS = ["IH", "IF", "IC", "IM"]

SSE_URL = ("https://query.sse.com.cn/commonQuery.do?isPagination=true"
           "&pageHelp.pageSize=10000&pageHelp.pageNo=1&pageHelp.beginPage=1"
           "&pageHelp.cacheSize=1&pageHelp.endPage=1"
           "&sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE={date}")
CFFEX_URL = "http://www.cffex.com.cn/sj/ccpm/{ym}/{dd}/{p}_1.csv"
SZSE_URL = ("https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx"
            "&CATALOGID=1000_lf&TABKEY=tab1&random=0.5")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ------------------------------------------------------------------ http
class Fetcher:
    def __init__(self, timeout=20, retries=2, sleep=lambda s: None):
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def get(self, url, headers=None, binary=False):
        """返回 (status:int, bytes);网络异常抛异常由调用方处理。"""
        last = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = r.read()
                    return r.status, data
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (404,):
                    return e.code, b""
            except Exception as e:  # noqa
                last = e
            if attempt < self.retries:
                self.sleep(1 + attempt)
        raise last


def bj_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S+08:00")


# ------------------------------------------------------------------ sources
def fetch_sse(fetcher, date_str):
    """返回 (rowcount, {code: shares_yi});空数据 rowcount=0。"""
    url = SSE_URL.format(date=date_str)
    status, raw = fetcher.get(url, headers={"User-Agent": UA, "Referer": "https://www.sse.com.cn/"})
    if status != 200:
        raise RuntimeError(f"SSE HTTP {status}")
    obj = json.loads(raw.decode("utf-8"))
    rows = (obj.get("pageHelp") or {}).get("data") or []
    out = {}
    for r in rows:
        code = str(r.get("SEC_CODE", "")).strip()
        vol = r.get("TOT_VOL")
        if code and vol not in (None, "", "-"):
            try:
                out[code] = round(float(vol) / 10000.0, 4)  # 万份->亿份
            except (TypeError, ValueError):
                pass
    return len(rows), out


def fetch_cffex(fetcher, date):
    """date: date()。返回 {product: {contract: {long,long_chg,short,short_chg,long_rank,short_rank}}};
    缺席合约不进 dict(≠0)。"""
    result = {}
    for p in PRODUCTS:
        url = CFFEX_URL.format(ym=date.strftime("%Y%m"), dd=date.strftime("%d"), p=p)
        try:
            status, raw = fetcher.get(url, headers={"User-Agent": UA})
        except Exception:
            continue
        if status != 200 or not raw:
            continue
        text = raw.decode("gbk", errors="replace")
        cons = {}
        for line in text.splitlines():
            cells = [x.strip() for x in line.split(",")]
            if len(cells) < 12 or not cells[0].isdigit():
                continue
            contract = cells[1].upper()
            if not (contract.startswith(p) and contract[len(p):].isdigit()):
                continue
            def member_of(s):
                return re.split(r"[(（]", s)[0].strip()
            c = cons.setdefault(contract, {})
            try:
                if member_of(cells[6]) == MEMBER:
                    c["long"], c["long_chg"] = int(cells[7]), int(cells[8])
                if member_of(cells[9]) == MEMBER:
                    c["short"], c["short_chg"] = int(cells[10]), int(cells[11])
            except ValueError:
                continue
        cons = {k: v for k, v in cons.items() if v}
        if cons:
            result[p] = cons
    return result


def fetch_szse_snapshot(fetcher):
    """仅留痕(无日期,不参与计算)。返回 {code: scale_yi} 或 {}。"""
    try:
        status, raw = fetcher.get(SZSE_URL, headers={"User-Agent": UA,
                                                      "Referer": "https://fund.szse.cn/"})
    except Exception:
        return {}
    if status != 200 or not raw:
        return {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                sx = z.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
                shared = re.findall(r"<t[^>]*>(.*?)</t>", sx, re.S)
            sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8", errors="replace")
        rows = re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S)
        out = {}
        for ridx, row in enumerate(rows):
            cells = re.findall(r'<c[^>]*?(?:\s+t="(\w+)")?[^>]*>(?:<v>(.*?)</v>)?</c>', row)
            vals = []
            for t, v in cells:
                vals.append(shared[int(v)] if (t == "s" and v.isdigit()) else v)
            if ridx == 0:
                continue
            # 第一列基金代码(数字串),第六列当前规模
            code = next((v for v in vals if re.fullmatch(r"\d{6}", str(v))), None)
            if not code:
                continue
            scale = None
            for v in vals:
                try:
                    fv = float(str(v).replace(",", ""))
                    if fv > 1e6:   # 当前规模(份)量级
                        scale = round(fv / 1e8, 4)
                except (TypeError, ValueError):
                    continue
            if code and scale is not None:
                out[code] = scale
        return out
    except Exception:
        return {}


# ------------------------------------------------------------------ global (容错)
def maybe_collect_global():
    """独立调用 global_sources.update_global。全球失败/降级不阻塞国内,但要如实上报状态。
    返回 (state, info)。state: ok | degraded_partial | failed_nonblocking | skipped。"""
    import os
    repo_tools = os.path.join(REPO, "preview-tools")
    for p in (HERE, repo_tools):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import global_sources  # type: ignore
        if not hasattr(global_sources, "update_global"):
            return "skipped", {"reason": "global_sources 无 update_global()"}
        out_path = os.path.join(REPO, "preview-data", "global.json")
        st = global_sources.update_global(output_path=out_path)
        # 以模块返回的 state 为准,不把 CLI 恒0 当成功。
        # ok=实取;degraded_stale_cached=取缓存上次有效值;failed_no_cache=无缓存;
        # 降级/失败均不阻塞国内,但 runner 据此报 partial。
        raw_state = st.get("state", "unknown")
        if raw_state == "ok" and st.get("ok"):
            state = "ok"
        elif raw_state == "degraded_stale_cached":
            state = "degraded_partial"
        else:
            state = "failed_nonblocking"
        return state, {"module_state": raw_state, "path": st.get("path"),
                       "metrics_total": st.get("metrics_total"),
                       "connected": st.get("connected"),
                       "connected_stale": st.get("connected_stale"),
                       "restricted": st.get("restricted"),
                       "link_only": st.get("link_only"), "pending": st.get("pending"),
                       "missing": st.get("missing"), "errors": st.get("errors", [])}
    except ImportError:
        return "skipped", {"reason": "global_sources 模块未就绪(非阻塞)"}
    except Exception as e:  # noqa
        return "failed_nonblocking", {"reason": f"全球源失败,已隔离: {type(e).__name__}: {e}"}


# ------------------------------------------------------------------ history & windows
def load_history():
    if os.path.exists(HIST_PATH):
        try:
            with open(HIST_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def growth_window(calendar_dates, valid_dates, as_of, n, blocked=None):
    """calendar_dates: 升序官方交易日集合; valid_dates: 该ETF在哪些日期有同口径有效值。
    要求 as_of 往前 n 个交易日(含端点)的**每一天**都在 valid_dates 且不在 blocked,
    否则 null+reason(任缺/异常/源失败不跳洞、不跨洞凑 n 步)。"""
    blocked = blocked or set()
    if as_of not in calendar_dates:
        return None, "as_of 不在官方交易日历"
    pos = calendar_dates.index(as_of)
    if pos - n < 0:
        return None, f"官方交易日历史不足{n}天(当前{pos}天),待累积"
    window = calendar_dates[pos - n:pos + 1]   # 含两端,共 n+1 个观测点
    target = window[0]
    for dstr in window:
        if dstr in blocked:
            return None, f"窗口[{target}..{as_of}]含未判休市的源失败日期,阻断"
        if dstr not in valid_dates:
            return None, f"窗口内 {dstr} 缺该标的同口径有效值,跨洞禁算"
    cur = None  # 由调用方在 valid_dates 保证非空
    return window, target   # 返回窗口供调用方算 pct


# ------------------------------------------------------------------ main run
def run(target_date=None, fetcher=None, now=None):
    now = now or bj_now()
    fetcher = fetcher or Fetcher()
    errors = []
    source_log = []

    # 1) 定位 SSE 可确认的最新交易日: 从 target 起往前最多试 6 个自然日(跳周末/未发布)
    probe = target_date or now.date()
    sse_date = None
    sse_map = {}
    sse_rows = 0
    blocked = set()  # SSE 请求失败(非空)的日期:休市未判明,窗口不得跨
    for back in range(6):
        d = probe - dt.timedelta(days=back)
        try:
            rows, mp = fetch_sse(fetcher, d.strftime("%Y-%m-%d"))
            source_log.append({"name": "SSE", "date": d.strftime("%Y-%m-%d"),
                               "http": 200, "rows": rows,
                               "status": "ok" if rows > 0 else "unpublished"})
            if rows > 0:
                sse_date = d
                sse_map = mp
                sse_rows = rows
                break
        except Exception as e:
            source_log.append({"name": "SSE", "date": d.strftime("%Y-%m-%d"),
                               "http": None, "rows": 0, "status": f"error:{e}"})
            errors.append(f"SSE {d}: {e}")
            blocked.add(d.strftime("%Y-%m-%d"))
            break

    # 2) CFFEX 独立日期轴: 从当天往前找最新已发布日,不锁 SSE as_of(异步,UI须明示)
    fut_by_product = {}
    fut_as_of = None
    for back in range(6):
        d = probe - dt.timedelta(days=back)
        try:
            fb = fetch_cffex(fetcher, d)
        except Exception as e:
            source_log.append({"name": "CFFEX", "date": d.strftime("%Y-%m-%d"), "status": f"error:{e}"})
            continue
        if fb:
            fut_by_product = fb
            fut_as_of = d
            source_log.append({"name": "CFFEX", "date": d.strftime("%Y-%m-%d"), "status": "ok"})
            break
        else:
            source_log.append({"name": "CFFEX", "date": d.strftime("%Y-%m-%d"), "status": "no_published"})

    # 3) SZSE 快照留痕(无日期,不参与计算)
    szse_map = fetch_szse_snapshot(fetcher)
    source_log.append({"name": "SZSE_snapshot", "rows": len(szse_map),
                       "status": "recorded_no_date", "note": "无日期,份额待核禁算"})

    if sse_date is None:
        snap = {"schema_version": SCHEMA_VERSION, "generated_at": iso(now),
                "as_of": None, "run_status": "failed",
                "message": "SSE 连续多日无发布(节假日/未发布/源失败)",
                "sources": source_log, "errors": errors}
        save_json(STATUS_PATH, {"schema_version": SCHEMA_VERSION, "generated_at": iso(now),
                                "run_status": "failed", "as_of": None,
                                "domestic": "failed", "sources": source_log, "errors": errors})
        return snap

    as_of = sse_date.strftime("%Y-%m-%d")

    # 4) 读历史,合并当日,写回(幂等:同日同值)
    hist = load_history()
    day = hist.setdefault(as_of, {"etfs": {}, "futures": {}})
    for code, exch, name, core in WATCH:
        rec = {"name": name, "exch": exch, "core": core}
        if exch == "SH":
            if code in sse_map:
                rec["shares_yi"] = sse_map[code]
                rec["shares_status"] = "official_confirmed"
            else:
                # 沪市该日缺此标的:保留历史上一有效值并标 stale
                prev = next((hist[d]["etfs"][code].get("shares_yi")
                             for d in sorted(hist) if code in hist[d].get("etfs", {})
                             and hist[d]["etfs"][code].get("shares_yi") is not None), None)
                rec["shares_yi"] = prev
                rec["shares_status"] = "stale" if prev is not None else "unpublished"
        else:
            # 深市:快照无日期 -> pending_unverifiable;不写入官方份额历史
            rec["shares_yi"] = None
            rec["shares_status"] = "pending_unverifiable"
            if code in szse_map:
                rec["snapshot_scale_yi_no_date"] = szse_map[code]
        day["etfs"][code] = rec

    # 期货不再写入ETF history(原错位: 期货独立as_of≠ETF as_of)。
    # 期货历史独立存 preview-data/official/futures/{product}.json(futures_history.py)。

    # 5) 官方交易日历 = 历史中所有有 SH 官方份额的日期(升序)
    calendar = sorted(d for d in hist
                      if any(hist[d].get("etfs", {}).get(c, {}).get("shares_status") == "official_confirmed"
                             for c in ["510300", "510050"]))

    # 6) 组装 ETF 输出 + 窗口增幅(仅 SH 官方确认标的)
    etf_out = []
    for code, exch, name, core in WATCH:
        rec = day["etfs"][code]
        item = {"code": code, "name": name, "exch": exch, "core": core,
                "shares_yi": rec.get("shares_yi"), "shares_status": rec.get("shares_status"),
                "amount_calc": "not_enabled_nav_unverified"}
        if exch == "SH" and rec.get("shares_status") == "official_confirmed":
            series = {d: hist[d]["etfs"].get(code, {}).get("shares_yi") for d in hist}
            # 该ETF同口径有效值日期集合(official_confirmed 且非null)
            valid = {d for d in hist
                     if hist[d].get("etfs", {}).get(code, {}).get("shares_status") == "official_confirmed"
                     and series.get(d) is not None}

            def pct(n):
                w = growth_window(calendar, valid, as_of, n, blocked)
                if w[0] is None:
                    return None, w[1]
                _win, target = w
                base = series[target]
                if base == 0:
                    return None, "基准日份额为0,不可比"
                return round((series[as_of] / base - 1.0) * 100.0, 2), None

            g1, r1 = pct(1)
            g5, r5 = pct(5)
            g20, r20 = pct(20)
            item.update({"growth_1d_pct": g1, "coverage_reason_1d": r1,
                         "growth_5d_pct": g5, "coverage_reason_5d": r5,
                         "growth_20d_pct": g20, "coverage_reason_20d": r20})
            # 异常告警:1d 可用且超阈值 -> 仅标记待人工核验(>25%不能认定拆合),保留raw delta供人工
            if g1 is not None and abs(g1) >= SPLIT_ALERT_PCT * 100:
                pos = calendar.index(as_of)
                prev = series.get(calendar[pos - 1]) if pos >= 1 else None
                item["anomaly"] = "anomaly_pending_verification"
                item["raw_shares_delta_yi"] = round(series[as_of] - prev, 4) if prev is not None else None
                item["raw_growth_pct_flagged"] = g1
                item["growth_1d_pct"] = None
                item["coverage_reason_1d"] = "单日份额变动>=25%,仅标记待人工核验(不认定拆合),原始delta已保留,禁算"
                item["growth_5d_pct"] = None
                item["growth_20d_pct"] = None
            else:
                item["anomaly"] = "ok"
        else:
            item.update({"growth_1d_pct": None, "growth_5d_pct": None, "growth_20d_pct": None,
                         "coverage_reason": "深市快照无日期/份额未核,本版禁算增幅",
                         "anomaly": "pending"})
        if "snapshot_scale_yi_no_date" in rec:
            item["snapshot_scale_yi_no_date"] = rec["snapshot_scale_yi_no_date"]
        etf_out.append(item)

    # 7) 期货输出(当日真实,非 legacy)
    fut_out = {"member": MEMBER, "as_of": fut_as_of.strftime("%Y-%m-%d") if fut_as_of else None,
              "async_with_etf": (fut_as_of is not None and sse_date is not None
                                 and fut_as_of != sse_date),
              "note": "期指与ETF份额各自独立最新发布日,异步;勿与ETF as_of混作同一资金数",
              "products": {}}
    for p in PRODUCTS:
        cons = fut_by_product.get(p, {})
        long_ = sum(c.get("long", 0) for c in cons.values())
        short = sum(c.get("short", 0) for c in cons.values())
        fut_out["products"][p] = {
            "long": long_, "short": short, "net_short": short - long_,
            "observed_contracts": sorted(cons.keys()),
            "contracts": sorted([[k, v.get("long"), v.get("long_chg"),
                                   v.get("short"), v.get("short_chg")]
                                 for k, v in cons.items()]),
            "note": "仅CFFEX前20上榜合约汇总;某合约当日未上榜=缺席,不等于0手"}

    # 7b) 期指日更append: 独立 shard preview-data/official/futures/{product}.json
    #     只增量当日,不重下04回补;单源失败保留历史并标旧,各源partial互不覆盖。
    fut_daily = {}
    try:
        import futures_history as fh_mod
        FUT_DIR = os.path.join(REPO, "preview-data", "official", "futures")
        for p in PRODUCTS:
            try:
                d_ = fut_as_of or probe
                res = fh_mod.collect_daily(
                    p, d_, os.path.join(FUT_DIR, f"{p}.json"),
                    raw_dir=None)
                fut_daily[p] = res
            except Exception as e:  # noqa
                fut_daily[p] = {"product": p, "state": "failed_keep_history", "err": str(e)}
                errors.append(f"futures_daily {p}: {e}")
    except Exception as e:  # noqa
        errors.append(f"futures_history module: {e}")

    # 8) global 容错调用
    g_state, g_info = maybe_collect_global()
    if sse_date and g_state in ("degraded_partial", "failed_nonblocking"):
        run_status = "partial"   # 国内ok但全球非连通,如实降级,不假成功
    elif sse_date:
        run_status = "ok"
    else:
        run_status = "failed"

    snap = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "collected_at": iso(now),
        "run_status": run_status,
        "as_of": as_of,
        "as_of_confirmed_by": ["sse_stat_date", "cffex_trading_day"] if fut_as_of else ["sse_stat_date"],
        "calendar_source": "sse_observed_trading_days",
        "calendar_days": len(calendar),
        "etfs": etf_out,
        "futures": fut_out,
        "sources": source_log,
        "global": {"state": g_state, **g_info},
        "legacy_note": "preview-data/domestic.json、global.json 等第三方快照仅作 legacy 存档,不用于本官方数值;NAV金额未核,本版仅份额增幅%。",
        "errors": errors,
    }

    save_json(HIST_PATH, hist)
    save_json(SNAP_PATH, snap)
    status = {"schema_version": SCHEMA_VERSION, "generated_at": iso(now),
              "run_status": run_status,
              "as_of": as_of, "domestic": "ok" if sse_date else "failed",
              "global": g_state, "global_detail": g_info,
              "calendar_days": len(calendar),
              "futures_history": {
                  "as_of": fut_as_of.isoformat() if fut_as_of else None,
                  "products": fut_daily,
                  "last_attempt": iso(now),
                  "errors": [e for e in errors if "futures" in e.lower()]},
              "sources": source_log, "errors": errors}
    save_json(STATUS_PATH, status)
    return snap


def backfill(days, end=None, fetcher=None):
    """可重跑 bootstrap: 从 end 往前 days 个自然日,逐自然日取 SSE 有效发布日构建官方交易日轴。
    空=非交易日/未发布(跳过);请求失败=记入 blocked 并返回,不猜休市。幂等(同日同值覆盖)。
    仅写沪市官方份额;深市仍 pending。定时任务不要大跨度回补,首次 bootstrap 用本函数。"""
    fetcher = fetcher or Fetcher()
    end = end or bj_now().date()
    hist = load_history()
    added, blocked = [], []
    for back in range(days + 1):
        d = end - dt.timedelta(days=back)
        try:
            rows, mp = fetch_sse(fetcher, d.strftime("%Y-%m-%d"))
        except Exception as e:
            blocked.append(d.strftime("%Y-%m-%d"))
            continue
        if rows == 0:
            continue
        rec = hist.setdefault(d.strftime("%Y-%m-%d"), {"etfs": {}, "futures": {}})
        for code, exch, name, core in WATCH:
            if exch == "SH" and code in mp:
                rec["etfs"][code] = {"name": name, "exch": "SH", "core": core,
                                     "shares_yi": mp[code], "shares_status": "official_confirmed"}
        added.append(d.strftime("%Y-%m-%d"))
    save_json(HIST_PATH, hist)
    return {"trading_days": len(added), "range": [added[-1], added[0]] if added else None,
            "blocked_request_errors": blocked}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="强制 SSE 探测起始日 YYYYMMDD(默认当日)")
    ap.add_argument("--backfill", type=int, metavar="DAYS",
                    help="可重跑 bootstrap: 回补近 DAYS 自然日的沪市官方份额")
    args = ap.parse_args()
    if args.backfill:
        res = backfill(args.backfill)
        print(json.dumps(res, ensure_ascii=False))
        sys.exit(0)
    tgt = dt.datetime.strptime(args.date, "%Y%m%d").date() if args.date else None
    out = run(target_date=tgt)
    print(json.dumps({"as_of": out.get("as_of"), "run_status": out.get("run_status", "ok"),
                      "etfs": len(out.get("etfs", [])),
                      "calendar_days": out.get("calendar_days"),
                      "global": out.get("global", {}).get("state")}, ensure_ascii=False))
