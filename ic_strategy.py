#!/usr/bin/env python3
"""中证500 IC 只做多收盘信号 → data/ic_strategy.json

正式信号用中证500指数收盘。期货用对应合约的收盘价（不是结算价）。

数据源（免费、无需密钥）:
  指数  新浪 sh000905 日K收盘
        CN_MarketDataService.getKLineData scale=240
  期货  新浪 InnerFuturesNewService.getDailyKLine 字段 c
        与中金所日行情「今收盘」一致；跑数当天若中金所 CSV 已发布，用它核对并覆盖当日
日历  有指数收盘的日期视为交易日。最后交易日 = 合约月份第三个星期五，
      遇周末或已知休市顺延到下一交易日。

规则摘要见页面「最终规则」。本文件只计算，不给出交易指令。

D 腿（盈利覆盖加仓）叠在 A+B+C 的底仓上，用指数收盘把全历史重放一遍：
累计盈亏按上一日（底仓 + D）× 指数涨跌 × 200。自上次 D 撤出以来的盈利，
有 50% 够付 1 手 12% 保证金就加 1 手，D 最多 4，底仓 + D 不超过 10。
收盘低于 MA250×0.99，或底仓为 0，D 归零并把累计盈亏记为新的起点。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend_io import write_text_atomic

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "ic_strategy.json"
MULTIPLIER = 200
MA_N = 250
GRID = (5000, 5250, 5500, 6000, 6500)
OPEN_GAP = 0.03
SWITCH_GAP = 0.02
MIN_DAYS = 10
# D 腿：盈利的 50% 覆盖 12% 保证金才加仓，最多 4 手，底仓 + D 不超过 10。
D_FRAC = 0.5
D_MAX = 4
D_MARGIN = 0.12
# 指数窗口之外、脚本还要用到的休市（元旦）。已过去的休市从指数缺口回填。
FUTURE_HOLIDAYS = {"2027-01-01"}
SINA_UA = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
}
INDEX_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/"
    "CN_MarketDataService.getKLineData?symbol=sh000905&scale=240&ma=no&datalen=1023"
)
FUT_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{code}=/"
    "InnerFuturesNewService.getDailyKLine?symbol={code}"
)
CFFEX_URL = "http://www.cffex.com.cn/sj/hqsj/rtj/{ym}/{dd}/{ymd}_1.csv"
ROLE_LABEL = {"near": "当月", "next": "次月", "quarter": "当季", "far": "下季"}


def clip_lots(n: int) -> int:
    return max(0, min(10, int(n)))


def leg_a(close: float) -> int:
    if close <= 5000:
        return 10
    if close <= 5250:
        return 8
    if close <= 5500:
        return 6
    if close <= 6000:
        return 4
    if close <= 6500:
        return 2
    return 0


def next_b(prev: int, close: float, ma: float) -> int:
    if close > ma * 1.01:
        return 2
    if close < ma * 0.99:
        return 0
    return prev


def c_from_basis(ann: float) -> int:
    if ann >= 0.10:
        return 2
    if ann < 0.03:
        return -2
    return 0


def ann_discount(index: float, futures: float, days: int) -> float | None:
    if index <= 0 or days <= 0:
        return None
    return (index - futures) / index * 365 / days


def signed(n: int) -> str:
    n = int(n)
    if n > 0:
        return f"+{n}"
    if n < 0:
        return f"−{abs(n)}"
    return "0"


def leg_formula(a: int, b: int, c: int, d: int) -> str:
    """A2 + B0 + C2 + D0。负数把减号放在字母前：A2 + B0 − C2 + D0。"""
    def piece(letter: str, n: int, first: bool) -> str:
        n = int(n)
        body = f"{letter}{abs(n)}"
        if n < 0:
            return f"− {body}"
        if first:
            return body
        return f"+ {body}"

    return " ".join((
        piece("A", a, True),
        piece("B", b, False),
        piece("C", c, False),
        piece("D", d, False),
    ))


def add_months(year: int, month: int, n: int) -> tuple[int, int]:
    z = year * 12 + (month - 1) + n
    return z // 12, z % 12 + 1


def contract_code(year: int, month: int) -> str:
    return f"IC{year % 100:02d}{month:02d}"


def parse_code(code: str) -> tuple[int, int]:
    return 2000 + int(code[2:4]), int(code[4:6])


def third_friday(year: int, month: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (4 - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 14)


def last_trade_day(year: int, month: int, holidays: set[str]) -> dt.date:
    d = third_friday(year, month)
    for _ in range(14):
        if d.weekday() >= 5 or d.isoformat() in holidays:
            d += dt.timedelta(days=1)
            continue
        return d
    return d


def prev_trading_day(day: dt.date, holidays: set[str]) -> dt.date:
    d = day - dt.timedelta(days=1)
    for _ in range(14):
        if d.weekday() < 5 and d.isoformat() not in holidays:
            return d
        d -= dt.timedelta(days=1)
    return d


def infer_holidays(trading_days: list[str]) -> set[str]:
    if not trading_days:
        return set(FUTURE_HOLIDAYS)
    start = dt.date.fromisoformat(trading_days[0])
    end = dt.date.fromisoformat(trading_days[-1])
    have = set(trading_days)
    gaps = set()
    d = start
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in have:
            gaps.add(d.isoformat())
        d += dt.timedelta(days=1)
    return gaps | set(FUTURE_HOLIDAYS)


def list_slots(asof: dt.date, holidays: set[str]) -> list[dict]:
    y, m = asof.year, asof.month
    if asof > last_trade_day(y, m, holidays):
        y, m = add_months(y, m, 1)
    front = (y, m)
    nxt = add_months(y, m, 1)
    quarters: list[tuple[int, int]] = []
    cy, cm = nxt
    while len(quarters) < 2:
        cy, cm = add_months(cy, cm, 1)
        if cm in (3, 6, 9, 12):
            quarters.append((cy, cm))
    rows = []
    for role, ym in (("near", front), ("next", nxt), ("quarter", quarters[0]), ("far", quarters[1])):
        ltd = last_trade_day(ym[0], ym[1], holidays)
        rows.append({
            "role": role,
            "label": ROLE_LABEL[role],
            "code": contract_code(*ym),
            "expiry": ltd.isoformat(),
            "days": (ltd - asof).days,
            "ltd": ltd,
        })
    return rows


def b_trigger_prices(sum250: float, drop_close: float) -> tuple[float, float]:
    """下一交易日收盘 T 滚入均线后，使 T = k * MA(T) 的解。

    MA(T) = (sum250 - drop_close + T) / 250
    站上解用 k=1.01（收盘需严格高于该价，B 才变为 +2）
    跌破解用 k=0.99（收盘需严格低于该价，B 才变为 0）
    """
    base = sum250 - drop_close
    on_above = 1.01 * base / (MA_N - 1.01)
    off_below = 0.99 * base / (MA_N - 0.99)
    return on_above, off_below


def _fmt_level(level: float, grid: bool) -> str:
    if grid:
        return str(int(level)) if float(level).is_integer() else f"{level:g}"
    return f"{level:,.2f}"


def _leg_effect(name: str, old: int, new: int, old_total: int, new_total: int) -> str:
    if new > old:
        leg = f"{name}升到 {new} 手"
    elif new < old:
        leg = f"{name}降到 {new} 手"
    else:
        leg = f"{name}维持 {new} 手"
    if new_total > old_total:
        tot = f"合计加到 {new_total} 手"
    elif new_total < old_total:
        tot = f"合计减到 {new_total} 手"
    else:
        tot = f"合计仍为 {new_total} 手"
    return f"{leg}，{tot}"


def _near_note(close: float, level: float) -> str:
    if level <= 0:
        return ""
    pct = abs(close - level) / level * 100
    if pct <= 0.15:
        return f"（距现价 {pct:.2f}%，收盘后请再核对）"
    return ""


def advance_d(lots: int, cum: float, cum0: float, prev_base: int, prev_close: float | None,
              base: int, close: float, ma: float) -> tuple[int, float, float]:
    """重放一天 D 腿。盈亏用上一日底仓和 D，撤出与加仓用当日底仓和收盘。

    与回测同一顺序：先记盈亏，底仓归零或收盘跌破 MA250×0.99 则 D 归零并重置起点，
    然后按 50% 盈利覆盖 12% 保证金加仓。D 不超过 4，也不让底仓 + D 超过 10。
    """
    if prev_close is not None:
        cum += (prev_base + lots) * (close - prev_close) * MULTIPLIER
    if base <= 0 and lots > 0:
        lots = 0
        cum0 = cum
    if lots > 0 and close < ma * 0.99:
        lots = 0
        cum0 = cum
    unit = D_MARGIN * close * MULTIPLIER
    avail = D_FRAC * max(cum - cum0, 0.0)
    tgt = min(D_MAX, int(avail // unit)) if unit > 0 else 0
    room = max(0, 10 - int(base))
    tgt = min(tgt, room)
    if tgt > lots:
        lots = tgt
    if lots > room:
        lots = room
    return lots, cum, cum0


def d_next_yuan(lots: int, cum: float, cum0: float, base: int, close: float) -> float | None:
    """再加 1 手还差多少累计盈利（元）。已到 D 上限或总手数上限时返回 None。"""
    cap = min(D_MAX, max(0, 10 - int(base)))
    if lots >= cap:
        return None
    unit = D_MARGIN * close * MULTIPLIER
    if unit <= 0:
        return None
    # 触发线是累计盈利（可为负）达到 (D+1) 手保证金 / 50%。还需金额含把亏损赚回来的部分。
    target_profit = (lots + 1) * unit / D_FRAC
    need = target_profit - (cum - cum0)
    if need < 0:
        need = 0.0
    return need


def _d_after_base(base: int, leg_d: int, ma_exit: bool) -> int:
    """触发价只改一条底仓腿时，D 保持不变，除非撤出或被 10 手上限挤掉。不加仓。"""
    if ma_exit or base <= 0:
        return 0
    return min(max(0, int(leg_d)), D_MAX, max(0, 10 - int(base)))


def _total_with_d(a: int, b: int, c: int, leg_d: int, ma_exit: bool = False) -> tuple[int, int]:
    base = clip_lots(a + b + c)
    d = _d_after_base(base, leg_d, ma_exit)
    return d, min(10, base + d)


def _with_breakdown(effect: str, a: int, b: int, c: int, d: int) -> str:
    """在「合计」前写上 A/B/C/D，避免只看到一串没有名字的数字。"""
    br = leg_formula(a, b, c, d)
    token = "，合计"
    if token in effect:
        return effect.replace(token, f"，{br}{token}", 1)
    return f"{effect}，{br}"


def _with_d_note(effect: str, old_d: int, new_d: int) -> str:
    if new_d == old_d:
        return effect
    if new_d <= 0:
        note = "D 腿撤出"
    elif new_d < old_d:
        note = f"D 腿减到 {new_d} 手"
    else:
        note = f"D 腿加到 {new_d} 手"
    head, sep, tail = effect.partition("，")
    if not sep:
        return f"{effect}，{note}"
    return f"{head}，{note}，{tail}"


def trigger_rows(close: float, leg_a_now: int, leg_b_now: int, leg_c_now: int,
                 on_above: float, off_below: float, next_roll: str | None,
                 c_code: str | None, c_basis_now: float | None, leg_d: int = 0) -> list[dict]:
    rows = []
    d_now = max(0, int(leg_d))
    _, target = _total_with_d(leg_a_now, leg_b_now, leg_c_now, d_now, False)
    below = [lv for lv in GRID if lv < close]
    if below:
        lv = max(below)
        new_a = leg_a(lv)
        if new_a != leg_a_now:
            new_d, new_total = _total_with_d(new_a, leg_b_now, leg_c_now, d_now, False)
            rows.append({
                "kind": "grid_down",
                "cond": f"收盘跌破 {_fmt_level(lv, True)}",
                "effect": _with_breakdown(_with_d_note(_leg_effect("网格腿", leg_a_now, new_a, target, new_total), d_now, new_d), new_a, leg_b_now, leg_c_now, new_d) + _near_note(close, lv),
            })
    above = [lv for lv in GRID if lv >= close]
    if above:
        lv = min(above)
        new_a = leg_a(lv + 1e-6)
        if new_a != leg_a_now:
            new_d, new_total = _total_with_d(new_a, leg_b_now, leg_c_now, d_now, False)
            rows.append({
                "kind": "grid_up",
                "cond": f"收盘站上 {_fmt_level(lv, True)}",
                "effect": _with_breakdown(_with_d_note(_leg_effect("网格腿", leg_a_now, new_a, target, new_total), d_now, new_d), new_a, leg_b_now, leg_c_now, new_d) + _near_note(close, lv),
            })
    if on_above:
        new_d, new_total = _total_with_d(leg_a_now, 2, leg_c_now, d_now, False)
        rows.append({
            "kind": "trend_on",
            "cond": f"收盘站上 {_fmt_level(on_above, False)}",
            "effect": _with_breakdown(_with_d_note(_leg_effect("趋势腿", leg_b_now, 2, target, new_total), d_now, new_d), leg_a_now, 2, leg_c_now, new_d) + _near_note(close, on_above),
        })
    if off_below:
        new_d, new_total = _total_with_d(leg_a_now, 0, leg_c_now, d_now, True)
        rows.append({
            "kind": "trend_off",
            "cond": f"收盘跌破 {_fmt_level(off_below, False)}",
            "effect": _with_breakdown(_with_d_note(_leg_effect("趋势腿", leg_b_now, 0, target, new_total), d_now, new_d), leg_a_now, 0, leg_c_now, new_d) + _near_note(close, off_below),
        })
    if next_roll:
        if c_basis_now is not None and c_code:
            implied = c_from_basis(c_basis_now)
            effect = (
                f"新近月 {c_code}，按今日收盘年化贴水 {c_basis_now * 100:.2f}%"
                f"（若按今日则 C = {signed(implied)}）。在此之前 C 维持 {signed(leg_c_now)}。"
            )
        elif c_code:
            effect = f"新近月 {c_code} 今日无行情。在此之前 C 维持 {signed(leg_c_now)}，以换月当日收盘为准。"
        else:
            effect = f"在此之前 C 维持 {signed(leg_c_now)}，以换月当日收盘为准。"
        rows.append({
            "kind": "basis",
            "cond": f"{next_roll} 收盘决定贴水腿",
            "effect": effect,
        })
    return rows


def _best(codes: list[str], discounts: dict[str, float]) -> str | None:
    if not codes:
        return None
    return max(codes, key=lambda c: discounts[c])


def choose_open(eligible: list[str], benchmark: str, discounts: dict[str, float]) -> str | None:
    if not eligible:
        return None
    base = benchmark if benchmark in eligible else eligible[0]
    contenders = [c for c in eligible if c != base and discounts[c] >= discounts[base] + OPEN_GAP - 1e-9]
    if not contenders:
        return base
    return _best(contenders, discounts)


def fresh_entry(slots: list[dict], prices: dict[str, float | None], discounts: dict[str, float]) -> str | None:
    """新开仓会选的合约。近月不足 10 日时，基准改成次月，不沿用回测路径上的旧仓。"""
    eligible = eligible_codes(slots, prices)
    if not eligible or not slots:
        return None
    near = slots[0]["code"]
    bench = slots[1]["code"] if near not in eligible else near
    return choose_open(eligible, bench, discounts)


def choose_switch(held: str, eligible: list[str], discounts: dict[str, float]) -> str:
    contenders = [c for c in eligible if c != held and discounts[c] >= discounts[held] + SWITCH_GAP - 1e-9]
    if not contenders:
        return held
    return _best(contenders, discounts)


def status_text(role: str, days: int, price: float | None, code: str, held: str | None) -> str:
    bits = []
    if held and code == held:
        bits.append("持仓中")
    if role == "far":
        bits.append("下季不参与")
    elif price is None:
        bits.append("无行情")
    elif days < MIN_DAYS:
        bits.append("不足10日")
    elif not held or code != held:
        bits.append("可比较")
    return " · ".join(bits)


def eligible_codes(slots: list[dict], prices: dict[str, float | None]) -> list[str]:
    out = []
    for s in slots:
        if s["role"] == "far" or s["days"] < MIN_DAYS:
            continue
        if prices.get(s["code"]) is None:
            continue
        out.append(s["code"])
    return out


def decide_position(asof: dt.date, slots: list[dict], prices: dict[str, float | None],
                    discounts: dict[str, float], held: str | None, holidays: set[str],
                    target: int) -> dict:
    """返回当日收盘后的持仓决定。target=0 则空仓。"""
    blank = {
        "action": "flat", "contract": None, "roll_hint": None,
        "position_roll": None, "action_text": "空仓", "note": "",
    }
    if target <= 0:
        return blank
    eligible = eligible_codes(slots, prices)
    near = slots[0]["code"]
    post_near = slots[1]["code"]
    expiring = False
    if held:
        ltd = last_trade_day(*parse_code(held), holidays)
        if asof == prev_trading_day(ltd, holidays):
            expiring = True
    if held is None or expiring or held not in discounts:
        chosen = choose_open(eligible, post_near if expiring or near not in eligible else near, discounts)
        if chosen is None:
            if held and not expiring:
                pos_roll = _future_roll(held, asof, holidays)
                text = f"持有 {held}" + (f" · {pos_roll} 收盘强制换月" if pos_roll else "")
                return {
                    "action": "hold", "contract": held, "roll_hint": None,
                    "position_roll": pos_roll, "action_text": text, "note": "当日可比合约行情不全，持仓未改",
                }
            return blank
        action = "roll" if expiring else "open"
        text = ("换月至 " if expiring else "开仓 ") + chosen
        return {
            "action": action, "contract": chosen, "roll_hint": None,
            "position_roll": asof.isoformat() if expiring else _future_roll(chosen, asof, holidays),
            "action_text": text, "note": "",
        }
    chosen = choose_switch(held, eligible, discounts)
    action = "switch" if chosen != held else "hold"
    pos_roll = _future_roll(chosen, asof, holidays)
    if action == "switch":
        text = f"切换至 {chosen}"
        note = ""
    else:
        text = f"持有 {chosen}"
        if pos_roll:
            text += f" · {pos_roll} 收盘强制换月"
        note = ""
        hint = choose_open(eligible, post_near if post_near in eligible else (eligible[0] if eligible else ""), discounts) if pos_roll else None
        if hint and hint != chosen:
            note = f"按今日贴水预计换至 {hint}，换月当日重算"
        return {
            "action": "hold", "contract": chosen, "roll_hint": hint,
            "position_roll": pos_roll, "action_text": text, "note": note,
        }
    return {
        "action": action, "contract": chosen, "roll_hint": None,
        "position_roll": pos_roll, "action_text": text, "note": note,
    }


def _future_roll(code: str | None, asof: dt.date, holidays: set[str]) -> str | None:
    if not code:
        return None
    roll = prev_trading_day(last_trade_day(*parse_code(code), holidays), holidays)
    if roll >= asof:
        return roll.isoformat()
    return None


def next_c_event(asof: dt.date, slots: list[dict], holidays: set[str]) -> tuple[str, str | None]:
    """返回 (下一次贴水腿决定日, 该日要看的新近月合约)。决定日当天收盘之后，指向再下一次。"""
    near_roll = prev_trading_day(slots[0]["ltd"], holidays)
    if asof < near_roll:
        return near_roll.isoformat(), slots[1]["code"]
    y, m = add_months(*parse_code(slots[0]["code"]), 2)
    roll = prev_trading_day(last_trade_day(y, m, holidays), holidays)
    return roll.isoformat(), contract_code(y, m)


def simulate(index_rows: list[tuple[str, float]], price_map: dict[str, dict[str, float]],
             holidays: set[str]) -> list[dict]:
    b = 0
    c = 0
    c_set_on = None
    c_basis = None
    c_contract = None
    held = None
    d_lots = 0
    d_cum = 0.0
    d_cum0 = 0.0
    d_prev_base = 0
    d_prev_close = None
    out = []
    for i, (ds, close) in enumerate(index_rows):
        if i < MA_N - 1:
            d_prev_close = close
            continue
        window = index_rows[i - (MA_N - 1):i + 1]
        sum250 = sum(px for _, px in window)
        ma = sum250 / MA_N
        drop = window[0][1]
        a = leg_a(close)
        b = next_b(b, close, ma)
        asof = dt.date.fromisoformat(ds)
        slots = list_slots(asof, holidays)
        prices = {s["code"]: price_map.get(s["code"], {}).get(ds) for s in slots}
        near_roll = prev_trading_day(slots[0]["ltd"], holidays)
        if asof == near_roll:
            nxt = slots[1]
            px = prices.get(nxt["code"])
            basis = ann_discount(close, px, nxt["days"]) if px is not None else None
            if basis is not None:
                c = c_from_basis(basis)
                c_set_on = ds
                c_basis = basis
                c_contract = nxt["code"]
        base = clip_lots(a + b + c)
        d_lots, d_cum, d_cum0 = advance_d(
            d_lots, d_cum, d_cum0, d_prev_base, d_prev_close, base, close, ma,
        )
        d_prev_base = base
        d_prev_close = close
        d_pnl = d_cum - d_cum0
        d_need = d_next_yuan(d_lots, d_cum, d_cum0, base, close)
        if c_set_on is None:
            # 贴水腿还没有一次换月可以落定，持仓状态仍向前走，但不发布。
            # D 已按当日底仓重放，状态留到下一天。
            target_shadow = base
            discounts = {}
            for s in slots:
                px = prices.get(s["code"])
                basis = ann_discount(close, px, s["days"]) if px is not None else None
                if basis is not None:
                    discounts[s["code"]] = basis
            if held and held not in discounts:
                series = price_map.get(held, {})
                px = series.get(ds)
                basis = ann_discount(close, px, (last_trade_day(*parse_code(held), holidays) - asof).days) if px is not None else None
                if basis is not None:
                    discounts[held] = basis
            decision = decide_position(asof, slots, prices, discounts, held, holidays, target_shadow)
            held = decision["contract"]
            continue
        target = base
        discounts = {}
        for s in slots:
            px = prices.get(s["code"])
            basis = ann_discount(close, px, s["days"]) if px is not None else None
            if basis is not None:
                discounts[s["code"]] = basis
        if held and held not in discounts:
            series = price_map.get(held, {})
            px = series.get(ds)
            days_held = (last_trade_day(*parse_code(held), holidays) - asof).days
            basis = ann_discount(close, px, days_held) if px is not None else None
            if basis is not None:
                discounts[held] = basis
                prices[held] = px
        decision = decide_position(asof, slots, prices, discounts, held, holidays, target)
        held = decision["contract"]
        on_above, off_below = b_trigger_prices(sum250, drop)
        next_roll, eval_code = next_c_event(asof, slots, holidays)
        eval_basis = None
        if eval_code:
            # 优先用当日挂牌槽里的价格；不在四档里则查该合约自己的序列。
            px = prices.get(eval_code)
            if px is None:
                px = price_map.get(eval_code, {}).get(ds)
            if px is not None:
                ev_days = (last_trade_day(*parse_code(eval_code), holidays) - asof).days
                eval_basis = ann_discount(close, px, ev_days)
        rows = trigger_rows(close, a, b, c, on_above, off_below, next_roll, eval_code, eval_basis, d_lots)
        entry_pick = fresh_entry(slots, prices, discounts)
        entry_roll = _future_roll(entry_pick, asof, holidays)
        contract_rows = []
        for s in slots:
            px = prices.get(s["code"])
            basis = discounts.get(s["code"])
            contract_rows.append({
                "code": s["code"],
                "role": s["role"],
                "label": s["label"],
                "price": None if px is None else round(px, 1),
                "expiry": s["expiry"],
                "days": s["days"],
                "basis": None if basis is None else round(basis, 6),
                "status": status_text(s["role"], s["days"], px, s["code"], held),
            })
        held_px = prices.get(held) if held else None
        notional = target * held_px * MULTIPLIER if held_px and target else 0.0
        total = min(10, target + d_lots)
        total_notional = total * held_px * MULTIPLIER if held_px and total else 0.0
        raw = a + b + c
        sum_text = f"{leg_formula(a, b, c, d_lots)} = {total}"
        if raw != target:
            sum_text += f"（底仓未限制前 {raw}）"
        out.append({
            "date": ds,
            "index_close": round(close, 3),
            "ma250": round(ma, 2),
            "ma_upper": round(ma * 1.01, 2),
            "ma_lower": round(ma * 0.99, 2),
            "leg_a": a,
            "leg_b": b,
            "leg_c": c,
            "leg_d": d_lots,
            "d_pnl": round(d_pnl, 2),
            "d_next_yuan": None if d_need is None else round(d_need, 2),
            "c_set_on": c_set_on,
            "c_basis": None if c_basis is None else round(c_basis, 6),
            "c_contract": c_contract,
            "target": target,
            "total": total,
            "sum_text": sum_text,
            "contract": held,
            "entry_pick": entry_pick,
            "entry_roll": entry_roll,
            "action": decision["action"],
            "action_text": decision["action_text"],
            "note": decision["note"],
            "next_roll": next_roll,
            "position_roll": decision["position_roll"],
            "roll_hint": decision["roll_hint"],
            "b_open": b == 2,
            "notional": round(notional, 2),
            "margin_12": round(notional * 0.12, 2),
            "margin_14": round(notional * 0.14, 2),
            "buffer_15": round(notional * 0.15, 2),
            "total_notional": round(total_notional, 2),
            "total_margin_12": round(total_notional * 0.12, 2),
            "contracts": contract_rows,
            "triggers": rows,
            "trend_on_above": round(on_above, 2),
            "trend_off_below": round(off_below, 2),
        })
    return out


def sanity_check(days: list[dict]) -> None:
    by = {d["date"]: d for d in days}
    d = by.get("2026-11-16")
    if d is None:
        print("sanity: 2026-11-16 不在本次窗口内，跳过锁定检查")
        return
    problems = []
    expect = {
        "leg_a": 2, "leg_b": 0, "leg_c": 2, "target": 4,
        "contract": "IC2611", "c_contract": "IC2611", "c_set_on": "2026-10-15",
        "next_roll": "2026-11-19",
    }
    for k, v in expect.items():
        if d.get(k) != v:
            problems.append(f"{k}={d.get(k)!r} 期望 {v!r}")
    if abs(d["index_close"] - 6297.183) > 0.02:
        problems.append(f"index_close={d['index_close']}")
    if abs(d["ma250"] - 7819.50) > 0.2:
        problems.append(f"ma250={d['ma250']}")
    if d.get("c_basis") is None or abs(d["c_basis"] * 100 - 11.42) > 0.05:
        problems.append(f"c_basis={d.get('c_basis')}")
    want = {
        "IC2611": (4, 24.32, False),
        "IC2612": (32, 14.27, True),
        "IC2703": (123, 10.44, True),
        "IC2706": (214, 10.28, False),
    }
    got = {c["code"]: c for c in d["contracts"]}
    for code, (days_n, basis_pct, eligible) in want.items():
        c = got.get(code)
        if c is None:
            problems.append(f"缺少 {code}")
            continue
        if c["days"] != days_n:
            problems.append(f"{code} days={c['days']} 期望 {days_n}")
        if c["basis"] is None or abs(c["basis"] * 100 - basis_pct) > 0.02:
            problems.append(f"{code} basis={c.get('basis')}")
        is_eligible = "可比较" in c["status"]
        if is_eligible != eligible:
            problems.append(f"{code} status={c['status']}")
    texts = " ".join(r["cond"] + r["effect"] for r in d["triggers"])
    if "收盘跌破 6000" not in texts or "合计加到 6 手" not in texts:
        problems.append("缺少跌破 6000 文案")
    if "收盘站上 6500" not in texts or "合计减到 2 手" not in texts:
        problems.append("缺少站上 6500 文案")
    if d.get("leg_d") is None or not 0 <= int(d["leg_d"]) <= D_MAX:
        problems.append(f"leg_d={d.get('leg_d')}")
    if d.get("total") != min(10, d["target"] + d.get("leg_d", 0)):
        problems.append(f"total={d.get('total')}")
    if problems:
        for day in days:
            if day["date"] >= "2026-09-01":
                print(f"  {day['date']} A{day['leg_a']} B{day['leg_b']} C{day['leg_c']} D{day.get('leg_d')} "
                      f"base{day['target']} T{day.get('total')} {day['action']:6} {day['contract']} set={day['c_set_on']}")
        raise SystemExit("2026-11-16 核对失败: " + "; ".join(problems))
    print("sanity 2026-11-16 OK", d["action_text"], d["note"], "target", d["target"])


def merge_history(old: list[dict], new: list[dict]) -> list[dict]:
    if not new:
        return old
    cutoff = new[0]["date"]
    kept = [d for d in old if d["date"] < cutoff]
    return kept + new


# ---------------------------------------------------------------- fetch
def _cache_path(cache: str | None, url: str) -> Path | None:
    if not cache:
        return None
    import hashlib
    name = hashlib.sha256(url.encode()).hexdigest()[:24]
    return Path(cache) / name


def http_get(url: str, headers: dict, cache: str | None = None, timeout: int = 40, retries: int = 3) -> bytes | None:
    path = _cache_path(cache, url)
    if path and path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    delay = 0.8
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return data
        except Exception as exc:  # noqa: BLE001 — 单个源失败要重试，不让整次任务直接 traceback
            last = exc
            time.sleep(delay)
            delay *= 2
    print(f"[warn] GET failed {url[:80]} {last}", file=sys.stderr)
    return None


def parse_jsonp_array(text: str):
    i = text.find("([")
    j = text.rfind("])")
    if i < 0 or j < i:
        return []
    try:
        data = json.loads(text[i + 1:j + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def fetch_index(cache: str | None) -> list[tuple[str, float]]:
    raw = http_get(INDEX_URL, SINA_UA, cache)
    if not raw:
        raise SystemExit("指数日K拉取失败")
    rows = []
    for item in parse_jsonp_array(raw.decode("utf-8", "replace")):
        if not isinstance(item, dict) or "day" not in item or "close" not in item:
            continue
        rows.append((item["day"][:10], round(float(item["close"]), 3)))
    rows.sort()
    if len(rows) < MA_N:
        raise SystemExit(f"指数样本不足 {len(rows)}")
    return rows


def fetch_contract(code: str, cache: str | None) -> dict[str, float]:
    raw = http_get(FUT_URL.format(code=code), SINA_UA, cache)
    if not raw:
        return {}
    out = {}
    for item in parse_jsonp_array(raw.decode("utf-8", "replace")):
        if not isinstance(item, dict) or "d" not in item or "c" not in item:
            continue
        try:
            out[item["d"][:10]] = round(float(item["c"]), 1)
        except (TypeError, ValueError):
            continue
    return out


def fetch_cffex_closes(yyyymmdd: str, cache: str | None) -> dict[str, float]:
    url = CFFEX_URL.format(ym=yyyymmdd[:6], dd=yyyymmdd[6:], ymd=yyyymmdd)
    raw = http_get(url, {"User-Agent": "Mozilla/5.0", "Referer": "http://www.cffex.com.cn/"}, cache)
    if not raw:
        return {}
    text = raw.decode("gbk", "replace")
    out = {}
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9 or not re.fullmatch(r"IC\d{4}", parts[0]):
            continue
        try:
            out[parts[0]] = round(float(parts[8]), 1)
        except ValueError:
            continue
    return out


def parse_sina_fut_quote(text: str) -> dict | None:
    """新浪 nf_ 行情。只接受带日期、且时间不早于 15:00 的最新价，当作收盘。"""
    i = text.find('="')
    j = text.rfind('"')
    if i < 0 or j <= i + 2:
        return None
    parts = text[i + 2:j].split(",")
    if len(parts) < 5:
        return None
    date = next((p for p in parts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)), None)
    if date is None:
        return None
    idx = parts.index(date)
    clock = parts[idx + 1] if idx + 1 < len(parts) else ""
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", clock) or clock < "15:00:00":
        return None
    try:
        price = round(float(parts[3]), 1)
    except ValueError:
        return None
    return {"date": date, "time": clock, "price": price}


def fetch_sina_session_close(code: str) -> dict | None:
    url = f"https://hq.sinajs.cn/list=nf_{code}"
    req = urllib.request.Request(url, headers=SINA_UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("gbk", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] realtime {code} {exc}", file=sys.stderr)
        return None
    return parse_sina_fut_quote(text)


def fetch_cffex_quote_last() -> dict[str, float]:
    """中金所 quote_IC.txt 的 lastprice。文件本身不带日期，只用来核对同一时刻的新浪收盘。"""
    url = "http://www.cffex.com.cn/quote_IC.txt"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "http://www.cffex.com.cn/"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] cffex quote {exc}", file=sys.stderr)
        return {}
    out = {}
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5 or not re.fullmatch(r"IC\d{4}", parts[0]):
            continue
        try:
            out[parts[0]] = round(float(parts[4]), 1)
        except ValueError:
            continue
    return out


def fill_latest_closes(last_day: str, codes: list[str], price_map: dict[str, dict[str, float]]) -> str:
    """日 K 还没有最后交易日时，用新浪 15:00 最新价补收盘，并与中金所 lastprice 核对。"""
    missing = [c for c in codes if last_day not in price_map.get(c, {})]
    if not missing:
        return ""
    quote = fetch_cffex_quote_last()
    filled = []
    skipped = []
    for code in missing:
        live = fetch_sina_session_close(code)
        if live is None or live["date"] != last_day:
            continue
        official = quote.get(code)
        if official is not None and abs(official - live["price"]) > 0.15:
            skipped.append(f"{code}:sina {live['price']} cffex {official}")
            continue
        price_map.setdefault(code, {})[last_day] = official if official is not None else live["price"]
        filled.append(code)
    if not filled and not skipped:
        return ""
    note = f"{last_day} 日K未含收盘，已用新浪 15:00 最新价补 " + ",".join(filled)
    if quote and filled:
        note += "，与中金所 quote_IC.txt lastprice 一致"
    if skipped:
        note += "；以下两边不一致已跳过 " + ",".join(skipped)
    return note


def month_codes(start: tuple[int, int], end: tuple[int, int]) -> list[str]:
    y, m = start
    codes = []
    while (y, m) <= end:
        codes.append(contract_code(y, m))
        y, m = add_months(y, m, 1)
    return codes


def build_payload(index_rows, price_map, holidays, cffex_note: str) -> dict:
    days = simulate(index_rows, price_map, holidays)
    sanity_check(days)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return {
        "schema_version": "ic-strategy-1.0",
        "updated": now,
        "latest": days[-1]["date"] if days else None,
        "multiplier": MULTIPLIER,
        "d_leg": {
            "name": "盈利覆盖加仓",
            "frac": D_FRAC,
            "d_max": D_MAX,
            "margin_rate": D_MARGIN,
            "total_cap": 10,
            "exit": "收盘 < MA250×0.99，或底仓 <= 0",
        },
        "sources": {
            "index": "新浪财经 sh000905 日K收盘（CN_MarketDataService.getKLineData scale=240）",
            "futures": "新浪财经 IC 日K收盘（InnerFuturesNewService.getDailyKLine 字段 c）",
            "cffex": cffex_note,
            "calendar": "交易日取中证500有收盘的日期；最后交易日为合约月份第三个星期五，遇休市顺延",
        },
        "days": days,
    }


def dump_payload(payload: dict) -> str:
    """元数据缩进，每个交易日单独一行，方便每日只 diff 到新增的那一天。"""
    days = payload.get("days") or []
    meta = {k: v for k, v in payload.items() if k != "days"}
    lines = ["{"]
    for key, value in meta.items():
        lines.append(f"  {json.dumps(key, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append('  "days": [')
    last = len(days) - 1
    for i, day in enumerate(days):
        comma = "," if i < last else ""
        lines.append("    " + json.dumps(day, ensure_ascii=False, separators=(",", ":")) + comma)
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def load_old(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    days = data.get("days") or []
    return days if isinstance(days, list) else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="计算 IC 收盘信号并写入 data/ic_strategy.json")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--cache", default="")
    args = ap.parse_args(argv)
    cache = args.cache or None
    index_rows = fetch_index(cache)
    last_day = dt.date.fromisoformat(index_rows[-1][0])
    first_ma = dt.date.fromisoformat(index_rows[MA_N - 1][0])
    start = add_months(first_ma.year, first_ma.month, -3)
    end = add_months(last_day.year, last_day.month, 9)
    codes = month_codes(start, end)
    print(f"index {index_rows[0][0]}→{index_rows[-1][0]} n={len(index_rows)} contracts={len(codes)}")
    price_map: dict[str, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for code, series in zip(codes, pool.map(lambda c: fetch_contract(c, cache), codes)):
            if series:
                price_map[code] = series
    print(f"futures series {len(price_map)}/{len(codes)}")
    ymd = index_rows[-1][0].replace("-", "")
    official = fetch_cffex_closes(ymd, cache)
    cffex_note = "中金所日行情当日文件未取到，期货收盘用新浪"
    if official:
        replaced = []
        for code, px in official.items():
            series = price_map.setdefault(code, {})
            prev = series.get(index_rows[-1][0])
            if prev != px:
                replaced.append(f"{code}:{prev}->{px}")
            series[index_rows[-1][0]] = px
        cffex_note = "中金所日行情今收盘已核对" + (("，覆盖 " + ",".join(replaced)) if replaced else "，与新浪一致")
    holidays = infer_holidays([d for d, _ in index_rows])
    slot_codes = [s["code"] for s in list_slots(last_day, holidays)]
    extra = fill_latest_closes(index_rows[-1][0], slot_codes, price_map)
    if extra:
        cffex_note = cffex_note + "。" + extra
        print(extra)
    payload = build_payload(index_rows, price_map, holidays, cffex_note)
    old = load_old(Path(args.out))
    payload["days"] = merge_history(old, payload["days"])
    if payload["days"]:
        payload["latest"] = payload["days"][-1]["date"]
    text = dump_payload(payload)
    out = Path(args.out)
    write_text_atomic(out, text)
    print(f"wrote {out} days={len(payload['days'])} latest={payload['latest']} {cffex_note}")
    for day in payload["days"]:
        if day["date"] >= "2026-10-01":
            print(f"  {day['date']} A{day['leg_a']} B{day['leg_b']} C{day['leg_c']} D{day['leg_d']} "
                  f"base{day['target']} T{day['total']} pick={day.get('entry_pick')} "
                  f"pnl={day['d_pnl']} {day['action']:6} path={day['contract']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
