"""global_sources.py — Safe public-data source module for the global risk preview.

Scope (per parent agent, 2026-11-04):
- Only US-government public-domain data is auto-fetched and written into preview-data/global.json.
- ICE BofA OAS (HY/IG), MOVE, Cboe indices (license unverified), Yahoo prices, CDS,
  GEX, sentiment surveys are NOT replicated. They appear in the payload with
  status=restricted / link_only / pending and value=null.
- No network calls on import. `update_global(output_path)` is the only entry point
  that touches the network. Safe to `from preview_tools.global_sources import update_global`.
- No FRED API key required. No secrets.
- Treasury nominal vs real yield curves are NEVER mixed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import urllib.request
import urllib.error
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
TREASURY_REAL_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/daily-treasury-rates.csv/2026/all"
    "?type=daily_treasury_real_yield_curve"
    "&field_tdr_date_value=2026&page&_format=csv"
)

# Canonical 28 main + 7 slow definitions. Values are intentionally null except
# for the one source we can legally auto-pull this round.
# status values:
#   connected     — value auto-fetched from a verified public-domain source this run
#   restricted    — data exists but reproduction not licensed for public GitHub Pages output
#   link_only     — show a link to the authoritative page; do not transcribe the number
#   pending       — source identified but license/network not yet verified this run
#   missing       — page itself marks no data (e.g. surveys, CAPE)

_METRIC_DEFS: list[dict[str, Any]] = [
    # A. Macro rates
    {"id": "A1_move", "name": "MOVE (ICE BofA Treasury Volatility)", "layer": "A_macro_rates",
     "unit": "index points", "status": "link_only",
     "link_url": "https://www.theice.com/marketdata/indices/indices-dashboard/move-index",
     "restricted_reason": "ICE private index; no free public feed. Page uses VXTLT regression estimate (R²=0.833) which is not an ICE print. Do not transcribe."},
    {"id": "A1b_vxtlt", "name": "CBOE 10-Year Treasury Volatility (VXTLT)", "layer": "A_macro_rates",
     "unit": "index points", "status": "link_only",
     "link_url": "https://www.cboe.com/us/indices/dashboard/VXTLT/",
     "restricted_reason": "Cboe index; public CSV download exists but Cboe redistribution license not verified for GitHub Pages. Do not equate with MOVE."},
    {"id": "A2_move_vix_ratio", "name": "MOVE / VIX ratio (derived)", "layer": "A_macro_rates",
     "unit": "ratio", "status": "restricted",
     "restricted_reason": "Derived from A1_move which is link_only; no legal input to compute."},
    {"id": "A3_yield_curve_nominal", "name": "US Treasury Nominal Yield Curve (daily)", "layer": "A_macro_rates",
     "unit": "percent", "status": "connected",
     "source_url": TREASURY_NOMINAL_CSV_URL,
     "license": "US Government work — public domain. https://home.treasury.gov/"},
    {"id": "A3b_yield_curve_real", "name": "US Treasury Real (TIPS) Yield Curve (daily)", "layer": "A_macro_rates",
     "unit": "percent", "status": "pending",
     "source_url": TREASURY_REAL_CSV_URL,
     "restricted_reason": "US Government public domain, but network fetch timed out this run; not auto-connected."},
    {"id": "A4_fed_path", "name": "Fed policy path (FOMC)", "layer": "A_macro_rates",
     "unit": "text", "status": "link_only",
     "link_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
     "restricted_reason": "Event-judgment; no machine-readable status."},

    # B. Credit
    {"id": "B1_hy_oas", "name": "ICE BofA US High Yield OAS", "layer": "B_credit",
     "unit": "percent", "status": "restricted",
     "link_url": "https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
     "restricted_reason": "ICE Data Indices copyright; FRED terms prohibit third-party reproduction without written ICE permission."},
    {"id": "B2_ig_oas", "name": "ICE BofA US Corporate (IG) OAS", "layer": "B_credit",
     "unit": "percent", "status": "restricted",
     "link_url": "https://fred.stlouisfed.org/series/BAMLC0A0CM",
     "restricted_reason": "Same ICE restriction as B1."},
    {"id": "B3_tech_cds_5y", "name": "Tech mega-cap 5Y CDS (NVDA/Meta/AMZN/GOOG/MSFT)", "layer": "B_credit",
     "unit": "basis points", "status": "link_only",
     "link_url": "https://www.lseg.com/en/market-data",
     "restricted_reason": "LSEG/Bloomberg paid; manual chart-reading."},
    {"id": "B4_ai_chain_credit", "name": "AI-chain credit (CoreWeave/Oracle)", "layer": "B_credit",
     "unit": "text", "status": "link_only",
     "restricted_reason": "News/research clip; no machine feed."},
    {"id": "B5_ai_cds_volume", "name": "AI-issuer CDS weekly notional", "layer": "B_credit",
     "unit": "USD billions", "status": "link_only",
     "restricted_reason": "Morgan Stanley / FT research; manual."},

    # C. Second-order vol
    {"id": "C1_skew", "name": "CBOE SKEW", "layer": "C_second_order_vol",
     "unit": "index points", "status": "link_only",
     "link_url": "https://www.cboe.com/us/indices/dashboard/skew/",
     "restricted_reason": "Cboe index; redistribution license not verified."},
    {"id": "C2_vvix", "name": "CBOE VVIX", "layer": "C_second_order_vol",
     "unit": "index points", "status": "link_only",
     "link_url": "https://www.cboe.com/us/indices/dashboard/vvix/",
     "restricted_reason": "Cboe index; license not verified."},
    {"id": "C3_vix_term", "name": "VIX / VIX3M term ratio", "layer": "C_second_order_vol",
     "unit": "ratio", "status": "link_only",
     "link_url": "https://www.cboe.com/us/indices/dashboard/vix/",
     "restricted_reason": "Derived from Cboe indices; license not verified."},
    {"id": "C4_vix9d_vix", "name": "VIX9D / VIX ratio", "layer": "C_second_order_vol",
     "unit": "ratio", "status": "link_only",
     "restricted_reason": "Cboe indices; license not verified."},
    {"id": "C5_putcall_skew_scissor", "name": "P/C 63d vs SKEW 5d scissor", "layer": "C_second_order_vol",
     "unit": "text", "status": "link_only",
     "link_url": "https://www.cboe.com/us/market_statistics/daily/",
     "restricted_reason": "Cboe daily options statistics; CSV endpoint not confirmed this run."},

    # D. Market-maker structure
    {"id": "D1_gex", "name": "Dealer Gamma Exposure (GEX)", "layer": "D_mm_structure",
     "unit": "USD billions", "status": "link_only",
     "link_url": "https://squeezemetrics.com/monitor",
     "restricted_reason": "Vendor estimate (DDOI); does not include all 0DTE. Free API endpoint returned 502/404 this run. Not market-wide."},
    {"id": "D2_cor1m", "name": "CBOE 1-Month Implied Correlation", "layer": "D_mm_structure",
     "unit": "index points", "status": "link_only",
     "link_url": "https://www.cboe.com/us/indices/dashboard/cor1m/",
     "restricted_reason": "Cboe index; license not verified."},
    {"id": "D3_vixeq_vix", "name": "VIXEQ / VIX tech crowding", "layer": "D_mm_structure",
     "unit": "ratio", "status": "link_only",
     "restricted_reason": "Cboe 'if public' + LSEG manual."},
    {"id": "D4_vxn_vix", "name": "VXN - VIX tech premium", "layer": "D_mm_structure",
     "unit": "index points", "status": "link_only",
     "restricted_reason": "Derived from Cboe VXN/VIX; license not verified."},

    # E. First-order sentiment
    {"id": "E1_vix", "name": "CBOE VIX", "layer": "E_first_order_sentiment",
     "unit": "index points", "status": "link_only",
     "link_url": "https://www.cboe.com/us/indices/dashboard/vix/",
     "restricted_reason": "Cboe index; license not verified."},
    {"id": "E2_putcall_structure", "name": "Put/Call structure (index vs equity)", "layer": "E_first_order_sentiment",
     "unit": "text", "status": "link_only",
     "restricted_reason": "Cboe daily options statistics; not auto-connected."},
    {"id": "E3_sentiment_surveys", "name": "BofA FMS / AAII / NAAIM", "layer": "E_first_order_sentiment",
     "unit": "text", "status": "missing",
     "restricted_reason": "Page itself marks as 缺数据 this cycle."},
    {"id": "E4_margin_debt_yoy", "name": "FINRA margin debt YoY", "layer": "E_first_order_sentiment",
     "unit": "percent", "status": "pending",
     "link_url": "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics",
     "restricted_reason": "FINRA provides public Excel download; direct xlsx URL not parsed this run."},
    {"id": "E5_single_day_shock", "name": "QQQ / SOXX single-day shock", "layer": "E_first_order_sentiment",
     "unit": "text", "status": "link_only",
     "restricted_reason": "Yahoo delayed quotes; no commercial redistribution license."},

    # F. Price breadth
    {"id": "F1_nvda_ma", "name": "NVDA vs 50/200d MA", "layer": "F_price_breadth",
     "unit": "USD / percent", "status": "link_only",
     "restricted_reason": "Yahoo price; no license. Cboe delayed quotes license not verified."},
    {"id": "F2_sox_drawdown", "name": "PHLX Semiconductor (SOX) drawdown", "layer": "F_price_breadth",
     "unit": "percent", "status": "link_only",
     "restricted_reason": "Yahoo ^SOX; no license."},
    {"id": "F3_spx_trend", "name": "S&P 500 vs 50/200d MA", "layer": "F_price_breadth",
     "unit": "index points / percent", "status": "link_only",
     "restricted_reason": "Cboe SPX delayed quotes; license not verified. SPY ETF is not an official SPX substitute."},
    {"id": "F4_rsp_spy_breadth", "name": "Equal-weight minus cap-weight drawdown gap", "layer": "F_price_breadth",
     "unit": "percentage points", "status": "link_only",
     "restricted_reason": "Yahoo ETF prices; no license."},
    {"id": "F5_cross_market", "name": "KOSPI / JPY carry unwind", "layer": "F_price_breadth",
     "unit": "text", "status": "link_only",
     "restricted_reason": "Yahoo ^KS11 / JPY=X; no license."},

    # S. Slow variables
    {"id": "S1_sahm", "name": "Sahm Rule Real-time", "layer": "S_slow_variables",
     "unit": "percentage points", "status": "pending",
     "link_url": "https://fred.stlouisfed.org/series/SAHMREALTIME",
     "restricted_reason": "US Fed-derived; FRED redistribution terms not cleared this run."},
    {"id": "S2_nfci", "name": "Chicago Fed NFCI", "layer": "S_slow_variables",
     "unit": "z-score", "status": "pending",
     "link_url": "https://fred.stlouisfed.org/series/NFCI",
     "restricted_reason": "Chicago Fed series; FRED terms not cleared this run."},
    {"id": "S3_real_10y", "name": "10Y TIPS real yield", "layer": "S_slow_variables",
     "unit": "percent", "status": "pending",
     "source_url": TREASURY_REAL_CSV_URL,
     "restricted_reason": "Same as A3b; Treasury real curve not auto-connected this run."},
    {"id": "S4_term_premium", "name": "ACM 10Y term premium", "layer": "S_slow_variables",
     "unit": "percent", "status": "link_only",
     "link_url": "https://fred.stlouisfed.org/series/THREEFYTP10",
     "restricted_reason": "Fed/ACM series via FRED; terms not cleared."},
    {"id": "S5_sofr_iorb", "name": "SOFR - IORB funding spread", "layer": "S_slow_variables",
     "unit": "basis points", "status": "pending",
     "link_url": "https://www.newyorkfed.org/markets/reference-rates/sofr",
     "restricted_reason": "NY Fed SOFR is public; direct NY Fed CSV endpoint not wired this run (FRED mirror terms not cleared)."},
    {"id": "S6_brent", "name": "Brent crude", "layer": "S_slow_variables",
     "unit": "USD/barrel", "status": "link_only",
     "restricted_reason": "Yahoo BZ=F; no license."},
    {"id": "S7_cape", "name": "Shiller CAPE", "layer": "S_slow_variables",
     "unit": "ratio", "status": "missing",
     "link_url": "https://www.multpl.com/shiller-pe",
     "restricted_reason": "Page itself marks as 缺数据; monthly manual."},
]

# ---------------------------------------------------------------------------
# Fetchers (only these touch the network)
# ---------------------------------------------------------------------------

def _normalize_iso_date(raw: str | None) -> str | None:
    """Convert Treasury CSV 'MM/DD/YYYY' to 'YYYY-MM-DD'. Pass through if already ISO.
    Returns None if input is None/blank."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Already ISO?
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return raw
    try:
        m, d, y = raw.split("/")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return raw


