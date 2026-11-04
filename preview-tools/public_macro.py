#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview-tools/public_macro.py — 合法公开宏观数据(只读,无副作用)。

纯 import 不触发网络;调用 collect_public_macro(existing=None) 才取数。
返回 {"metrics":[...], "fetch_status":{...}, "errors":[...]}。

源(均为一手/官方公开,无API key;FRED 仅作镜像且需key,本版标注不泛化):
  - 美国国债名义收益率曲线 CSV (home.treasury.gov)
  - 美国国债实际收益率(TIPS) CSV (home.treasury.gov)
  - NYFed SOFR API (markets.newyorkfed.org)
  - NYFed ACM 期限溢价 .xls (newyorkfed.org;旧版OLE2格式,需 pip 安装 preview-tools/requirements.txt 的 xlrd)
  - IORB / NFCI / SAHM: FRED fredgraph.csv 免key镜像(无API key);
    SAHM = SAHMREALTIME(实时规则,单位百分点pp,已独审核列名 observation_date,SAHMREALTIME);
    fetch_fred 严格要求以 "observation_date," 开头,HTML错误页不解析;
    网络失败=status pending/failed with known semantics,非series未知。
    FRED 仅镜像,具体 series 许可见 source_url
计算口径:
  - 10Y 盈亏平衡通胀 = 名义10Y - 实际10Y,仅同日 intersection;名义/实际不混。
  - SOFR-IORB 利差需 IORB 同日,IORB 未通则 null。
