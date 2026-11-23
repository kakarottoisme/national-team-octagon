"""Scheduled observations; no orders, no holdings writes, no IC strategy changes.
Run in an isolated checkout with authenticated git; stdout is one JSON result.
"""
from __future__ import annotations
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from backend_io import write_json_atomic
from market_calendar import TZ, is_trading_day

ROOT = Path(__file__).resolve().parent
REPO = 'https://github.com/kakarottoisme/national-team-octagon'
PAGE = 'https://kakarottoisme.github.io/national-team-octagon/'
HOLDINGS_URL = 'https://raw.githubusercontent.com/kakarottoisme/national-team-octagon/main/data/holdings.json'
SINA = 'https://hq.sinajs.cn/list='


def get(url, encoding='utf-8'):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode(encoding)


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], stderr=subprocess.STDOUT).decode()


def commit_files(paths, title):
    """Only explicit outputs staged, never force push; preserve conflicting work."""
    staged = git('diff', '--cached', '--name-only').splitlines()
    if staged:
        raise ValueError('存在其他暂存修改，停止提交')
    changed = git('diff', '--name-only').splitlines()
    if set(changed) - set(paths):
        raise ValueError('存在其他未提交修改，停止提交')
    git('add', '--', *paths)
    if not git('diff', '--cached', '--name-only').strip():
        return False
    git('-c','user.name=A-share Data Assistant','-c','user.email=ashare-assistant@users.noreply.github.com','commit','-m',title)
    for attempt in range(3):
        try:
            git('push','origin','HEAD:main')
            return True
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise ValueError('推送失败，保留本地提交；禁止空提交或强推')
            git('fetch','origin','main')
            try:
                git('-c','user.name=A-share Data Assistant','-c','user.email=ashare-assistant@users.noreply.github.com','rebase','origin/main')
            except subprocess.CalledProcessError:
                with contextlib.suppress(subprocess.CalledProcessError):
                    git('rebase','--abort')
                raise ValueError('远端并发修改冲突，保留提交，未强推')
    return False


def third_friday(year, month):
    first = dt.date(year,month,1)
    return first + dt.timedelta(days=(4-first.weekday())%7+14)


def month_add(year, month, n):
    k = year*12 + month-1+n
    return k//12, k%12+1


def contract_code(year, month):
    return f'IC{year%100:02d}{month:02d}'


def contract_expiry(code):
    if not re.fullmatch(r'IC\d{4}',code):
        raise ValueError('持仓合约格式无效')
    return third_friday(2000+int(code[2:4]),int(code[4:6]))


