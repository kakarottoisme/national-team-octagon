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
        code = url.rsplit('=', 1)[1]
        payload = raw['data'][code]['data']
        if payload['date'] != date.replace('-', ''):
            raise ValueError(f'{code} 分时不是目标交易日，未写入')
        times = [r.split()[0] for r in payload['data']]
        if len(times) < 200 or max(times) < '1500':
            raise ArchiveSourcePending(f'{code} 当日收盘分时不完整，未写入')
        previous = 0.
        for row in payload['data']:
            amount = number(row.split()[3])
            if amount < previous:
                raise ArchiveSourcePending(f'{code} 累计成交额倒退，未写入')
            previous = amount
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
    def validated(url):
        return fetch_archive_source(producer.g, url, date)
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


def margin(now, state):
    if state.get('margin_success_day')==now.date().isoformat():
        return {'notify':False,'reason':'本次交易日已完成新数据更新'}
    import margin as producer
    path=ROOT/'data/margin_balance.json'
    old=json.loads(path.read_text())
    old_latest=max(old.get('margin',{}),default='')
    with contextlib.redirect_stdout(io.StringIO()):
        # Macro feeds can lag for weeks. Retain stored history, supplement the
        # recent SSE dates with same-date SZSE official values (亿元).
        rows=dict(old.get('margin',{}))
        start=(dt.date.fromisoformat(old_latest)-dt.timedelta(days=7)).strftime('%Y%m%d') if old_latest else (now.date()-dt.timedelta(days=14)).strftime('%Y%m%d')
        recent=producer.ak.stock_margin_sse(start_date=start,end_date=now.strftime('%Y%m%d'))
        for record in recent.to_dict('records'):
            day=dt.datetime.strptime(str(record['信用交易日期']),'%Y%m%d').date().isoformat()
            if day <= old_latest or day > now.date().isoformat():
                continue
            sz=producer.ak.stock_margin_szse(date=day.replace('-',''))
            if sz is None or len(sz)==0:
                continue
            rows[day]=round(number(record['融资融券余额'])/1e8+number(sz['融资融券余额'].iloc[0]),2)
    rows=dict(sorted(rows.items()))
    if not rows:
        raise ValueError('沪深两融余额来源为空')
    latest=max(rows)
    if latest<=old_latest:
        return {'notify':False,'reason':'交易所尚无更新交易日'}
    if latest>now.date().isoformat() or not is_trading_day(dt.date.fromisoformat(latest)):
        raise ValueError('两融来源日期无效')
    dates=sorted(rows)
    if len(dates)<2:
        raise ValueError('缺少较前一交易日变化基准')
    for k,v in rows.items():
        if not math.isfinite(number(v)) or number(v)<=0:
            raise ValueError('两融余额无效')
    if any(k not in rows for k in old.get('margin',{})):
        raise ValueError('两融来源缺少既有历史，停止覆盖')
    indices={k:producer.idx(c) for k,c in producer.IDX.items()}
    if any(latest not in values for values in indices.values()):
        raise ValueError('相关指数同日收盘缺失，停止覆盖')
    out={'updated':now.strftime('%Y-%m-%d %H:%M'),'codes':producer.IDX,'margin':rows,'index':indices}
    write_json_atomic(path,out,ensure_ascii=False,separators=(',',':'))
    pushed=commit_files(['data/margin_balance.json'],f'margin {latest}')
    if not pushed:
        return {'notify':False,'reason':'无变化'}
    state['margin_success_day']=now.date().isoformat()
    change=rows[latest]-rows[dates[-2]]
    return {'notify':True,'message':f'{latest}｜两融余额{rows[latest]:.2f}亿元｜较前一交易日{change:+.2f}亿元','link':PAGE+'margin.html','sources':[REPO+'/blob/main/data/margin_balance.json']}


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


def main():
    p=argparse.ArgumentParser()
    p.add_argument('task',choices=['ic','archive','margin','live-health'])
    p.add_argument('--state-dir',required=True)
    args=p.parse_args()
    now=dt.datetime.now(TZ)
    minute=now.hour*60+now.minute
    windows={'ic':[(882,887)],'archive':[(910,930)],'margin':[(990,1020)],'live-health':[(575,695),(805,905)]}
    if args.task!='archive' and not any(a<=minute<=b for a,b in windows[args.task]):
        print(json.dumps({'notify':False,'reason':'不在授权窗口'},ensure_ascii=False));return
    if args.task=='archive' and archive_mode(now) is None:
        print(json.dumps({'notify':False,'reason':'不在授权窗口'},ensure_ascii=False));return
    directory=Path(args.state_dir);directory.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    timing={'planned_at':now.replace(hour=15,minute=12,second=0,microsecond=0).isoformat(),'python_entered_at':now.isoformat(),'entry_offset_seconds':int((now-now.replace(hour=15,minute=12,second=0,microsecond=0)).total_seconds()),'task_received_at':os.environ.get('ARCHIVE_TASK_RECEIVED_AT'),'wrapper_started_at':os.environ.get('ARCHIVE_WRAPPER_STARTED_AT'),'checkout_ready_at':os.environ.get('ARCHIVE_CHECKOUT_READY_AT'),'clone_seconds':os.environ.get('ARCHIVE_CLONE_SECONDS')}
    with (directory/'lock').open('a') as lock:
        lock_started=time.monotonic()
        if args.task in ('archive','margin'):
            fcntl.flock(lock,fcntl.LOCK_EX)
        timing['lock_wait_seconds']=round(time.monotonic()-lock_started,3)
        timing['lock_acquired_at']=dt.datetime.now(TZ).isoformat()
        path=directory/(args.task+'.json')
        state=json.loads(path.read_text()) if path.exists() else {}
        try:
            if not is_trading_day(now.date()):
                result={'notify':False,'reason':'非交易日'}
            elif args.task=='ic': result=ic(now)
            elif args.task=='archive':
                # Re-evaluate after waiting for the lock; never fetch past 15:30.
                result=archive(dt.datetime.now(TZ),state,timing)
            elif args.task=='margin': result=margin(now,state)
            else: result=live_health(now,state)
        except Exception as exc:
            # No fabricated numbers; deduplicate failures once per task per date.
            if args.task=='live-health':
                state['live_failures']=state.get('live_failures',0)+1
                send=state['live_failures']>=3 and state.get('live_warned_day')!=now.date().isoformat()
                if send: state['live_warned_day']=now.date().isoformat()
            else:
                send=state.get('error_notified')!=now.date().isoformat()
                if send: state['error_notified']=now.date().isoformat()
            # Avoid echoing credentials from git/HTTP exceptions.
            reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__+'（来源或执行失败）'
            result={'notify':send,'message':args.task+'：'+reason}
        if args.task=='archive':
            timing.update(finished_at=dt.datetime.now(TZ).isoformat(),execution_seconds=round(time.monotonic()-started,3),business_state=result.get('state',result.get('reason','failed')),notify=result.get('notify',False))
            state['last_execution']=timing
            with (directory/'archive-runs.jsonl').open('a',encoding='utf-8') as audit:
                audit.write(json.dumps(timing,ensure_ascii=False)+'\n')
        write_json_atomic(path,state,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    main()