单位: 利率/利差均为 %(annualized);term premium 为 %(decimal)。
"""
import csv as _csv
import datetime as dt
import io
import json as _json
import urllib.request
import urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TREAS_NOM = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
             "daily-treasury-rates.csv/{yr}/all?field_tdr_date_value={yr}"
             "&type=daily_treasury_yield_curve&page&_format=csv")
TREAS_REAL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
              "daily-treasury-rates.csv/{yr}/all?field_tdr_date_value={yr}"
              "&type=daily_treasury_real_yield_curve&page&_format=csv")
SOFR_API = ("https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
            "?startDate={s}&endDate={e}")
ACM_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xlsx"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FRED_LICENSE = "https://fred.stlouisfed.org/legal/"
TREAS_LICENSE = "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics"
TREAS_REUSE = "https://fiscal.treasury.gov/fiscal-services/reporting/report-templates.html"
NYFED_TERMS = "https://resources.newyorkfed.org/privacy/termsofuse"
NYFED_DISCLAIMER = ("数据来源纽约联储(SOFR/ACM)。本看板为第三方汇编,非纽约联储官方发布;"
                    "纽约联储不对本看板的再发布负责、不背书本产品、不承担任何责任。")
NYFED_NOTICE_EN = ("The SOFR is subject to the Terms of Use posted at newyorkfed.org. "
                   "The New York Fed is not responsible for publication of the SOFR by this dashboard, "
                   "does not sanction or endorse any particular republication, and has no liability for your use.")
ACM_COPYRIGHT = ("©2026 Federal Reserve Bank of New York. Content from the New York Fed is subject to the "
                 "New York Fed Terms of Use (newyorkfed.org). ACM term premia: source research by "
                 "Adrian, Crump, Moench; this dashboard's derived/calculated values are not attributable to "
                 "the New York Fed or the authors.")


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _get(url, timeout=20, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except Exception as e:  # noqa
            last = e
    raise last


def _num(v):
    try:
        if v in (None, "", "N/A", "NA"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _guard(series, today):
    """过滤空值、未来日期。series: [(date_str, value)] -> sorted dict; today=ISO str"""
    t0 = dt.date.fromisoformat(today)
    out = {}
    for d, v in series:
        v = _num(v)
        if v is None:
            continue
        try:
            dd = dt.date.fromisoformat(d)
        except ValueError:
            continue
        if dd > t0:  # 防未来日期
            continue
        out[d] = v
    return out


def collect_public_macro(existing=None, years=(2026, 2025, 2024), fetcher=None, now=None):
    """existing: 可选上一轮缓存(用于失败时保留);本版不依赖,留接口给总模块。"""
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    today = now.date().isoformat()
    g = fetcher or _get
    metrics = []
    status = {}
    errors = []

    def add(id_, name, unit, src, lic, freq, series, note="",
            attribution="", disclaimer="", copyright_notice="", terms_notice_en=""):
        series = _guard(series, today)
        if not series:
            status[id_] = {"state": "pending", "reason": "no valid data"}
            return
        dates = sorted(series)
        metrics.append({
            "id": id_, "name": name, "unit": unit,
            "value": series[dates[-1]], "as_of": dates[-1],
            "history": [{"date": d, "value": series[d]} for d in dates],
            "source_url": src, "license_url": lic, "frequency": freq,
            "attribution": attribution, "disclaimer": disclaimer,
            "copyright_notice": copyright_notice, "terms_notice_en": terms_notice_en,
            "collected_at": _now_iso(), "note": note})
        status[id_] = {"state": "ok", "as_of": dates[-1], "n": len(dates)}

    # 1) Treasury 名义曲线(多年CSV合并; 同时取10Y标量与全期限曲线,无额外网络)
    TENORS_NOM = ["1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr",
                  "7 Yr", "10 Yr", "20 Yr", "30 Yr"]
    TENORS_REAL = ["5 YR", "7 YR", "10 YR", "20 YR", "30 YR"]
    try:
        nom = {}; nom_curve = {}
        for yr in years:
            st, raw = g(TREAS_NOM.format(yr=yr))
            if st != 200:
                continue
            for row in _csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))):
                d = row.get("Date", "")
                try:
                    dd = dt.datetime.strptime(d, "%m/%d/%Y").date().isoformat()
                except ValueError:
                    continue
                curve = {t: _num(row.get(t)) for t in TENORS_NOM}
                curve = {k: v for k, v in curve.items() if v is not None}
                if curve:
                    nom_curve[dd] = curve
                if dd > dt.date.fromisoformat(today).isoformat() or dd > today:
                    continue
                v = _num(row.get("10 Yr"))
                if v is not None:
                    nom[dd] = v
        add("ust_10y_nominal", "美国10年期国债名义收益率", "%",
            TREAS_NOM.format(yr="2026"), TREAS_LICENSE, "daily",
            list(nom.items()), "一手:美国财政部每日国债收益率曲线")
        # 全期限名义曲线 value={tenor:num} history[{date,{tenor:num}}]
        cdates = sorted(d for d in nom_curve if d <= today)
        if cdates:
            metrics.append({
                "id": "ust_curve_nominal", "name": "美国国债名义收益率曲线(全期限)", "unit": "%",
                "value": nom_curve[cdates[-1]], "as_of": cdates[-1],
                "history": [{"date": d, "value": nom_curve[d]} for d in cdates],
                "source_url": TREAS_NOM.format(yr="2026"), "license_url": TREAS_LICENSE,
                "frequency": "daily", "attribution": "Source: U.S. Treasury yield curve",
                "collected_at": _now_iso(), "note": "一手美国财政部;tenor见key(1Mo/3Mo/..30Y);至少1年历史"})
            status["ust_curve_nominal"] = {"state": "ok", "as_of": cdates[-1], "n": len(cdates)}
    except Exception as e:  # noqa
        errors.append(f"ust_10y_nominal: {e}"); status["ust_10y_nominal"] = {"state": "error"}

    # 2) Treasury 实际(TIPS) 10Y + 全期限实际曲线
    try:
        real = {}; real_curve = {}
        for yr in years:
            st, raw = g(TREAS_REAL.format(yr=yr))
            if st != 200:
                continue
            for row in _csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))):
                d = row.get("Date", "")
                try:
                    dd = dt.datetime.strptime(d, "%m/%d/%Y").date().isoformat()
                except ValueError:
                    continue
                curve = {t: _num(row.get(t)) for t in TENORS_REAL}
                curve = {k: v for k, v in curve.items() if v is not None}
                if curve:
                    real_curve[dd] = curve
                v = _num(row.get("10 YR"))
                if v is not None:
                    real[dd] = v
        add("ust_10y_real", "美国10年期TIPS实际收益率", "%",
            TREAS_REAL.format(yr="2026"), TREAS_LICENSE, "daily",
            list(real.items()), "一手:美国财政部实际收益率曲线")
        rdates = sorted(d for d in real_curve if d <= today)
        if rdates:
            metrics.append({
                "id": "ust_curve_real", "name": "美国国债实际收益率曲线(TIPS,全期限)", "unit": "%",
                "value": real_curve[rdates[-1]], "as_of": rdates[-1],
                "history": [{"date": d, "value": real_curve[d]} for d in rdates],
                "source_url": TREAS_REAL.format(yr="2026"), "license_url": TREAS_LICENSE,
                "frequency": "daily", "attribution": "Source: U.S. Treasury real yield curve (TIPS)",
                "collected_at": _now_iso(), "note": "一手美国财政部;tenor 5/7/10/20/30YR;至少1年历史"})
            status["ust_curve_real"] = {"state": "ok", "as_of": rdates[-1], "n": len(rdates)}
    except Exception as e:  # noqa
        errors.append(f"ust_10y_real: {e}"); status["ust_10y_real"] = {"state": "error"}

    # 3) 10Y 盈亏平衡通胀(名义-实际,同日交集)
    try:
        nom_m = {m["as_of"]: m["value"] for m in metrics if m["id"] == "ust_10y_nominal"}
        real_m = {m["as_of"]: m["value"] for m in metrics if m["id"] == "ust_10y_real"}
        nom_hist = {h["date"]: h["value"] for m in metrics if m["id"] == "ust_10y_nominal" for h in m["history"]}
        real_hist = {h["date"]: h["value"] for m in metrics if m["id"] == "ust_10y_real" for h in m["history"]}
        spread = [(d, round(nom_hist[d] - real_hist[d], 3)) for d in sorted(set(nom_hist) & set(real_hist))]
        add("bei_10y", "美国10年期盈亏平衡通胀(名义-实际)", "%",
            TREAS_NOM.format(yr="2026") + " & " + TREAS_REAL.format(yr="2026"),
            TREAS_LICENSE, "daily", spread, "同日交集;名义/实际不混")
    except Exception as e:  # noqa
        errors.append(f"bei_10y: {e}")

    # 4) NYFed SOFR
    try:
        end = today
        start = (dt.date.fromisoformat(today) - dt.timedelta(days=400)).isoformat()
        st, raw = g(SOFR_API.format(s=start, e=end))
        sofr = {}
        if st == 200:
            for r in _json.loads(raw.decode("utf-8")).get("refRates", []):
                v = _num(r.get("percentRate"))
                if v is not None:
                    sofr[r["effectiveDate"]] = v
        add("sofr", "担保隔夜融资利率 SOFR", "%",
            SOFR_API.format(s=start, e=end), NYFED_TERMS, "daily",
            list(sofr.items()), "一手:纽约联储",
            attribution="Source: Federal Reserve Bank of New York (SOFR)",
            disclaimer=NYFED_DISCLAIMER, terms_notice_en=NYFED_NOTICE_EN)
    except Exception as e:  # noqa
        errors.append(f"sofr: {e}"); status["sofr"] = {"state": "error"}

    # 5) NYFed ACM 10Y 期限溢价(.xls,xlrd可选)
    try:
        acm = {}
        st, raw = g(ACM_URL)
        if st == 200:
            import io as _io
            try:
                import xlrd  # 可选;无则标 pending
                wb = xlrd.open_workbook(file_contents=raw)
                sh = wb.sheet_by_name("ACM Daily") if "ACM Daily" in wb.sheet_names() else wb.sheet_by_index(0)
                hdr = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
                ci = hdr.index("ACMTP10")  # 10Y期限溢价(非模型收益率ACMY10)
                for r in range(1, sh.nrows):
                    dcell = sh.cell_value(r, 0)
                    dd = None
                    if isinstance(dcell, (int, float)):
                        try:
                            dd = dt.datetime(*xlrd.xldate_as_tuple(dcell, wb.datemode)).date().isoformat()
                        except Exception:  # noqa
                            dd = None
                    else:
                        try:
                            dd = dt.datetime.strptime(str(dcell).strip(), "%d-%b-%Y").date().isoformat()
                        except ValueError:
                            dd = None
                    if dd is None:
                        continue
                    v = _num(sh.cell_value(r, ci))
                    if v is not None:
                        acm[dd] = round(v, 3)
            except ImportError:
                status["acm_tp_10y"] = {"state": "pending", "reason": "xlrd 不可用,待解析"}
        add("acm_tp_10y", "NYFed ACM 10年期国债期限溢价", "%",
            ACM_URL, NYFED_TERMS, "daily", list(acm.items()),
            "一手:纽约联储;模型估计,非市场报价",
            attribution="Source: Federal Reserve Bank of New York (Adrian-Crump-Moench term premia)",
            disclaimer=NYFED_DISCLAIMER, copyright_notice=ACM_COPYRIGHT)
    except Exception as e:  # noqa
        errors.append(f"acm_tp_10y: {e}"); status["acm_tp_10y"] = {"state": "error"}

    # 6) FRED 免key fredgraph.csv 镜像(IORB/NFCI 已实测200; SAHM间歇502)
    def fetch_fred(sid):
        st, raw = g(FRED_CSV.format(sid=sid))
        out = {}
        text = raw.decode("utf-8", errors="replace")
        # 守卫: 返回HTML(错误页/404/拦截)绝不当CSV解析
        if st == 200 and not text.lstrip().startswith("observation_date,"):
            return {}, 502
        if st == 200:
            for row in _csv.DictReader(io.StringIO(text)):
                d = row.get("observation_date", "")
                v = _num(row.get(sid))
                if v is not None:
                    out[d] = v
        return out, st

    # IORB (FRED镜像; 原始=Fed H.15/IOR)
    try:
        iorb, st = fetch_fred("IORB")
        add("iorb", "美联储准备金余额利率 IORB", "%",
            FRED_CSV.format(sid="IORB"), FRED_LICENSE, "daily",
            list(iorb.items()), "FRED fredgraph免key镜像;原始来源Fed(H.15/IOR);metadata: public domain, citation requested",
            attribution="Source: Federal Reserve Board (via FRED series IORB); public domain, citation requested")
        if not iorb:
            metrics.append({"id": "iorb", "name": "美联储准备金余额利率 IORB", "unit": "%",
                            "value": None, "as_of": None, "history": [],
                            "source_url": FRED_CSV.format(sid="IORB"), "license_url": FRED_LICENSE,
                            "frequency": "daily", "collected_at": _now_iso(),
                            "note": f"FRED fredgraph免key;本版HTTP {st},待重试"})
            status["iorb"] = {"state": "pending", "reason": f"FRED HTTP {st}"}
    except Exception as e:  # noqa
        errors.append(f"iorb: {e}")
        metrics.append({"id": "iorb", "name": "美联储准备金余额利率 IORB", "unit": "%",
                        "value": None, "as_of": None, "history": [],
                        "source_url": FRED_CSV.format(sid="IORB"), "license_url": FRED_LICENSE,
                        "frequency": "daily", "collected_at": _now_iso(),
                        "note": f"本版取数异常:{e}"})
        status["iorb"] = {"state": "error"}

    # SOFR-IORB 利差(同日交集)
    try:
        sofr_hist = {h["date"]: h["value"] for m in metrics if m["id"] == "sofr" for h in m["history"]}
        iorb_m = {m["history"][i]["date"]: m["history"][i]["value"]
                  for m in metrics if m["id"] == "iorb" for i in range(len(m["history"]))}
        spread = [(d, round(sofr_hist[d] - iorb_m[d], 3))
                  for d in sorted(set(sofr_hist) & set(iorb_m))]
        add("sofr_iorb_spread", "SOFR-IORB 利差", "%",
            FRED_CSV.format(sid="IORB"), FRED_LICENSE, "daily", spread,
            "同日交集;SOFR(NYFed)与IORB(FRED)不跨源凑日")
        if not spread:
            metrics.append({"id": "sofr_iorb_spread", "name": "SOFR-IORB 利差", "unit": "%",
                            "value": None, "as_of": None, "history": [],
                            "source_url": "SOFR(NYFed) vs IORB(FRED fredgraph)", "license_url": FRED_LICENSE,
                            "frequency": "daily", "collected_at": _now_iso(),
                            "note": "需SOFR与IORB同日值,缺一则null;不跨源凑日"})
            status["sofr_iorb_spread"] = {"state": "pending", "reason": "same-day intersection empty"}
    except Exception as e:  # noqa
        errors.append(f"sofr_iorb_spread: {e}")
        metrics.append({"id": "sofr_iorb_spread", "name": "SOFR-IORB 利差", "unit": "%",
                        "value": None, "as_of": None, "history": [],
                        "source_url": "SOFR(NYFed) vs IORB(FRED fredgraph)", "license_url": FRED_LICENSE,
                        "frequency": "daily", "collected_at": _now_iso(),
                        "note": "本版计算异常"})
        status["sofr_iorb_spread"] = {"state": "pending"}

    # NFCI (Chicago Fed数据,FRED免key镜像;一手Chicago Fed本版500)
    try:
        nfci, st = fetch_fred("NFCI")
        add("nfci", "全国金融状况指数 NFCI", "index",
            FRED_CSV.format(sid="NFCI"), FRED_LICENSE, "weekly",
            list(nfci.items()), "一手Chicago Fed;FRED免key镜像(本版Chicago直连500);metadata: citation required",
            attribution="Source: Federal Reserve Bank of Chicago (via FRED series NFCI); citation required")
        if not nfci:
            metrics.append({"id": "nfci", "name": "全国金融状况指数 NFCI", "unit": "index",
                            "value": None, "as_of": None, "history": [],
                            "source_url": FRED_CSV.format(sid="NFCI"), "license_url": FRED_LICENSE,
                            "frequency": "weekly", "collected_at": _now_iso(),
                            "note": f"一手Chicago Fed,FRED免key镜像;本版HTTP {st},待重试"})
            status["nfci"] = {"state": "pending", "reason": f"FRED HTTP {st}"}
    except Exception as e:  # noqa
        errors.append(f"nfci: {e}")
        metrics.append({"id": "nfci", "name": "全国金融状况指数 NFCI", "unit": "index",
                        "value": None, "as_of": None, "history": [],
                        "source_url": FRED_CSV.format(sid="NFCI"), "license_url": FRED_LICENSE,
                        "frequency": "weekly", "collected_at": _now_iso(),
                        "note": f"本版取数异常:{e}"})
        status["nfci"] = {"state": "error"}

    # SAHM(实时规则): 正确series=SAHMREALTIME(observation_date,SAHMREALTIME,自1959-12),单位百分点pp
    try:
        sahm, st = fetch_fred("SAHMREALTIME")
        add("sahm", "Sahm Rule 实时触发指标", "pp",
            FRED_CSV.format(sid="SAHMREALTIME"), FRED_LICENSE, "monthly",
            list(sahm.items()), "FRED系列SAHMREALTIME(已独审核列名 observation_date,SAHMREALTIME);单位百分点;作者Claudia Sahm,源非Atlanta Fed;public domain, citation requested",
            attribution="Source: Claudia Sahm — Sahm Rule real-time (via FRED series SAHMREALTIME); public domain, citation requested")
        if not sahm:
            metrics.append({"id": "sahm", "name": "Sahm Rule 实时触发指标", "unit": "pp",
                            "value": None, "as_of": None, "history": [],
                            "source_url": FRED_CSV.format(sid="SAHMREALTIME"), "license_url": FRED_LICENSE,
                            "frequency": "monthly", "collected_at": _now_iso(),
                            "note": f"SAHMREALTIME;本版HTTP {st}或返回非CSV,待重试;源非Atlanta Fed"})
            status["sahm"] = {"state": "pending", "reason": f"FRED HTTP {st} / non-CSV"}
    except Exception as e:  # noqa
        errors.append(f"sahm: {e}")
        metrics.append({"id": "sahm", "name": "Sahm Rule 实时触发指标", "unit": "pp",
                        "value": None, "as_of": None, "history": [],
                        "source_url": FRED_CSV.format(sid="SAHMREALTIME"), "license_url": FRED_LICENSE,
                        "frequency": "monthly", "collected_at": _now_iso(),
                        "note": f"本版取数异常:{e}"})
        status["sahm"] = {"state": "error"}

    return {"metrics": metrics, "fetch_status": status, "errors": errors}


if __name__ == "__main__":
    out = collect_public_macro()
    print(_json.dumps({
        "n_metrics": len(out["metrics"]),
        "ok": {k: v for k, v in out["fetch_status"].items() if v.get("state") == "ok"},
        "pending": [k for k, v in out["fetch_status"].items() if v.get("state") == "pending"],
        "errors": out["errors"][:3],
    }, ensure_ascii=False, indent=1))