def candidate_codes(day):
    # User scope: current month, next month, current quarter; deduplicate overlap.
    quarter = ((day.month-1)//3+1)*3
    return sorted({contract_code(day.year,day.month),contract_code(*month_add(day.year,day.month,1)),contract_code(day.year,quarter)})


def number(value):
    x = float(value)
    if not math.isfinite(x):
        raise ValueError('来源数值无效')
    return x


def quotes(text, now, codes):
    rows = {name: value.split(',') for name,value in re.findall(r'var hq_str_(\w+)="([^"]*)";',text)}
    result = {}
    for code in ['sh000905', *['nf_'+c for c in codes]]:
        p = rows.get(code)
        if not p:
            raise ValueError(f'{code} 实时行情缺失')
        if code == 'sh000905':
            date, clock, price = p[30],p[31],number(p[3])
        else:
            i = next((i for i,v in enumerate(p) if re.fullmatch(r'\d{4}-\d{2}-\d{2}',v)),None)
            if i is None or i+1>=len(p):
                raise ValueError(f'{code} 行情时间缺失')
            date,clock,price = p[i],p[i+1],number(p[3])
        stamp = dt.datetime.fromisoformat(date+'T'+clock).replace(tzinfo=TZ)
        age = (now-stamp).total_seconds()
        if date != now.date().isoformat() or not -30 <= age <= 180 or price <= 0:
            raise ValueError(f'{code} 行情非当日实时或已过期')
        result[code.removeprefix('nf_')] = {'price':price,'time':stamp.isoformat()}
    return result


def evaluate_ic(positions, q, day):
    rows = [p for p in positions if re.fullmatch(r'IC\d{4}',str(p.get('contract',''))) and p.get('direction') in ('多','long') and number(p.get('lots',0))>0]
    if len(rows)!=1:
        raise ValueError('当前IC多头持仓不是单一合约，请核对持仓文件')
    held = rows[0]['contract']
    days = (contract_expiry(held)-day).days
    spot = q['sh000905']['price']
    def detail(c):
        remain = (contract_expiry(c)-day).days
        price = q[c]['price']
        return {'contract':c,'price':price,'days':remain,'basis':(spot-price)/spot*365/remain if remain>0 else None}
    old = detail(held)
    eligible = [detail(c) for c in candidate_codes(day) if (contract_expiry(c)-day).days>=10]
    if not eligible:
        raise ValueError('没有剩余至少10天且行情可用的候选合约')
    best = max(eligible,key=lambda r:r['basis'])
    diff = None if old['basis'] is None else best['basis']-old['basis']
    forced = days < 10
    triggered = best['contract']!=held and (forced or (diff is not None and diff >= .02-1e-12))
    return {'triggered':triggered,'forced':forced,'old':old,'new':best,'difference':diff,'index':spot,'lots':rows[0]['lots']}


def ic(now):
    try:
        holdings = json.loads(get(HOLDINGS_URL+'?t='+str(int(now.timestamp()))))
    except Exception:
        raise ValueError('当前持仓文件读取失败，停止换仓检查')
    eligible_codes = {c for c in candidate_codes(now.date()) if (contract_expiry(c)-now.date()).days >= 10}
    codes = sorted(eligible_codes | {p['contract'] for p in holdings.get('positions',[]) if re.fullmatch(r'IC\d{4}',str(p.get('contract','')))})
    q = quotes(get(SINA+','.join(['sh000905',*['nf_'+c for c in codes]]),'gbk'),now,codes)
    result = evaluate_ic(holdings.get('positions',[]),q,now.date())
    if not result['triggered']:
        return {'notify':False,'reason':'未触发换仓条件'}
    old,new = result['old'],result['new']
    basis = lambda x: '不适用（已到期）' if x is None else f'{x*100:.2f}%'
    diff = '不适用（已到期）' if result['difference'] is None else f"{result['difference']*100:.2f}个百分点"
    why = '持仓合约剩余不足10天/已到期' if result['forced'] else '候选年化贴水高出至少2个百分点'
    message = (f"IC换仓条件触发：{why}。{old['contract']} {old['price']:.1f}点、年化贴水{basis(old['basis'])}、剩余{old['days']}天；"
               f"{new['contract']} {new['price']:.1f}点、年化贴水{basis(new['basis'])}、剩余{new['days']}天；差值{diff}。"
               '按你设定的规则，建议14:57前同时平旧开新；换完请告诉我平仓和开仓成交价。提醒不代为下单。')
    return {'notify':True,'message':message,'data':result,'sources':[HOLDINGS_URL,SINA+','.join(['sh000905',*['nf_'+c for c in codes]])]}


class ArchiveSourcePending(ValueError):
    """Same-day source is incomplete or being corrected; never usable as-is."""


def validate_archive_source(raw, url, date):
    if '/minute/query' in url:
        # Tencent keeps appending quotes after 15:00, including cumulative
        # turnover corrections. They are not regular-session minute bars.
        # Select the entire defined session BEFORE validation and differencing;
        # never omit an invalid in-session point or mutate the source response.
        import copy
        code = url.rsplit('=', 1)[1]
        payload = raw['data'][code]['data']
        if payload['date'] != date.replace('-', ''):
            raise ValueError(f'{code} 分时不是目标交易日，未写入')
        required = [f'{m//60:02d}{m%60:02d}'
                    for start, end in ((570, 690), (780, 900))
                    for m in range(start, end + 1)]
        session = []
        previous_tick = None
        for row in payload['data']:
            fields = row.split()
            tick = fields[0] if fields else ''
            if (not re.fullmatch(r'\d{4}', tick) or
                    int(tick[:2]) > 23 or int(tick[2:]) > 59 or
                    (previous_tick is not None and tick <= previous_tick)):
                raise ArchiveSourcePending(f'{code} 分时顺序或时间无效，未写入')
            previous_tick = tick
            if '0930' <= tick <= '1130' or '1300' <= tick <= '1500':
                if len(fields) != 4:
                    raise ArchiveSourcePending(f'{code} 交易分钟字段不完整，未写入')
                session.append(row)
        if [r.split()[0] for r in session] != required:
            raise ArchiveSourcePending(f'{code} 当日收盘分时不完整，未写入')
        previous = 0.
        for row in session:
            fields = row.split()
            price, volume, amount = map(number, fields[1:4])
            if price <= 0 or volume < 0 or amount < 0:
                raise ArchiveSourcePending(f'{code} 交易分钟数值无效，未写入')
            if amount < previous:
                raise ArchiveSourcePending(f'{code} 交易时段累计成交额倒退，未写入')
            previous = amount
        quote = raw['data'][code].get('qt', {}).get(code, [])
        if len(quote) < 5 or number(quote[4]) <= 0:
            raise ArchiveSourcePending(f'{code} 前日收盘基准缺失，未写入')
        result = copy.deepcopy(raw)
        result['data'][code]['data']['data'] = session
        return result
    else:
        k = raw['data']['sh000001']
        rows = k.get('day') or k.get('qfqday') or []
        i = next((i for i, r in enumerate(rows) if r[0] == date), None)
        if i is None or i == 0:
            raise ArchiveSourcePending('上证当日收盘及前日基准不完整，未存档')
    return raw


def fetch_archive_source(fetch, url, date):
    # Tencent may revise post-close cumulative amounts. Re-read the entire
    # response, never clamp negative deltas, omit bad rows, or reuse old data.
    for attempt in range(3):
        try:
            return validate_archive_source(fetch(url), url, date)
        except urllib.error.HTTPError as exc:
            if attempt == 2 or (exc.code not in (408, 429) and exc.code < 500):
                raise  # Authentication/permission failures are not retried.
        except (ArchiveSourcePending, urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        if attempt == 2:
            raise ValueError('分时来源连续读取失败，未写入')
        time.sleep((15, 30)[attempt])
    raise ValueError('分时来源连续读取失败，未写入')


def verify_archive_pages(paths, timeout=180):
    """Read published JSON only; no quote fetch, build dispatch or permission changes."""
    expected={p:(ROOT/p).read_bytes() for p in paths}
    deadline=time.monotonic()+timeout
    while True:
        matches=True
        for p,raw in expected.items():
            try:
                request=urllib.request.Request(PAGE+p+'?archive_verify='+str(time.time_ns()),headers={'Cache-Control':'no-cache'})
                with urllib.request.urlopen(request,timeout=12) as response:
                    if response.read()!=raw:matches=False
            except Exception:matches=False
        if matches:return
        if time.monotonic()>=deadline:raise ValueError('归档已提交但Pages尚未验证发布，不重新抓取或重复提交')
        time.sleep(5)


def archive_mode(now):
    minute=now.hour*60+now.minute
    if 910<=minute<=930:return 'live_source'
    if minute>930:return 'git_snapshot'
    return None


def archive(now, state, timing=None):
    date = now.date().isoformat()
    mode=archive_mode(now)
    if mode is None:return {'notify':False,'reason':'不在授权窗口','state':'outside_window'}
    if timing is not None:timing['mode']=mode
    history=json.loads((ROOT/'data/history.json').read_text())
    saved=ROOT/f'data/minutes/{date}.json'
    if mode=='git_snapshot':
        # Late execution reads immutable Git observations only, never Tencent.
        if date in history and saved.exists():
            if json.loads(saved.read_text()).get('date')!=date:raise ValueError('已存档日期不匹配，停止自动覆盖')
            verify_archive_pages(['data/history.json',f'data/minutes/{date}.json'])
            state['archive_success']=date
            return {'notify':False,'reason':'当日已存档','state':'already_archived','mode':mode}
        if date in history or saved.exists():raise ValueError('当日已有不完整存档，停止自动覆盖，请核查')
        import restore_archive_snapshot as recovery
        started=time.monotonic()
        summary,minutes=recovery.from_git(ROOT,date)
        if timing is not None:timing['snapshot_seconds']=round(time.monotonic()-started,3)
        recovery.apply_missing(ROOT/'data',date,summary,minutes)
        started=time.monotonic()
        pushed=commit_files(['data/history.json',f'data/minutes/{date}.json'],f'archive {date} from preserved close snapshot')
        if timing is not None:timing['publish_seconds']=round(time.monotonic()-started,3)
        if not pushed:return {'notify':False,'reason':'无变化','state':'no_change','mode':mode}
        started=time.monotonic()
        verify_archive_pages(['data/history.json',f'data/minutes/{date}.json'])
        if timing is not None:timing['pages_seconds']=round(time.monotonic()-started,3)
        state['archive_success']=date
        state.pop('archive_failure',None)
        return {'notify':False,'reason':'晚到补档成功','state':'archived','mode':mode,'sources':[minutes['archive_provenance']['source_url']]}
    if state.get('archive_success') == date and date in history and saved.exists():
        return {'notify':False,'reason':'当日已存档','state':'already_archived'}
    import archive as producer
    source_started=time.monotonic()
    def windowed_fetch(url):
        # Keep the original request window even between slow retries.
        current=now+dt.timedelta(seconds=time.monotonic()-source_started)
        if archive_mode(current)!='live_source' or current.date()!=now.date():
            raise ValueError('实时抓源窗口已结束，停止请求；后续仅允许Git收盘快照补档')
        return producer.g(url)
    def validated(url):
        return fetch_archive_source(windowed_fetch,url,date)
    started=time.monotonic()
    day,summary,minutes = producer.build_archive(fetch=validated)
    if timing is not None:timing['collect_seconds']=round(time.monotonic()-started,3)
    if day!=date or '上证收盘' not in summary or '上证涨跌幅' not in summary:
        raise ValueError('上证当日收盘或涨跌幅缺失，未存档')
    for key in summary:
        number(summary[key])
    history=json.loads((ROOT/'data/history.json').read_text())
    if date in history and history[date]==summary and (ROOT/f'data/minutes/{date}.json').exists():
        state['archive_success']=date
        return {'notify':False,'reason':'已入库且无变化'}
    history[date]=summary
    write_json_atomic(ROOT/'data/history.json',dict(sorted(history.items())),ensure_ascii=False,indent=1)
    write_json_atomic(ROOT/f'data/minutes/{date}.json',minutes,separators=(',',':'))
    started=time.monotonic()
    pushed=commit_files(['data/history.json',f'data/minutes/{date}.json'],f'archive {date}')
    if timing is not None:timing['publish_seconds']=round(time.monotonic()-started,3)
    if not pushed:
        return {'notify':False,'reason':'无变化'}
    state['archive_success']=date
    state.pop('archive_failure',None)
    return {'notify':True,'message':f"{date}｜估算合计{summary['合计']:.2f}亿元｜上证{summary['上证涨跌幅']:+.2f}%",'link':PAGE,'sources':['https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh510050',REPO+'/blob/main/data/history.json']}


def previous_trading_day(day):
    day -= dt.timedelta(days=1)
    while not is_trading_day(day):
        day -= dt.timedelta(days=1)
    return day


def margin_missing(payload, expected):
    """Check actual data and six indices; never trust a run-day success flag."""
    import margin as producer
    rows = payload.get('margin', {})
    # Check recent internal holes as well as the last expected disclosure day.
    start = max(min(rows, default=expected),
                (dt.date.fromisoformat(expected)-dt.timedelta(days=20)).isoformat())
    day = dt.date.fromisoformat(start)
    missing = []
    while day.isoformat() <= expected:
        key = day.isoformat()
        if is_trading_day(day):
            if key not in rows or not math.isfinite(number(rows[key])) or number(rows[key]) <= 0:
                missing.append(key + '余额')
            elif any(key not in payload.get('index', {}).get(name, {}) or
                     number(payload['index'][name][key]) <= 0 for name in producer.IDX):
                missing.append(key + '指数')
        day += dt.timedelta(days=1)
    return missing


def build_margin_update(old, now, expected):
    """Build only missing official dates, retaining every existing stored value."""
    import margin as producer
    import copy
    out = copy.deepcopy(old)
    rows = out.setdefault('margin', {})
    start = max(min(rows, default=expected),
                (dt.date.fromisoformat(expected)-dt.timedelta(days=20)).isoformat())
    recent = producer.ak.stock_margin_sse(start_date=start.replace('-',''),
                                         end_date=expected.replace('-',''))
    sources = out.setdefault('margin_sources', {})
    for record in recent.to_dict('records'):
        day = dt.datetime.strptime(str(record['信用交易日期']), '%Y%m%d').date().isoformat()
        if day in rows or day > expected or not is_trading_day(dt.date.fromisoformat(day)):
            continue
        sz = producer.szse_official(day)
        sh = number(record['融资融券余额']) / 1e8
        if sz['date'] != day or sh <= 0 or number(sz['balance_yi']) <= 0:
            raise ValueError('沪深两融同日校验失败，未写入')
        rows[day] = round(sh + number(sz['balance_yi']), 2)
        sources[day] = {'date':day, 'sse_balance_yi':sh,
                        'szse_balance_yi':sz['balance_yi'],
                        'sse_source':'https://www.sse.com.cn/market/othersdata/margin/sum/',
                        'szse_source':sz['source_url'], 'collected_at':now.isoformat()}
    indices = out.setdefault('index', {})
    # Append missing index dates only. Never silently revise stored history.
    for name, code in producer.IDX.items():
        stored = indices.setdefault(name, {})
        needed = [day for day in rows if start <= day <= expected and day not in stored]
        if needed:
            fetched = producer.idx(code)
            for day in needed:
                if day in fetched:
                    stored[day] = number(fetched[day])
    rows = dict(sorted(rows.items()))
    if rows and (any(number(v) <= 0 for v in rows.values()) or max(rows) > now.date().isoformat()):
        raise ValueError('两融余额或数据日期无效')
    out['margin'] = rows
    # Partial candidates stay in memory, not in production.
    if margin_missing(out, expected):
        return None
    baseline = previous_trading_day(dt.date.fromisoformat(expected)).isoformat()
    if baseline not in rows:
        raise ValueError('缺少前一交易日变化基准，未写入')
    out['codes'] = producer.IDX
    out['updated'] = now.strftime('%Y-%m-%d %H:%M')
    return out


def margin(now, state, final=False):
    expected = previous_trading_day(now.date()).isoformat()
    path = ROOT/'data/margin_balance.json'
    old = json.loads(path.read_text())
    if not margin_missing(old, expected):
        verify_archive_pages(['data/margin_balance.json'])
        state['margin_success_data_date'] = expected
        state.pop('margin_success_day', None)
        return {'notify':False, 'status':'success', 'data_date':expected, 'reason':'目标数据日已齐全'}
    out = build_margin_update(old, now, expected)
    if out is None:
        missing = margin_missing(old, expected)
        if final:
            raise ValueError(f'披露后补查结束仍缺两融数据：{", ".join(missing)}；最新入库{max(old.get("margin",{}),default="无")}')
        return {'notify':False, 'status':'pending', 'data_date':expected,
                'reason':'目标数据日尚未齐全，需继续补查'}
    write_json_atomic(path, out, ensure_ascii=False, separators=(',',':'))
    commit_files(['data/margin_balance.json'], f'margin {expected}')
    verify_archive_pages(['data/margin_balance.json'])
    # Both Git push and public Pages bytes are checked before success.
    state['margin_success_data_date'] = expected
    state.pop('margin_success_day', None)
    return {'notify':False, 'status':'success', 'data_date':expected, 'reason':'官方同日数据已入库'}


def data_audit(now, state):
    """Independent read-only final check, even when collection never started."""
    expected = previous_trading_day(now.date()).isoformat()
    payload = json.loads((ROOT/'data/margin_balance.json').read_text())
    missing = margin_missing(payload, expected)
    date = now.date().isoformat()
    history = json.loads((ROOT/'data/history.json').read_text())
    minute_path = ROOT/f'data/minutes/{date}.json'
    if date not in history or not minute_path.exists():
        missing.append(date + '八宫格日终存档（缺日或错过采集窗口）')
    else:
        minute = json.loads(minute_path.read_text())
        if minute.get('date') != date:
            missing.append(date + '八宫格存档日期不匹配')
    if missing:
        raise ValueError('日终核对发现缺档：' + '，'.join(missing))
    return {'notify':False, 'status':'success', 'reason':'两融目标日和当日八宫格均已入库'}


def failure_result(task, now, state, reason):
    # Only reserve the dedupe marker for a final alert, never a pending retry.
    key = 'failure_notified_' + task
    day = now.date().isoformat()
    send = state.get(key) != day
    if send:
        state[key] = day
    return {'notify':send, 'status':'failed', 'message':task+'：'+reason,
            'sources':[REPO+'/blob/main/data/margin_balance.json',
                       REPO+'/blob/main/data/history.json']}


def live_health(now,state):
    result=json.loads(get('https://raw.githubusercontent.com/kakarottoisme/national-team-octagon/main/data/live.json?t='+str(int(now.timestamp()))))
    stamp=dt.datetime.fromisoformat(result['updated'])
    if stamp.tzinfo is None:
        stamp=stamp.replace(tzinfo=TZ)
    fresh=result.get('date')==now.strftime('%Y%m%d') and (now-stamp).total_seconds()<=1200
    if fresh:
        state['live_failures']=0
        return {'notify':False,'reason':'盘中数据正常'}
    state['live_failures']=state.get('live_failures',0)+1
    if state['live_failures']<3 or state.get('live_warned_day')==now.date().isoformat():
        return {'notify':False,'reason':'停更观察中或当日已提醒'}
    state['live_warned_day']=now.date().isoformat()
    return {'notify':True,'message':'盘中行情连续多次检查仍未更新，数据已停更超过20分钟；云端采集或推送可能延迟/失败。','sources':[REPO+'/blob/main/data/live.json']}


def execute_task(task, now, state, final=False, timing=None):
    """Window failures are business failures, not silent successful exits."""
    minute = now.hour*60 + now.minute
    windows = {'ic':[(882,887)], 'archive':[(910,930)],
               'margin':[(540,580)], 'data-audit':[(1080,1439)],
               'live-health':[(575,695),(805,905)]}
    try:
        if not is_trading_day(now.date()):
            return {'notify':False,'reason':'非交易日'}
        if task != 'archive' and not any(a <= minute <= b for a,b in windows[task]):
            if task in ('archive','margin','data-audit'):
                return failure_result(task, now, state, '错过授权采集/核对窗口，未采集；请核对数据日期')
            return {'notify':False,'reason':'不在授权窗口'}
        if task == 'ic': return ic(now)
        if task == 'archive':
            if archive_mode(now) is None:
                return failure_result(task, now, state, '错过授权窗口，未采集')
            result = archive(now,state,timing)
            if minute > 930:
                warning = failure_result(task,now,state,'错过15:10–15:30实时采集窗口；'+result.get('reason','已核对Git收盘快照'))
                warning['status'] = 'warning'
                return {**result, **warning}
            return result
        if task == 'margin': return margin(now,state, final or minute >= 565)
        if task == 'data-audit': return data_audit(now,state)
        return live_health(now,state)
    except Exception as exc:
        reason = str(exc) if isinstance(exc,ValueError) else type(exc).__name__+'（来源或执行失败）'
        if task in ('archive','margin','data-audit'):
            # Keep retryable early failures pending; no alert dedupe consumed.
            retry_before = 565 if task == 'margin' else 922
            if not final and task != 'data-audit' and minute < retry_before:
                return {'notify':False,'status':'pending','reason':reason}
            return failure_result(task, now, state, reason)
        if task == 'live-health':
            state['live_failures'] = state.get('live_failures',0)+1
            send = state['live_failures'] >= 3 and state.get('live_warned_day') != now.date().isoformat()
            if send: state['live_warned_day'] = now.date().isoformat()
        else:
            send = state.get('error_notified') != now.date().isoformat()
            if send: state['error_notified'] = now.date().isoformat()
        return {'notify':send,'message':task+'：'+reason}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('task',choices=['ic','archive','margin','data-audit','live-health'])
    p.add_argument('--state-dir',required=True)
    p.add_argument('--final',action='store_true')
    args=p.parse_args()
    now=dt.datetime.now(TZ)
    directory=Path(args.state_dir);directory.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    timing={'planned_at':now.replace(hour=15,minute=12,second=0,microsecond=0).isoformat(),'python_entered_at':now.isoformat(),'entry_offset_seconds':int((now-now.replace(hour=15,minute=12,second=0,microsecond=0)).total_seconds()),'task_received_at':os.environ.get('ARCHIVE_TASK_RECEIVED_AT'),'wrapper_started_at':os.environ.get('ARCHIVE_WRAPPER_STARTED_AT'),'checkout_ready_at':os.environ.get('ARCHIVE_CHECKOUT_READY_AT'),'clone_seconds':os.environ.get('ARCHIVE_CLONE_SECONDS')}
    with (directory/'lock').open('a') as lock:
        lock_started=time.monotonic()
        if args.task in ('archive','margin','data-audit'):
            fcntl.flock(lock,fcntl.LOCK_EX)
        timing['lock_wait_seconds']=round(time.monotonic()-lock_started,3)
        timing['lock_acquired_at']=dt.datetime.now(TZ).isoformat()
        path=directory/(args.task+'.json')
        state=json.loads(path.read_text()) if path.exists() else {}
        now=dt.datetime.now(TZ)  # Re-evaluate after lock wait; no source fetch past deadline.
        result=execute_task(args.task,now,state,args.final,timing)
        if args.task in ('archive','margin','data-audit'):
            state['last_attempt'] = {'at':now.isoformat(), 'task':args.task,
                                     'status':result.get('status','success' if not result.get('notify') else 'reported'),
                                     'data_date':result.get('data_date'),
                                     'reason':result.get('reason') or result.get('message'),
                                     'notify':result.get('notify',False)}
        if args.task=='archive':
            timing.update(finished_at=dt.datetime.now(TZ).isoformat(),execution_seconds=round(time.monotonic()-started,3),business_state=result.get('state',result.get('reason','failed')),notify=result.get('notify',False))
            state['last_execution']=timing
            with (directory/'archive-runs.jsonl').open('a',encoding='utf-8') as audit:
                audit.write(json.dumps(timing,ensure_ascii=False)+'\n')
        write_json_atomic(path,state,ensure_ascii=False,indent=2)
        if 'last_attempt' in state and args.task in ('archive','margin','data-audit'):
            with (directory/(args.task+'-attempts.jsonl')).open('a') as journal:
                journal.write(json.dumps(state['last_attempt'],ensure_ascii=False)+'\n')
        print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    main()
