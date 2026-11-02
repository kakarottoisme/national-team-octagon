"""两融余额 + 指数日线 -> data/margin_balance.json

优先用金十宏观沪/深序列；深市缺失交易日用深交所 stock_margin_szse 按日补齐，
对应沪市用上交所 stock_margin_sse 取值后相加。
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

import akshare as ak
import pandas as pd

OUT = Path(__file__).resolve().parent / "data" / "margin_balance.json"
IDX = {
    "创业板指": "sz399006",
    "科创50": "sh000688",
    "中证500": "sh000905",
    "中证1000": "sh000852",
    "沪深300": "sh000300",
    "上证50": "sh000016",
}


def _norm_macro(df: pd.DataFrame) -> pd.Series:
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    return (df.set_index("日期")["融资融券余额"].astype(float) / 1e8).sort_index()


def _sse_map(start: str, end: str) -> dict[str, float]:
    df = ak.stock_margin_sse(start_date=start, end_date=end)
    d = pd.to_datetime(df["信用交易日期"].astype(str)).dt.strftime("%Y-%m-%d")
    v = df["融资融券余额"].astype(float) / 1e8
    return dict(zip(d, v))


def _fill_missing(total: dict[str, float], sh: pd.Series, sz: pd.Series) -> dict[str, float]:
    miss = sorted(set(sh.index) - set(sz.index))
    if not miss:
        return total
    start = miss[0].replace("-", "")
    end = miss[-1].replace("-", "")
    try:
        sh_map = _sse_map(start, end)
    except Exception as e:
        print("sse map fail", e)
        sh_map = {}
    ok = fail = 0
    for i, d in enumerate(miss, 1):
        if d not in sh_map and d in sh.index:
            sh_map[d] = float(sh.loc[d])
        if d not in sh_map:
            fail += 1
            continue
        try:
            row = ak.stock_margin_szse(date=d.replace("-", ""))
            if row is None or len(row) == 0:
                fail += 1
                continue
            total[d] = round(float(sh_map[d]) + float(row["融资融券余额"].iloc[0]), 2)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.2 if i % 10 else 0.5)
    print(f"sz fill need={len(miss)} ok={ok} fail={fail}")
    return total


def margin() -> dict[str, float]:
    sh = _norm_macro(ak.macro_china_market_margin_sh())
    try:
        sz = _norm_macro(ak.macro_china_market_margin_sz())
    except Exception as e:
        print("sz macro fail, using empty then fill", e)
        sz = pd.Series(dtype=float)
    both = pd.concat([sh.rename("sh"), sz.rename("sz")], axis=1, join="inner").sum(axis=1)
    total = {d: round(float(v), 2) for d, v in both.sort_index().items()}
    total = _fill_missing(total, sh, sz)
    return {k: total[k] for k in sorted(total)}


def idx(code: str) -> dict[str, float]:
    df = ak.stock_zh_index_daily(symbol=code)
    return {
        pd.Timestamp(d).strftime("%Y-%m-%d"): round(float(c), 2)
        for d, c in zip(df["date"], df["close"])
    }


def main() -> None:
    m = margin()
    out = {
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "codes": IDX,
        "margin": m,
        "index": {k: idx(c) for k, c in IDX.items()},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("margin", min(m), max(m), len(m))


if __name__ == "__main__":
    main()
