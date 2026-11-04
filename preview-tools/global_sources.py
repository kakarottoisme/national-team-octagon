"""global_sources.py — Real-data source module for the global risk preview.

Connected sources (this run, verified):
- US Treasury nominal daily yield curve (public domain)
- Cboe CDN daily index CSV: VIX, VVIX, SKEW, VXN, VIX9D, VIX3M, COR1M, VXTLT
  (publicly downloadable from cdn.cboe.com; Cboe Global Markets copyright; display with attribution)

NOT connected (kept link_only/restricted/pending, no transcribed values):
- ICE BofA MOVE / HY OAS / IG OAS (ICE copyright; FRED terms prohibit reproduction)
- CDS (LSEG/Bloomberg paid)
- GEX (SqueezeMetrics vendor estimate; free API 502/404)
- Yahoo prices (no commercial redistribution license)
- FRED government series (SAHM/NFCI/TIPS etc. — domestic agent wires via public_macro.py)

Pure import — no network side effects. Network only inside fetch_* functions.
No FRED key, no secrets, no paid accounts.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import urllib.request
import urllib.error
import zipfile
from xml.etree import ElementTree as ET
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TREASURY_NOMINAL_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/daily-treasury-rates.csv/2026/all"
    "?type=daily_treasury_yield_curve"
    "&field_tdr_date_value=2026&page&_format=csv"
)

CBOE_CDN_BASE = "https://cdn.cboe.com/api/global/us_indices/daily_prices"

CBOE_SYMBOLS = {
    "VIX":   f"{CBOE_CDN_BASE}/VIX_History.csv",
    "VVIX":  f"{CBOE_CDN_BASE}/VVIX_History.csv",
    "SKEW":  f"{CBOE_CDN_BASE}/SKEW_History.csv",
    "VXN":   f"{CBOE_CDN_BASE}/VXN_History.csv",
    "VIX9D": f"{CBOE_CDN_BASE}/VIX9D_History.csv",
    "VIX3M": f"{CBOE_CDN_BASE}/VIX3M_History.csv",
    "COR1M": f"{CBOE_CDN_BASE}/COR1M_History.csv",
    "VXTLT": f"{CBOE_CDN_BASE}/VXTLT_History.csv",
}

CBOE_LICENSE = "Cboe Global Markets (C) ; public CDN CSV download; display with source attribution."
TREASURY_LICENSE = "US Government work -- public domain. https://home.treasury.gov/"

HISTORY_DAYS = 30


# ---------------------------------------------------------------------------
# HTTP + date helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "national-team-octagon/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _normalize_iso_date(raw: str | None) -> str | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return raw
    try:
        m, d, y = raw.split("/")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_treasury_nominal_curve() -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False, "source_url": TREASURY_NOMINAL_CSV_URL,
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of": None, "as_of_raw": None, "rates": {}, "history": [],
    }
    try:
        raw = _http_get(TREASURY_NOMINAL_CSV_URL)
    except Exception as e:
        out["error"] = f"network: {type(e).__name__}: {e}"
        return out
    try:
        reader = csv.DictReader(io.StringIO(raw))
        rows = [r for r in reader if r.get("Date")]
        if not rows:
            out["error"] = "empty CSV"
            return out
        latest = rows[0]
        out["as_of_raw"] = latest["Date"]
        out["as_of"] = _normalize_iso_date(latest["Date"])
        rates = {}
        for k, v in latest.items():
            if k == "Date":
                continue
            try:
                rates[k.strip()] = float(v)
            except (TypeError, ValueError):
                continue
        out["rates"] = rates
        hist = []
        for r in rows[:HISTORY_DAYS]:
            try:
                v = float(r.get("10 Yr", "")) if r.get("10 Yr") else None
                if v is not None:
                    hist.append({"date": _normalize_iso_date(r["Date"]), "value": v})
            except (ValueError, KeyError):
                continue
        out["history"] = hist
        out["ok"] = True
    except Exception as e:
        out["error"] = f"parse: {type(e).__name__}: {e}"
    return out


def fetch_cboe_index(symbol: str) -> dict[str, Any]:
    url = CBOE_SYMBOLS.get(symbol)
    out: dict[str, Any] = {
        "ok": False, "symbol": symbol, "source_url": url,
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of": None, "as_of_raw": None, "value": None, "history": [],
    }
    if not url:
        out["error"] = f"no URL for {symbol}"
        return out
    try:
        raw = _http_get(url)
    except Exception as e:
        out["error"] = f"network: {type(e).__name__}: {e}"
        return out
    try:
        reader = csv.DictReader(io.StringIO(raw))
        rows = [r for r in reader if r.get("DATE")]
        if not rows:
            out["error"] = "empty CSV"
            return out
        latest = rows[0]
        out["as_of_raw"] = latest["DATE"]
        out["as_of"] = _normalize_iso_date(latest["DATE"])
        close = latest.get("CLOSE") or latest.get("Close") or ""
        try:
            out["value"] = float(close)
        except (TypeError, ValueError):
            out["value"] = None
        hist = []
        for r in rows[:HISTORY_DAYS]:
            c = r.get("CLOSE") or r.get("Close") or ""
            try:
                v = float(c)
                hist.append({"date": _normalize_iso_date(r["DATE"]), "value": v})
            except (ValueError, TypeError):
                continue
        out["history"] = hist
        out["ok"] = out["value"] is not None
        if not out["ok"]:
            out["error"] = "no close value"
    except Exception as e:
        out["error"] = f"parse: {type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

_METRIC_DEFS: list[dict[str, Any]] = [
    {"id": "A1_move", "name": "ICE BofA MOVE Treasury Volatility", "layer": "A_macro_rates",
     "unit": "index points", "status": "link_only",
     "link_url": "https://www.theice.com/marketdata/indices/indices-dashboard/move-index",
     "interpretation_cn": "美债期权隐含波动率综合读数。读数越高表示美债市场预期波动越大；本身不指向方向，只反映预期幅度。本面板未取得 ICE 授权，不复制数值。",
     "restricted_reason": "ICE copyright; VXTLT is a different index, cannot substitute MOVE."},
    {"id": "A1b_vxtlt", "name": "CBOE 10-Year Treasury Volatility (VXTLT)", "layer": "A_macro_rates",
     "unit": "index points", "status": "link_only", "symbol": "VXTLT",
     "interpretation_cn": "10 年期美债期权隐含波动率。与 MOVE 口径不同，是 Cboe 自家 10Y 期限波动率指数；趋势可参照但不可与 MOVE 数值互换。"},
    {"id": "A2_move_vix_ratio", "name": "Treasury vol / equity vol ratio (proxy)", "layer": "A_macro_rates",
     "unit": "ratio", "status": "link_only",
     "interpretation_cn": "原站用 MOVE/VIX 比值判断债市相对股市波动。MOVE 未授权，本面板不提供该比值；可分别查看 A1b(VXTLT) 与 E1(VIX) 自行比较。",
     "restricted_reason": "Derived from A1 (MOVE) which is link_only."},
    {"id": "A3_yield_curve_nominal", "name": "US Treasury Nominal Yield Curve", "layer": "A_macro_rates",
     "unit": "percent", "status": "connected", "source": "treasury",
     "interpretation_cn": "美国财政部每日公布的名义国债收益率（1M-30Y）。2Y/10Y 差值即期限利差，为正通常表示正常向上倾斜；不直接判断经济周期方向。"},
    {"id": "A3b_yield_curve_real", "name": "US Treasury Real (TIPS) Yield Curve", "layer": "A_macro_rates",
     "unit": "percent", "status": "pending",
     "interpretation_cn": "TIPS 实际收益率（扣除通胀预期）。国内 macro 模块负责接入，本面板不重复。",
     "restricted_reason": "Domestic public_macro.py owns this."},
    {"id": "A4_fed_path", "name": "FOMC policy path", "layer": "A_macro_rates",
     "unit": "text", "status": "link_only",
     "link_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
     "interpretation_cn": "FOMC 会议决议与点阵图。政策路径由联储局公布，本面板不预测下一步加息/降息，也不对市场隐含定价做判断。",
     "restricted_reason": "Event-driven; no machine-readable status."},

    {"id": "B1_hy_oas", "name": "ICE BofA US High Yield OAS", "layer": "B_credit",
     "unit": "percent", "status": "restricted",
     "link_url": "https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
     "interpretation_cn": "高收益债相对国债的期权调整利差。数值走阔通常表示市场对低评级债券要求更高补偿；绝对值高低需结合历史分位判断，不单日定论。",
     "restricted_reason": "ICE copyright; FRED terms prohibit third-party reproduction."},
    {"id": "B2_ig_oas", "name": "ICE BofA US Corporate (IG) OAS", "layer": "B_credit",
     "unit": "percent", "status": "restricted",
     "link_url": "https://fred.stlouisfed.org/series/BAMLC0A0CM",
     "interpretation_cn": "投资级企业债 OAS。与 HY 对照可看信用层整体定价；走阔方向与 HY 一致但幅度通常更小。",
     "restricted_reason": "Same ICE restriction."},
    {"id": "B3_tech_cds_5y", "name": "Tech mega-cap 5Y CDS", "layer": "B_credit",
     "unit": "basis points", "status": "link_only",
     "link_url": "https://www.lseg.com/en/market-data",
     "interpretation_cn": "科技巨头 5 年期信用违约互换利差。走阔表示市场对其违约风险定价上升；绝对 bp 水平需区分发行人评级。",
     "restricted_reason": "LSEG/Bloomberg paid; no free public feed."},
    {"id": "B4_ai_chain_credit", "name": "AI-chain credit (CoreWeave/Oracle)", "layer": "B_credit",
     "unit": "text", "status": "link_only",
     "interpretation_cn": "AI 产业链相关债券/CDS 信用状态。属新闻与研究观察，无机器公开端点，本面板只提供链接。",
     "restricted_reason": "News/research clip; no machine feed."},
    {"id": "B5_ai_cds_volume", "name": "AI-issuer CDS weekly notional", "layer": "B_credit",
     "unit": "USD billions", "status": "link_only",
     "interpretation_cn": "AI 发债主体 CDS 周度成交名义额。成交放大不一定代表方向，需结合利差走阔/收窄一起看。",
     "restricted_reason": "Morgan Stanley / FT research; manual."},

    {"id": "C1_skew", "name": "CBOE SKEW", "layer": "C_second_order_vol",
     "unit": "index points", "status": "link_only", "symbol": "SKEW",
     "interpretation_cn": "标普 500 虚值看跌期权相对平价的偏斜程度。读数高表示市场为尾部风险付更高溢价；不等于即将暴跌，只反映定价。"},
    {"id": "C2_vvix", "name": "CBOE VVIX", "layer": "C_second_order_vol",
     "unit": "index points", "status": "link_only", "symbol": "VVIX",
     "interpretation_cn": "VIX 自身的波动率（波动率的波动率）。读数跳升通常伴随市场不确定性放大；绝对值高低需看历史分位。"},
    {"id": "C3_vix_term", "name": "VIX / VIX3M term ratio", "layer": "C_second_order_vol",
     "unit": "ratio", "status": "link_only", "derive_from": ("VIX", "VIX3M"), "derive_op": "ratio",
     "interpretation_cn": "近月 VIX 与 3 月 VIX 比值。比值 >1 表示近端波动预期高于远端（倒挂），通常对应压力期；<1 表示正常向上结构。"},
    {"id": "C4_vix9d_vix", "name": "VIX9D / VIX ratio", "layer": "C_second_order_vol",
     "unit": "ratio", "status": "link_only", "derive_from": ("VIX9D", "VIX"), "derive_op": "ratio",
     "interpretation_cn": "9 天期 VIX 与 30 天 VIX 比值。比值 >1 表示极短期事件（如 FOMC/财报）预期升温；回落通常表示事件过去。"},
    {"id": "C5_putcall_skew_scissor", "name": "P/C 63d vs SKEW 5d scissor", "layer": "C_second_order_vol",
     "unit": "text", "status": "link_only",
     "link_url": "https://www.cboe.com/us/market_statistics/daily/",
     "interpretation_cn": "看跌/看涨成交比与 SKEW 的背离。本面板未接 Cboe 每日期权统计 CSV，只给链接。",
     "restricted_reason": "Cboe daily options stats endpoint not wired."},

    {"id": "D1_gex", "name": "Dealer Gamma Exposure (GEX)", "layer": "D_mm_structure",
     "unit": "USD billions", "status": "link_only",
     "link_url": "https://squeezemetrics.com/monitor",
     "interpretation_cn": "做市商伽马敞口估算。正值通常表示做市商倾向于抑制波动，负值倾向于放大；不同供应商口径差异大，不含全部 0DTE。",
     "restricted_reason": "Vendor estimate; free API 502/404 this run."},
    {"id": "D2_cor1m", "name": "CBOE 1-Month Implied Correlation", "layer": "D_mm_structure",
     "unit": "index points", "status": "link_only", "symbol": "COR1M",
     "interpretation_cn": "标普成分股 1 个月期隐含相关性。读数高表示期权市场预期成分股同向运动（系统性风险上升）；读数低表示个股分化。"},
    {"id": "D3_vixeq_vix", "name": "VIXEQ / VIX tech crowding", "layer": "D_mm_structure",
     "unit": "ratio", "status": "link_only",
     "interpretation_cn": "科技股 ETF 波动率与 VIX 比值。Cboe VIXEQ 公开度未核，本面板不复制。",
     "restricted_reason": "Cboe VIXEQ + LSEG manual."},
    {"id": "D4_vxn_vix", "name": "VXN - VIX tech premium", "layer": "D_mm_structure",
     "unit": "index points", "status": "link_only", "derive_from": ("VXN", "VIX"), "derive_op": "diff",
     "interpretation_cn": "纳斯达克 100 波动率与标普 500 波动率之差。差值扩大表示科技股期权相对大盘更贵；收窄表示科技板块压力相对缓和。"},

    {"id": "E1_vix", "name": "CBOE VIX", "layer": "E_first_order_sentiment",
     "unit": "index points", "status": "link_only", "symbol": "VIX",
     "interpretation_cn": "标普 500 未来 30 天隐含波动率。读数本身不预测方向；通常 20 以上表示市场定价较高波动，20 以下相对平静。"},
    {"id": "E2_putcall_structure", "name": "Put/Call structure", "layer": "E_first_order_sentiment",
     "unit": "text", "status": "link_only",
     "interpretation_cn": "个股与指数看跌/看涨成交比分层。本面板未接 Cboe 每日期权统计，只给链接。",
     "restricted_reason": "Cboe daily options stats not wired."},
    {"id": "E3_sentiment_surveys", "name": "BofA FMS / AAII / NAAIM", "layer": "E_first_order_sentiment",
     "unit": "text", "status": "missing",
     "interpretation_cn": "机构与散户情绪调查。周/月频，研究报告口径，无免费机器端点。",
     "restricted_reason": "Page itself marks as 缺数据."},
    {"id": "E4_margin_debt_yoy", "name": "FINRA margin debt YoY", "layer": "E_first_order_sentiment",
     "unit": "percent", "status": "pending",
     "link_url": "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics",
     "interpretation_cn": "美股融资余额同比。同比高增表示杠杆参与度上升；转负通常表示去杠杆。月频，FINRA 公布。",
     "restricted_reason": "FINRA public Excel; direct xlsx URL not parsed this run."},
    {"id": "E5_single_day_shock", "name": "QQQ / SOXX single-day shock", "layer": "E_first_order_sentiment",
     "unit": "text", "status": "link_only",
     "interpretation_cn": "科技与半导体 ETF 单日跌幅。需要延迟行情，Yahoo 无商用再分发许可，本面板只给链接。",
     "restricted_reason": "Yahoo delayed quotes; no license."},

    {"id": "F1_nvda_ma", "name": "NVDA vs 50/200d MA", "layer": "F_price_breadth",
     "unit": "USD / percent", "status": "link_only",
     "interpretation_cn": "NVDA 价格相对均线位置。需要个股延迟行情，Cboe/Yahoo 许可未核，本面板不复制价格。",
     "restricted_reason": "No licensed price source."},
    {"id": "F2_sox_drawdown", "name": "PHLX SOX drawdown", "layer": "F_price_breadth",
     "unit": "percent", "status": "link_only",
     "interpretation_cn": "费城半导体指数距一年高点回撤。^SOX 为 Yahoo 代码，无许可。",
     "restricted_reason": "Yahoo ^SOX; no license."},
    {"id": "F3_spx_trend", "name": "S&P 500 vs 50/200d MA", "layer": "F_price_breadth",
     "unit": "index points / percent", "status": "link_only",
     "interpretation_cn": "标普 500 相对均线趋势。Cboe SPX 延迟行情许可未核；SPY ETF 是不同标的，不能替代 SPX。",
     "restricted_reason": "Cboe delayed quotes license not verified."},
    {"id": "F4_rsp_spy_breadth", "name": "Equal-weight vs cap-weight drawdown gap", "layer": "F_price_breadth",
     "unit": "percentage points", "status": "link_only",
     "interpretation_cn": "等权与市值权回撤之差。需要 RSP/SPY ETF 价格，Yahoo 无许可。",
     "restricted_reason": "Yahoo ETF prices; no license."},
    {"id": "F5_cross_market", "name": "KOSPI / JPY carry unwind", "layer": "F_price_breadth",
     "unit": "text", "status": "link_only",
     "interpretation_cn": "韩国综指与日元交叉市场情绪。需要 ^KS11/JPY=X，Yahoo 无许可。",
     "restricted_reason": "Yahoo foreign prices; no license."},

    {"id": "S1_sahm", "name": "Sahm Rule Real-time", "layer": "S_slow_variables",
     "unit": "percentage points", "status": "pending",
     "link_url": "https://fred.stlouisfed.org/series/SAHMREALTIME",
     "interpretation_cn": "Sahm 规则（3 个月失业率均值相对 12 个月低点）。国内 macro 模块负责。",
     "restricted_reason": "Domestic public_macro.py will own."},
    {"id": "S2_nfci", "name": "Chicago Fed NFCI", "layer": "S_slow_variables",
     "unit": "z-score", "status": "pending",
     "link_url": "https://fred.stlouisfed.org/series/NFCI",
     "interpretation_cn": "芝加哥联储全国金融状况指数。国内 macro 模块负责。",
     "restricted_reason": "Domestic public_macro.py will own."},
    {"id": "S3_real_10y", "name": "10Y TIPS real yield", "layer": "S_slow_variables",
     "unit": "percent", "status": "pending",
     "interpretation_cn": "10 年期 TIPS 实际收益率。国内 macro 模块/Treasury 实际曲线负责。",
     "restricted_reason": "Domestic public_macro.py will own."},
    {"id": "S4_term_premium", "name": "ACM 10Y term premium", "layer": "S_slow_variables",
     "unit": "percent", "status": "pending",
     "link_url": "https://fred.stlouisfed.org/series/THREEFYTP10",
     "interpretation_cn": "纽约联储 ACM 期限溢价模型。国内 macro 模块负责。",
     "restricted_reason": "Domestic public_macro.py will own."},
    {"id": "S5_sofr_iorb", "name": "SOFR - IORB funding spread", "layer": "S_slow_variables",
     "unit": "basis points", "status": "pending",
     "link_url": "https://www.newyorkfed.org/markets/reference-rates/sofr",
     "interpretation_cn": "担保隔夜融资利率与准备金利率之差。纽约联储公布，国内 macro 模块负责。",
     "restricted_reason": "Domestic public_macro.py will own."},
    {"id": "S6_brent", "name": "Brent crude", "layer": "S_slow_variables",
     "unit": "USD/barrel", "status": "link_only",
     "interpretation_cn": "布伦特原油价格。BZ=F 为 Yahoo 代码，无许可。",
     "restricted_reason": "Yahoo BZ=F; no license."},
    {"id": "S7_cape", "name": "Shiller CAPE", "layer": "S_slow_variables",
     "unit": "ratio", "status": "missing",
     "link_url": "https://www.multpl.com/shiller-pe",
     "interpretation_cn": "周期调整市盈率。月频，multpl.com 手工更新。",
     "restricted_reason": "Monthly manual."},
]


# ---------------------------------------------------------------------------
# Cached snapshot whitelist
# ---------------------------------------------------------------------------

def _valid_cached_snapshot(m: dict[str, Any]) -> bool:
    if m.get("id") != "A3_yield_curve_nominal":
        return False
    if m.get("status") not in ("connected", "connected_stale"):
        return False
    src = (m.get("source_url") or "")
    if "home.treasury.gov" not in src:
        return False
    lic = (m.get("license") or "").lower()
    if "public domain" not in lic and "us government" not in lic:
        return False
    val = m.get("value")
    if not isinstance(val, dict) or not val:
        return False
    if not all(isinstance(v, (int, float)) for v in val.values()):
        return False
    as_of = m.get("as_of")
    if not isinstance(as_of, str) or len(as_of) != 10 or as_of[4] != "-" or as_of[7] != "-":
        return False
    try:
        d = dt.date.fromisoformat(as_of)
    except ValueError:
        return False
    if d > dt.date.today():
        return False
    return True


def _read_existing_snapshot(output_path: str) -> dict[str, Any] | None:
    try:
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)
    except (OSError, ValueError):
        return None
    if existing.get("schema_version") != "2.1-safe":
        return None
    for m in existing.get("metrics", []):
        if _valid_cached_snapshot(m):
            return {
                "value": m["value"],
                "as_of": m["as_of"],
                "as_of_raw": m.get("as_of_raw"),
                "collected_at": m.get("collected_at"),
                "history": m.get("history", []),
            }
    return None


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def _compute_change(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history or history[0].get("value") is None:
        return {"value": None, "change": None, "change_pct": None}
    latest = history[0]["value"]
    prev = history[1]["value"] if len(history) > 1 and history[1].get("value") is not None else None
    change = (latest - prev) if prev is not None else None
    change_pct = (change / prev * 100) if (prev is not None and prev != 0) else None
    return {"value": latest, "change": change, "change_pct": change_pct}


def build_payload(
    treasury_data: dict[str, Any] | None = None,
    cboe_data: dict[str, dict[str, Any]] | None = None,
    cached_treasury: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if treasury_data is None:
        treasury_data = {"ok": False, "error": "not fetched"}
    if cboe_data is None:
        cboe_data = {}

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    use_stale = (not treasury_data.get("ok")) and bool(cached_treasury)
    cboe_by_sym = {sym: d for sym, d in cboe_data.items() if d.get("ok")}

    metrics: list[dict[str, Any]] = []
    for d in _METRIC_DEFS:
        m = {
            "id": d["id"], "name": d["name"], "layer": d["layer"], "unit": d["unit"],
            "status": d["status"], "value": None, "as_of": None, "as_of_raw": None,
            "collected_at": None, "history": [], "change": None, "change_pct": None,
            "stale": False, "last_attempt_at": None,
            "source_url": d.get("source_url"), "link_url": d.get("link_url"),
            "license": d.get("license"), "license_status": None,
            "restricted_reason": d.get("restricted_reason"),
            "update_frequency": "daily",
            "interpretation_cn": d.get("interpretation_cn", ""),
        }

        if d["id"] == "A3_yield_curve_nominal":
            if treasury_data.get("ok"):
                m["value"] = treasury_data["rates"]
                m["as_of"] = treasury_data["as_of"]
                m["as_of_raw"] = treasury_data.get("as_of_raw")
                m["collected_at"] = treasury_data["collected_at"]
                m["history"] = treasury_data.get("history", [])
                m["source_url"] = TREASURY_NOMINAL_CSV_URL
                m["license"] = TREASURY_LICENSE
                m["license_status"] = "public_domain"
                m["status"] = "connected"
            elif use_stale:
                m["value"] = cached_treasury["value"]
                m["as_of"] = cached_treasury["as_of"]
                m["as_of_raw"] = cached_treasury.get("as_of_raw")
                m["collected_at"] = cached_treasury["collected_at"]
                m["history"] = cached_treasury.get("history", [])
                m["source_url"] = TREASURY_NOMINAL_CSV_URL
                m["license"] = TREASURY_LICENSE
                m["license_status"] = "public_domain"
                m["status"] = "connected_stale"
                m["stale"] = True
                m["last_attempt_at"] = now_iso
                m["restricted_reason"] = f"Live fetch failed; showing last snapshot from {cached_treasury['as_of']}."
            else:
                m["status"] = "failed_no_cache"
                m["last_attempt_at"] = now_iso
                m["restricted_reason"] = f"Live fetch failed: {treasury_data.get('error')}"

        elif "symbol" in d and d["status"] == "connected":
            sym = d["symbol"]
            cd = cboe_by_sym.get(sym)
            if cd:
                m["value"] = cd["value"]
                m["as_of"] = cd["as_of"]
                m["as_of_raw"] = cd.get("as_of_raw")
                m["collected_at"] = cd["collected_at"]
                m["history"] = cd.get("history", [])
                chg = _compute_change(cd.get("history", []))
                m["change"] = chg["change"]
                m["change_pct"] = chg["change_pct"]
                m["source_url"] = CBOE_SYMBOLS[sym]
                m["license"] = CBOE_LICENSE
                m["license_status"] = "public_cdn_with_attribution"
            else:
                m["status"] = "failed_no_cache"
                m["last_attempt_at"] = now_iso
                err = cboe_data.get(sym, {}).get("error", "unknown")
                m["restricted_reason"] = f"Cboe fetch failed: {err}"

        elif d.get("status") == "connected_derived":
            a_sym, b_sym = d["derive_from"]
            a = cboe_by_sym.get(a_sym)
            b = cboe_by_sym.get(b_sym)
            if a and b and a.get("value") is not None and b.get("value") is not None:
                if d["derive_op"] == "ratio":
                    m["value"] = a["value"] / b["value"] if b["value"] != 0 else None
                elif d["derive_op"] == "diff":
                    m["value"] = a["value"] - b["value"]
                m["as_of"] = a["as_of"]
                m["as_of_raw"] = a.get("as_of_raw")
                m["collected_at"] = a["collected_at"]
                a_hist = {h["date"]: h["value"] for h in a.get("history", [])}
                b_hist = {h["date"]: h["value"] for h in b.get("history", [])}
                aligned = []
                for h in a.get("history", []):
                    dt_ = h["date"]
                    bv = b_hist.get(dt_)
                    if bv is not None and h["value"] is not None:
                        if d["derive_op"] == "ratio":
                            v = h["value"] / bv if bv != 0 else None
                        else:
                            v = h["value"] - bv
                        if v is not None:
                            aligned.append({"date": dt_, "value": v})
                m["history"] = aligned
                chg = _compute_change(aligned)
                m["change"] = chg["change"]
                m["change_pct"] = chg["change_pct"]
                m["source_url"] = f"{CBOE_SYMBOLS[a_sym]} + {CBOE_SYMBOLS[b_sym]}"
                m["license"] = CBOE_LICENSE
                m["license_status"] = "public_cdn_with_attribution"
                m["status"] = "connected"
            else:
                m["status"] = "failed_no_cache"
                m["last_attempt_at"] = now_iso
                m["restricted_reason"] = f"Missing derived inputs: {a_sym} or {b_sym} not available"

        metrics.append(m)

    fetch_status = {
        "treasury_nominal": {
            "ok": treasury_data.get("ok", False),
            "degraded": use_stale,
            "error": treasury_data.get("error"),
            "as_of": treasury_data.get("as_of") if treasury_data.get("ok") else (cached_treasury or {}).get("as_of"),
            "last_attempt_at": now_iso,
        },
        "cboe": {sym: {"ok": d.get("ok", False), "error": d.get("error"), "as_of": d.get("as_of")}
                 for sym, d in cboe_data.items()},
    }

    return {
        "schema_version": "2.1-safe",
        "generated_at": now_iso,
        "data_status": "safe_public_with_attribution",
        "policy_notes": [
            "US Treasury data is public domain; Cboe index CSVs are public CDN downloads with attribution.",
            "ICE BofA / LSEG / Yahoo / GEX values are NOT transcribed; link_only only.",
            "No market-regime level, no position sizing, no trading instructions.",
            "Treasury nominal vs real kept as separate metric IDs; TIPS owned by domestic macro module.",
            "On fetch failure, last licensed snapshot preserved with stale=true; different sources fail independently.",
        ],
        "fetch_status": fetch_status,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# FINRA margin debt fetcher
# ---------------------------------------------------------------------------

def fetch_finra_margin() -> dict[str, Any]:
    """Fetch FINRA monthly margin debt xlsx, compute YoY. Returns {ok, value, as_of, history, ...}."""
    url = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
    out: dict[str, Any] = {
        "ok": False, "source_url": url,
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of": None, "value": None, "history": [],
    }
    try:
        import io as _io
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=20).read()
        z = zipfile.ZipFile(_io.BytesIO(data))
        xml = z.read("xl/worksheets/sheet1.xml").decode()
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        root = ET.fromstring(xml)
        rows = []
        for row in root.iter(f"{ns}row"):
            cells = {}
            for c in row.findall(f"{ns}c"):
                ref = c.get("r")
                col = "".join(ch for ch in ref if ch.isalpha())
                is_el = c.find(f"{ns}is/{ns}t")
                v_el = c.find(f"{ns}v")
                if is_el is not None:
                    cells[col] = is_el.text
                elif v_el is not None:
                    cells[col] = v_el.text
                else:
                    cells[col] = None
            rows.append(cells)
        # rows[0] = header; rows[1:] newest first; col A=Year-Month, B=Debit balance ($M)
        data_rows = [r for r in rows[1:] if r.get("A") and r.get("B")]
        if len(data_rows) < 13:
            out["error"] = f"insufficient rows: {len(data_rows)}"
            return out
        latest = data_rows[0]
        prev_year = data_rows[12]  # 12 months back
        latest_val = float(latest["B"])
        prev_val = float(prev_year["B"])
        yoy = (latest_val - prev_val) / prev_val * 100 if prev_val else None
        out["as_of"] = latest["A"]  # YYYY-MM
        out["value"] = yoy
        out["history"] = [
            {"date": r["A"], "value": float(r["B"])} for r in data_rows[:13]
        ]
        out["ok"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Public macro integration (peer module)
# ---------------------------------------------------------------------------

def _try_collect_public_macro() -> dict[str, Any]:
    """Import and call public_macro.collect_public_macro. Return empty dict on failure."""
    try:
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import public_macro  # type: ignore
        return public_macro.collect_public_macro()
    except Exception as e:
        return {"metrics": [], "fetch_status": {}, "errors": [f"public_macro import: {e}"]}


# Mapping from public_macro metric id -> our metric id
_PUBLIC_MACRO_MAP = {
    "ust_10y_real":   "S3_real_10y",
    "acm_tp_10y":     "S4_term_premium",
    "sofr":           "S5_sofr_iorb",  # SOFR value; spread vs IORB still pending
    "ust_10y_nominal": None,  # already covered by A3 full curve; skip
    "bei_10y":        None,  # new independent metric, added below
}


def _merge_public_macro(payload: dict[str, Any], pm: dict[str, Any]) -> None:
    """Overlay public_macro metrics onto payload['metrics'] in place."""
    by_id = {m["id"]: m for m in payload["metrics"]}
    pm_metrics = pm.get("metrics", [])
    bei_metric = None
    for pm_m in pm_metrics:
        pm_id = pm_m["id"]
        if pm_id == "bei_10y":
            bei_metric = pm_m
            continue
        target_id = _PUBLIC_MACRO_MAP.get(pm_id)
        if not target_id or target_id not in by_id:
            continue
        if pm_m.get("value") is None:
            continue  # leave as pending
        tgt = by_id[target_id]
        tgt["value"] = pm_m["value"]
        tgt["as_of"] = pm_m.get("as_of")
        tgt["collected_at"] = pm_m.get("collected_at")
        tgt["history"] = pm_m.get("history", [])
        tgt["source_url"] = pm_m.get("source_url")
        tgt["license"] = pm_m.get("license_url", pm_m.get("license"))
        tgt["license_status"] = "official_source"
        tgt["status"] = "connected"
        tgt["update_frequency"] = pm_m.get("frequency", "daily")
        tgt["restricted_reason"] = None
    # Add BEI as new independent metric (not a risk score)
    if bei_metric and bei_metric.get("value") is not None:
        payload["metrics"].append({
            "id": "S8_bei_10y",
            "name": "10Y Breakeven Inflation (nominal - real, same-day)",
            "layer": "S_slow_variables",
            "unit": "percent",
            "status": "connected",
            "value": bei_metric["value"],
            "as_of": bei_metric.get("as_of"),
            "as_of_raw": None,
            "collected_at": bei_metric.get("collected_at"),
            "history": bei_metric.get("history", []),
            "change": None, "change_pct": None,
            "stale": False, "last_attempt_at": None,
            "source_url": bei_metric.get("source_url"),
            "link_url": None,
            "license": bei_metric.get("license_url"),
            "license_status": "official_source",
            "restricted_reason": None,
            "update_frequency": "daily",
            "interpretation_cn": "10 年期名义国债收益率减 10 年期 TIPS 实际收益率（同日交集）。反映市场隐含长期通胀预期；不是风险评分，也不预测通胀走势。",
        })


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def update_global(output_path: str) -> dict[str, Any]:
    cached = _read_existing_snapshot(output_path)
    treasury = fetch_treasury_nominal_curve()
    finra = fetch_finra_margin()
    pm = _try_collect_public_macro()
    cboe: dict[str, dict[str, Any]] = {}
    payload = build_payload(treasury, cboe, cached_treasury=cached)

    # Merge public_macro
    _merge_public_macro(payload, pm)

    # Overlay FINRA margin debt on E4
    if finra.get("ok"):
        by_id = {m["id"]: m for m in payload["metrics"]}
        e4 = by_id.get("E4_margin_debt_yoy")
        if e4:
            e4["value"] = finra["value"]
            e4["as_of"] = finra["as_of"]
            e4["collected_at"] = finra["collected_at"]
            e4["history"] = finra.get("history", [])
            e4["source_url"] = finra["source_url"]
            e4["license"] = "FINRA margin statistics (public, no fee): https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
            e4["license_status"] = "official_source"
            e4["status"] = "connected"
            e4["update_frequency"] = "monthly"
            e4["restricted_reason"] = None

    # Multi-source cache: for any metric that ended up failed_no_cache but had a
    # connected value in the previous file, promote to connected_stale.
    try:
        with open(output_path, encoding="utf-8") as f:
            prev = json.load(f)
        prev_connected = {m["id"]: m for m in prev.get("metrics", [])
                          if m.get("status") in ("connected", "connected_stale")
                          and m.get("value") is not None}
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for m in payload["metrics"]:
            if m["status"] == "failed_no_cache" and m["id"] in prev_connected:
                pm_old = prev_connected[m["id"]]
                m["value"] = pm_old["value"]
                m["as_of"] = pm_old.get("as_of")
                m["as_of_raw"] = pm_old.get("as_of_raw")
                m["collected_at"] = pm_old.get("collected_at")
                m["history"] = pm_old.get("history", [])
                m["source_url"] = pm_old.get("source_url")
                m["license"] = pm_old.get("license")
                m["license_status"] = pm_old.get("license_status")
                m["status"] = "connected_stale"
                m["stale"] = True
                m["last_attempt_at"] = now_iso
    except (OSError, ValueError):
        pass

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    counts: dict[str, int] = {}
    for m in payload["metrics"]:
        counts[m["status"]] = counts.get(m["status"], 0) + 1

    connected_now = sum(1 for m in payload["metrics"] if m["status"] == "connected")
    stale_now = sum(1 for m in payload["metrics"] if m["status"] == "connected_stale")
    failed_now = sum(1 for m in payload["metrics"] if m["status"] == "failed_no_cache")
    if connected_now >= 4:
        state = "ok"
    elif connected_now >= 1 or stale_now >= 1:
        state = "degraded_partial"
    else:
        state = "failed_no_cache"

    return {
        "ok": live_ok,
        "state": state,
        "path": os.path.abspath(output_path),
        "metrics_total": len(payload["metrics"]),
        "connected": counts.get("connected", 0),
        "connected_stale": counts.get("connected_stale", 0),
        "failed_no_cache": counts.get("failed_no_cache", 0),
        "restricted": counts.get("restricted", 0),
        "link_only": counts.get("link_only", 0),
        "pending": counts.get("pending", 0),
        "missing": counts.get("missing", 0),
        "cboe_connected": cboe_ok,
        "errors": [d.get("error") for d in [treasury] + list(cboe.values()) if d.get("error")],
    }


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Update safe preview-data/global.json")
    p.add_argument("--output", "-o", required=True)
    args = p.parse_args(argv)
    status = update_global(args.output)
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
