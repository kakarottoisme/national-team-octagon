"""Pure ETF parsing shared by live and archive collectors. No network or file I/O."""
from __future__ import annotations

ETF_UNIVERSE = (
    ("上证50", "sh510050"),
    ("沪深300", "sh510300"),
    ("中证A500", "sz159352"),
    ("中证500", "sh510500"),
    ("中证1000", "sh512100"),
    ("创业板", "sz159915"),
    ("科创50", "sh588000"),
)


def parse_minutes(raw: dict, code: str) -> dict:
    """Keep legacy precision and fallback: cumulative yuan -> per-minute 亿元.

    Empty minutes are valid; malformed numeric rows raise as before. Do not turn
    missing records into zero or change the strict >1亿元 threshold here.
    """
    data = raw["data"]
    previous_amount = 0
    times, prices, amounts = [], [], []
    for text in data["data"]:
        fields = text.split()
        cumulative = float(fields[3])
        times.append(fields[0])
        prices.append(float(fields[1]))
        amounts.append(round((cumulative - previous_amount) / 1e8, 4))
        previous_amount = cumulative
    try:
        previous_close = float(raw["qt"][code][4])
    except Exception:
        previous_close = prices[0] if prices else None
    return {"t": times, "p": prices, "a": amounts, "date": data["date"],
            "pre": previous_close, "amt": round(previous_amount / 1e8, 4)}


def estimated_large_minutes(amounts: list[float]) -> float:
    """Legacy threshold proxy; not an institution or fund-flow identification."""
    return round(sum(value for value in amounts if value > 1), 2)


def quote_direction(raw: dict, code: str, minutes: dict) -> dict:
    """Legacy active-buy estimate: quote outer/inner volume times turnover."""
    quote = (raw.get("qt") or {}).get(code) or []
    outer = inner = price = None
    try:
        outer, inner = int(float(quote[7])), int(float(quote[8]))
    except Exception:
        outer = inner = None
    try:
        price = float(quote[3])
    except Exception:
        price = minutes["p"][-1] if minutes["p"] else None
    net = None
    if outer is not None and inner is not None and (outer + inner) > 0:
        net = round((outer - inner) / (outer + inner) * minutes["amt"], 3)
    return {"ob": outer, "os": inner, "px": price, "net_buy": net}


def iso_market_date(date: str) -> str:
    """Legacy Tencent YYYYMMDD conversion; deliberately no calendar inference."""
    return f"{date[:4]}-{date[4:6]}-{date[6:]}"
