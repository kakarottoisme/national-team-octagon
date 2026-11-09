#!/usr/bin/env python3
"""回补七只 ETF 的日成交额历史（新浪 60 分钟 K 按日累加）。

为什么用 60 分钟 K：新浪 getKLineData 的 datalen 上限 1023 根，
- scale=5  → 约 22 个交易日（用于同时点曲线/尾盘占比，见 turnover_baseline.py）
- scale=60 → 4 根/日，约 250 个交易日，每根都带 amount，
  按日求和即为当日二级市场成交额。

口径自检：拿 60 分钟累加的"最近完整交易日合计"与站点 data/live.json 的
turnover.total_amt_yi 对比，差 <1% 才写出，否则报错退出（不静默产出脏数）。

输出 preview-data/turnover-history.json（只服务预览页，不碰正式数据）。
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "preview-data" / "turnover-history.json"
LIVE = ROOT / "data" / "live.json"

CODES = {
    "sh510050": "上证50",
    "sh510300": "沪深300",
    "sz159352": "中证A500",
    "sh510500": "中证500",
    "sh512100": "中证1000",
    "sz159915": "创业板",
    "sh588000": "科创50",
}
MAX_DAYS = 260
API = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/"
       "CN_MarketDataService.getKLineData?symbol={sym}&scale=60&ma=no&datalen=1023")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def fetch(sym):
    req = urllib.request.Request(API.format(sym=sym), headers=UA)
    raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
    m = re.search(r"var x\((\[.*?\])\)", raw, re.S)
    if not m:
        raise RuntimeError(f"{sym}: 新浪返回非预期格式 {raw[:80]}")
    return json.loads(m.group(1))


def daily(bars):
    """按日累加；只用 4 根齐全且收盘到 15:00 的完整交易日。"""
    byday = {}
    for b in bars:
        d = b["day"][:10]
        byday.setdefault(d, []).append(b)
    out = []
    for d, bs in sorted(byday.items()):
        if len(bs) < 4:
            continue
        if max(x["day"][11:16] for x in bs) < "15:00":
            continue
        s = sum(float(x.get("amount") or 0) for x in bs)
        if s <= 0:
            continue
        out.append([d, round(s / 1e8, 3)])
    return out[-MAX_DAYS:]


def main():
    codes, dates = {}, None
    for code, short in CODES.items():
        try:
            bars = fetch(code)
        except Exception as exc:  # 不静默失败：记错并继续别的标的
            codes[code] = {"name": short, "error": str(exc)[:120], "days": []}
            print(f"[warn] {code} {exc}", file=sys.stderr)
            continue
        days = daily(bars)
        codes[code] = {"name": short, "days": days, "bars_span": [bars[0]["day"], bars[-1]["day"]]}

    ok = [c for c in codes.values() if c["days"]]
    if len(ok) < len(CODES):
        print(f"[warn] 仅 {len(ok)}/{len(CODES)} 只取到数据", file=sys.stderr)
    common = None
    for c in ok:
        ds = {d for d, _ in c["days"]}
        common = ds if common is None else (common & ds)
    common = sorted(common or [])
    if not common:
        raise SystemExit("没有共同的完整交易日，回补中止")

    total = []
    for d in common:
        total.append([d, round(sum(dict((x[0], x[1]) for x in c["days"])[d] for c in ok), 3)])

    def avg(series, n):
        vals = [v for _, v in series[-n:]]
        return round(sum(vals) / len(vals), 2) if vals else None

    # 口径自检：与站点当日合计对齐（同源应几乎相等）
    check = {"live_total_yi": None, "hist_total_yi": total[-1][1], "date": total[-1][0], "diff_pct": None}
    try:
        live = json.loads(LIVE.read_text())
        lt = (live.get("turnover") or {}).get("total_amt_yi")
        if lt:
            check["live_total_yi"] = lt
            check["live_date"] = live.get("updated")
            check["diff_pct"] = round((total[-1][1] - lt) / lt * 100, 2)
            if abs(check["diff_pct"]) > 1.0:
                raise SystemExit(
                    f"口径不一致：回补 {total[-1][1]} 亿 vs 站点 {lt} 亿（{check['diff_pct']}%），不写文件")
    except FileNotFoundError:
        pass

    payload = {
        "v": "1",
        "source": "sina CN_MarketDataService.getKLineData scale=60（按日累加，含 amount）",
        "note": "二级市场成交额；七只合计按共同交易日求和；不含大宗/协议转让，不代表申赎或任何机构身份",
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "window": [total[0][0], total[-1][0]],
        "n_days": len(total),
        "codes": codes,
        "total_days": total,
        "avg20": avg(total, 20),
        "avg60": avg(total, 60),
        "check": check,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"OK {OUT} days={len(total)} {total[0][0]}~{total[-1][0]} "
          f"avg20={payload['avg20']} avg60={payload['avg60']} check={check}")
    for code, c in codes.items():
        if c.get("error"):
            print(f"  error {code}: {c['error']}", file=sys.stderr)
        else:
            print(f"  {code} {c['name']} {len(c['days'])} 日")


if __name__ == "__main__":
    main()