def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "national-team-octagon/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_treasury_nominal_curve() -> dict[str, Any]:
    """Fetch the latest row from Treasury daily nominal yield curve CSV.

    Returns dict:
      ok, source_url, collected_at (UTC ISO)
      as_of (data date, normalized to YYYY-MM-DD)
      as_of_raw (original CSV date string, for audit)
      rates: {tenor: percent}
      error?
    Never raises on network error.
    """
    out: dict[str, Any] = {
        "ok": False,
        "source_url": TREASURY_NOMINAL_CSV_URL,
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of": None,
        "as_of_raw": None,
    }
    try:
        raw = _http_get(TREASURY_NOMINAL_CSV_URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        out["error"] = f"network: {type(e).__name__}: {e}"
        return out
    try:
        reader = csv.DictReader(io.StringIO(raw))
        rows = [r for r in reader if r.get("Date")]
        if not rows:
            out["error"] = "empty CSV"
            return out
        latest = rows[0]  # Treasury CSV is newest-first
        out["as_of_raw"] = latest["Date"]
        out["as_of"] = _normalize_iso_date(latest["Date"])
        rates: dict[str, float] = {}
        for k, v in latest.items():
            if k == "Date":
                continue
            try:
                rates[k.strip()] = float(v)
            except (TypeError, ValueError):
                continue
        out["rates"] = rates
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"parse: {type(e).__name__}: {e}"
    return out


def _valid_cached_snapshot(m: dict[str, Any]) -> bool:
    """Whitelist + integrity check for a cached A3 row.

    Accepts only: id=A3_yield_curve_nominal, status in {connected, connected_stale},
    source_url containing home.treasury.gov, license mentioning public domain,
    value non-empty dict of {tenor: numeric}, as_of valid YYYY-MM-DD and not in future.
    Any legacy / tampered / restricted row is rejected (returns False).
    """
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
    """Load existing global.json and return the last valid A3 snapshot.

    Accepts status in {connected, connected_stale} so consecutive failures keep value.
    Rejects legacy / tampered / restricted rows via _valid_cached_snapshot().
    """
    try:
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)
    except (OSError, ValueError):
        return None
    # Only consider rows whose schema_version is our safe one
    if existing.get("schema_version") != "2.0-safe":
        return None
    for m in existing.get("metrics", []):
        if _valid_cached_snapshot(m):
            return {
                "value": m["value"],
                "as_of": m["as_of"],
                "as_of_raw": m.get("as_of_raw"),
                "collected_at": m.get("collected_at"),
            }
    return None


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def build_payload(
    treasury_data: dict[str, Any] | None = None,
    cached_treasury: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the safe global.json payload. Pure function, no network.

    If treasury_data.ok is False and cached_treasury has a prior connected snapshot,
    A3 is marked status='connected_stale' with stale=true, last_attempt_at=now,
    preserving the original as_of/collected_at.
    """
    if treasury_data is None:
        treasury_data = {"ok": False, "error": "not fetched"}

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    use_stale = (not treasury_data.get("ok")) and bool(cached_treasury)

    metrics: list[dict[str, Any]] = []
    for d in _METRIC_DEFS:
        m = {
            "id": d["id"],
            "name": d["name"],
            "layer": d["layer"],
            "unit": d["unit"],
            "status": d["status"],
            "value": None,
            "as_of": None,
            "as_of_raw": None,
            "collected_at": None,
            "stale": False,
            "last_attempt_at": None,
            "source_url": d.get("source_url"),
            "link_url": d.get("link_url"),
            "license": d.get("license"),
            "restricted_reason": d.get("restricted_reason"),
        }
        if d["id"] == "A3_yield_curve_nominal":
            if treasury_data.get("ok"):
                m["value"] = treasury_data["rates"]
                m["as_of"] = treasury_data["as_of"]
                m["as_of_raw"] = treasury_data.get("as_of_raw")
                m["collected_at"] = treasury_data["collected_at"]
                m["status"] = "connected"
            elif use_stale:
                m["value"] = cached_treasury["value"]
                m["as_of"] = cached_treasury["as_of"]
                m["as_of_raw"] = cached_treasury.get("as_of_raw")
                m["collected_at"] = cached_treasury["collected_at"]
                m["status"] = "connected_stale"
                m["stale"] = True
                m["last_attempt_at"] = now_iso
                m["restricted_reason"] = (
                    f"Live fetch failed this run (error: {treasury_data.get('error')}); "
                    f"showing last licensed snapshot from {cached_treasury['as_of']}."
                )
            else:
                # Failure with no valid cache: do NOT claim connected.
                m["status"] = "failed_no_cache"
                m["last_attempt_at"] = now_iso
                m["restricted_reason"] = (
                    f"Live fetch failed this run (error: {treasury_data.get('error')}); "
                    f"no prior licensed snapshot on disk. UI should render link_url."
                )
        metrics.append(m)

    return {
        "schema_version": "2.0-safe",
        "generated_at": now_iso,
        "data_status": "safe_public_only",
        "policy_notes": [
            "Only US-government public-domain data is auto-fetched.",
            "ICE BofA / Cboe / Yahoo / CDS / GEX values are NOT replicated to this public JSON.",
            "Restricted/link_only/pending entries keep value=null; UI should render link_url.",
            "No market-regime level, no position sizing, no trading instructions.",
            "Treasury nominal and real curves are kept as separate metric IDs and never mixed.",
            "On live-fetch failure, last licensed Treasury snapshot is preserved with stale=true.",
        ],
        "fetch_status": {
            "treasury_nominal": {
                "ok": treasury_data.get("ok", False),
                "degraded": use_stale,
                "error": treasury_data.get("error"),
                "as_of": treasury_data.get("as_of") if treasury_data.get("ok") else (cached_treasury or {}).get("as_of"),
                "as_of_raw": treasury_data.get("as_of_raw") if treasury_data.get("ok") else (cached_treasury or {}).get("as_of_raw"),
                "collected_at": treasury_data.get("collected_at") if treasury_data.get("ok") else (cached_treasury or {}).get("collected_at"),
                "last_attempt_at": now_iso,
            }
        },
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def update_global(output_path: str) -> dict[str, Any]:
    """Fetch allowed public sources and write the safe global.json.

    On live-fetch failure, reads output_path for a prior connected A3 snapshot and
    re-emits it with status='connected_stale'. Restricted/link_only values are never
    carried over (they are always null).

    Returns a status dict: {ok, path, state, metrics_total, connected, connected_stale,
    restricted, link_only, pending, missing, errors}.
    Never raises.
    """
    cached = _read_existing_snapshot(output_path)
    treasury = fetch_treasury_nominal_curve()
    payload = build_payload(treasury, cached_treasury=cached)

    import os
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    counts: dict[str, int] = {}
    for m in payload["metrics"]:
        counts[m["status"]] = counts.get(m["status"], 0) + 1

    live_ok = treasury.get("ok", False)
    stale_preserved = (not live_ok) and bool(cached)
    if live_ok:
        state = "ok"
    elif stale_preserved:
        state = "degraded_stale_cached"
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
        "errors": [treasury.get("error")] if treasury.get("error") else [],
    }


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Update safe preview-data/global.json")
    p.add_argument("--output", "-o", required=True, help="Output JSON path")
    args = p.parse_args(argv)
    status = update_global(args.output)
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0 if status["ok"] else 0  # still exit 0 so workflow doesn't fail on degraded sources


if __name__ == "__main__":
    raise SystemExit(_cli())
