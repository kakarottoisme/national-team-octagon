"""A-share trading calendar: verified holidays, unknown years fail closed."""
from __future__ import annotations
import datetime as dt
import html
import re
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
CALENDAR_URL = 'https://www.sse.com.cn/disclosure/dealinstruc/closed/'
HOLIDAYS_2026 = ((1,1,1,3),(2,15,2,23),(4,4,4,6),(5,1,5,5),(6,19,6,21),(9,25,9,27),(10,1,10,7))


def is_trading_day(day: dt.date, fetch=None) -> bool:
    if day.weekday() >= 5:
        return False
    if day.year == 2026:
        intervals = HOLIDAYS_2026
    else:
        if fetch is None:
            def fetch(url):
                req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=12) as r:
                    return r.read().decode('utf-8')
        raw = fetch(CALENDAR_URL)
        text = html.unescape(re.sub(r'<[^>]+>', ' ', raw))
        marker = re.search(rf'{day.year}\s*年休市安排', text)
        if not marker:
            raise ValueError('交易日日历尚未确认，停止采集')
        text = text[marker.end():]
        text = text.split('相关公告')[0]
        # Seven annual holiday intervals, not weekend makeup working days.
        matches = re.findall(r'(\d{1,2})月(\d{1,2})日[^。；]{0,30}?至(\d{1,2})月(\d{1,2})日[^。；]{0,30}?休市', text)
        if len(matches) != 7:
            raise ValueError('交易日日历解析不完整，停止采集')
        intervals = [tuple(map(int, row)) for row in matches]
    return not any(dt.date(day.year,a,b) <= day <= dt.date(day.year,c,d) for a,b,c,d in intervals)


def in_live_session(now: dt.datetime) -> bool:
    now = now.astimezone(TZ)
    m = now.hour * 60 + now.minute
    return (555 <= m <= 695 or 775 <= m <= 905) and is_trading_day(now.date())
