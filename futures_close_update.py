#!/usr/bin/env python3
"""Post-close incremental updates for the existing two futures workflows.

No orders, no account writes, no formula changes. Already-published dates are
idempotent. Pending/invalid sources leave the last valid files untouched.
"""
from __future__ import annotations
import argparse
import contextlib
import copy
import csv
import datetime as dt
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from backend_io import write_json_atomic, write_text_atomic
from market_calendar import TZ, is_trading_day
import ic_strategy as ic

ROOT=Path(__file__).resolve().parent
TOOLS=ROOT/'preview-tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))
from futures_history import backfill_one_day, validate_shard
PRODUCTS=('IH','IF','IC','IM')


def source_get(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
    try:
        with urllib.request.urlopen(request,timeout=18) as response:
            return response.status,response.read()
    except urllib.error.HTTPError as exc:
        if exc.code==404:return 404,b''
        raise


def permitted(now,calendar=is_trading_day):
    now=now.astimezone(TZ)
    return now.hour*60+now.minute>=15*60+30 and calendar(now.date())


def strategy_target_day(now, calendar=is_trading_day):
    """Latest completed official session, not the delayed runner's calendar date."""
    now = now.astimezone(TZ)
    day = now.date()
    if now.hour * 60 + now.minute < 15 * 60 + 30:
        day -= dt.timedelta(days=1)
    for _ in range(370):
        if calendar(day):
            return day
        day -= dt.timedelta(days=1)
    raise ValueError('无法确认最近已收盘交易日，停止')


def validate_strategy_date(payload, expected):
    days = payload.get('days', [])
    dates = [row.get('date') for row in days]
    if (not dates or any(not isinstance(x, str) for x in dates) or
            dates != sorted(set(dates)) or payload.get('latest') != dates[-1]):
        raise ValueError('信号latest与历史日期不一致，保留原文件')
    if any(dt.date.fromisoformat(x).isoformat() != x for x in dates):
        raise ValueError('信号日期格式无效，保留原文件')
    if dates[-1] > expected:
        raise ValueError('信号日期晚于已收盘交易日，停止')
    return dates[-1] == expected


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def run_members(root,day,fetch=source_get):
    day=day.isoformat();folder=Path(root)/'preview-data/official/futures'
    originals={p:load(folder/f'{p}.json') for p in PRODUCTS}
    pending=[p for p in PRODUCTS if originals[p]['records'].get(day,{}).get('source_status')!='published']
    results={p:{'state':'already_current','date':day} for p in PRODUCTS if p not in pending}
    def get(p):return p,backfill_one_day(p,dt.date.fromisoformat(day),fetcher=fetch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched=list(pool.map(get,pending))
    changed=[]
    for p,record in fetched:
        if record['source_status']!='published':
            results[p]={'state':record['source_status'],'date':day,'reason':record.get('note','')}
            continue
        if record['trading_date']!=day or record['source_date_evidence']!=day:
            raise ValueError('会员源日期不一致，保留原文件')
        candidate=copy.deepcopy(originals[p]);candidate['records'][day]=record
        dates=sorted(candidate['records']);candidate['range_start']=dates[0];candidate['range_end']=dates[-1]
        candidate['collected_at']=dt.datetime.now(TZ).isoformat(timespec='seconds')
        coverage=dict(candidate.get('coverage',{}));missing=[d for d,r in candidate['records'].items() if r.get('source_status')!='published']
        coverage.update(date_count=len(dates),published=len(dates)-len(missing))
        if 'missing' in coverage:coverage['missing']=missing
        if 'not_published' in coverage:
            absent=[d for d,r in candidate['records'].items() if r.get('source_status')=='not_published']
            errors=[d for d,r in candidate['records'].items() if r.get('source_status') not in ('published','not_published')]
            coverage.update(not_published=len(absent),not_published_dates=absent,errors=len(errors),error_dates=errors)
        candidate['coverage']=coverage
        validate_shard(candidate)
        for d,old in originals[p]['records'].items():
            if d!=day and candidate['records'][d]!=old:raise ValueError('原会员历史变动，停止')
        target=folder/f'{p}.json'
        if load(target)!=originals[p]:raise ValueError('会员文件并发更新，停止')
        write_json_atomic(target,candidate,ensure_ascii=False,indent=1)
        changed.append(str(target.relative_to(root)))
        results[p]={'state':'updated','date':day,'source_url':record['source_url']}
    complete=all(v['state'] in ('updated','already_current') for v in results.values())
    return {'state':'current' if complete else 'pending','date':day,'changed':changed,'products':results}


def member_missing_days(root, day, calendar=is_trading_day):
    """Missing official sessions since each product's last published day.

    Also check recent internal holes; a later successful day must not hide a gap.
    Never initialise a shard or overwrite an already-published record.
    """
    jobs = set()
    for product in PRODUCTS:
        shard = load(Path(root)/f'preview-data/official/futures/{product}.json')
        validate_shard(shard)
        records = shard.get('records', {})
        published = sorted(d for d, row in records.items() if row.get('source_status') == 'published')
        if not published or published[-1] > day.isoformat():
            raise ValueError('会员历史缺失或日期晚于已收盘交易日，停止')
        start = min(dt.date.fromisoformat(published[-1]) + dt.timedelta(days=1),
                    max(dt.date.fromisoformat(published[0]), day-dt.timedelta(days=20)))
        cursor = start
        while cursor <= day:
            ds = cursor.isoformat()
            if calendar(cursor) and records.get(ds, {}).get('source_status') != 'published':
                jobs.add(ds)
            cursor += dt.timedelta(days=1)
    return sorted(jobs)


def verify_members_date(root, day):
    missing = member_missing_days(root, day)
    if missing:
        raise ValueError('期指会员缺少官方交易日：' + ', '.join(missing))
    for product in PRODUCTS:
        record = load(Path(root)/f'preview-data/official/futures/{product}.json')['records'].get(day.isoformat(), {})
        if (record.get('source_status') != 'published' or
                record.get('trading_date') != day.isoformat() or
                record.get('source_date_evidence') != day.isoformat() or
                not record.get('source_sha256')):
            raise ValueError(product + ' 目标日/原始来源证据不完整')
    return True


def run_members_catchup(root, day, fetch=source_get):
    results = [run_members(root, dt.date.fromisoformat(ds), fetch)
               for ds in member_missing_days(root, day)]
    missing = member_missing_days(root, day)
    changed = sorted({p for result in results for p in result['changed']})
    if not missing:
        verify_members_date(root, day)
    return {'state': 'pending' if missing else 'current', 'date': day.isoformat(),
            'changed': changed, 'missing_dates': missing, 'days': results}


def parse_official_closes(raw,day):
    """Date evidence is the dated CFFEX URL; CSV uses named 今收盘, not positional settlement."""
    rows=csv.DictReader(io.StringIO(raw.decode('gbk')))
    if not rows.fieldnames or not {'合约代码','今收盘'}<=set(rows.fieldnames):
        raise ValueError('官方日行情表头不完整')
    result={}
    for row in rows:
        code=row['合约代码'].strip()
        if not re.fullmatch(r'IC\d{4}',code):continue
        price=float(row['今收盘'])
        if not math.isfinite(price) or price<=0:raise ValueError('官方今收盘无效')
        if code in result:raise ValueError('官方合约重复')
        result[code]=round(price,1)
    return result


def approve_strategy(old,candidate,day,index_rows,official,official_days=None):
    """Approve new days only; full-history replay never overwrites older records."""
    if candidate.get('latest')!=day or index_rows[-1][0]!=day:raise ValueError('信号日期不一致')
    rows={d['date']:d for d in candidate['days']}
    if len(rows)!=len(candidate['days']):raise ValueError('候选信号日期重复')
    old_days=old.get('days',[])
    if not old_days:raise ValueError('缺少原历史，禁止自动初始化模型')
    last=old_days[-1]['date']
    if last not in rows or rows[last]!=old_days[-1]:
        raise ValueError('重放与上次发布状态不一致，保留历史，需单独核验')
    added=[d for d in candidate['days'] if last<d['date']<=day]
    expected=[d for d,_ in index_rows if last<d<=day]
    if [r['date'] for r in added]!=expected or not added:raise ValueError('新增信号日期覆盖不完整')
    for item in added:
        i = next(i for i, row in enumerate(index_rows) if row[0] == item['date'])
        if item['index_close'] != index_rows[i][1]:raise ValueError('信号指数收盘不一致')
        ma=math.fsum(x[1] for x in index_rows[i-249:i+1])/250
        for key,value in [('ma250',ma),('ma_upper',ma*1.01),('ma_lower',ma*.99)]:
            if item[key]!=round(value,2):raise ValueError('均线核验失败')
        closes = official if official_days is None else official_days[item['date']]
        if len(item['contracts'])!=4:raise ValueError('缺少挂牌合约')
        for row in item['contracts']:
            if row['code'] not in closes or row['price']!=closes[row['code']]:raise ValueError('合约今收盘缺失或不一致')
            basis=None if row['days']<=0 else round((item['index_close']-row['price'])/item['index_close']*365/row['days'],6)
            if row['basis']!=basis:raise ValueError('合约贴水核验失败')
    result={**candidate,'days':old_days+added}
    if result['days'][:len(old_days)]!=old_days:raise ValueError('旧历史被修改')
    return result


def run_strategy(root,day,fetch=source_get,engine=ic.main):
    root=Path(root);target=root/'data/ic_strategy.json';old=load(target);date=day.isoformat()
    if validate_strategy_date(old,date):return {'state':'already_current','date':date,'changed':[]}
    status,raw_index=fetch(ic.INDEX_URL)
    if status!=200 or not raw_index:return {'state':'pending_index','date':date,'changed':[]}
    parsed=ic.parse_jsonp_array(raw_index.decode('utf-8','replace'))
    # The feed may already contain today's unfinished bar during a delayed run.
    parsed=[r for r in parsed if isinstance(r,dict) and 'day' in r and 'close' in r and r['day'][:10]<=date]
    index_rows=sorted((r['day'][:10],round(float(r['close']),3)) for r in parsed)
    if len(index_rows)<250 or index_rows[-1][0]!=date:
        return {'state':'pending_index','date':date,'changed':[]}
    if len({d for d,_ in index_rows}) != len(index_rows) or any(not math.isfinite(p) or p<=0 for _,p in index_rows):
        raise ValueError('指数日期重复或收盘无效，保留原文件')
    last=old['days'][-1]['date']
    dates=[d for d,_ in index_rows if last<d<=date]
    cursor=dt.date.fromisoformat(last)+dt.timedelta(days=1)
    expected=[]
    while cursor<=day:
        if is_trading_day(cursor):expected.append(cursor.isoformat())
        cursor+=dt.timedelta(days=1)
    if dates!=expected:raise ValueError('新增指数交易日有缺口，保留原文件')
    official_days={};official_raw={};urls=[]
    for ds in dates:
        source_day=dt.date.fromisoformat(ds);ymd=source_day.strftime('%Y%m%d')
        url=ic.CFFEX_URL.format(ym=ymd[:6],dd=ymd[6:],ymd=ymd)
        status,raw_official=fetch(url)
        if status==404 or not raw_official:return {'state':'pending_official','date':date,'missing_date':ds,'changed':[]}
        if status!=200:raise ValueError('官方日行情读取失败')
        official_days[ds]=parse_official_closes(raw_official,source_day)
        slots=ic.list_slots(source_day,ic.infer_holidays([d for d,_ in index_rows]))
        if not all(s['code'] in official_days[ds] for s in slots):
            return {'state':'pending_official','date':date,'missing_date':ds,'changed':[]}
        official_raw[url]=raw_official;urls.append(url)
    raw_index=('x('+json.dumps(parsed,ensure_ascii=False)+');').encode('utf-8')
    with tempfile.TemporaryDirectory(prefix='ic-close-') as temp:
        cache=str(Path(temp)/'source-cache');out=Path(temp)/'candidate.json'
        for source,raw in [(ic.INDEX_URL,raw_index),*official_raw.items()]:
            p=ic._cache_path(cache,source);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        write_text_atomic(out,ic.dump_payload(old))
        with contextlib.redirect_stdout(io.StringIO()):engine(['--out',str(out),'--cache',cache])
        approved=approve_strategy(old,load(out),date,index_rows,official_days[date],official_days)
    if load(target)!=old:raise ValueError('信号文件并发更新，停止')
    write_text_atomic(target,ic.dump_payload(approved))
    return {'state':'updated','date':date,'changed':['data/ic_strategy.json'],'source_urls':[ic.INDEX_URL,*urls]}


def main(argv=None,now=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['members','strategy'],required=True)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--verify-date',help='Read-only business-date gate after push and Pages verification')
    args=parser.parse_args(argv);now=now or dt.datetime.now(TZ)
    try:
        if args.verify_date is not None:
            day=dt.date.fromisoformat(args.verify_date)
            if not is_trading_day(day) or day>strategy_target_day(now):raise ValueError('验收日不是已收盘交易日')
            if args.mode=='strategy':
                if not validate_strategy_date(load(args.root/'data/ic_strategy.json'),day.isoformat()):
                    raise ValueError('缺少应有信号日 '+day.isoformat())
            else:
                verify_members_date(args.root, day)
            result={'state':'data_date_verified','date':day.isoformat(),'changed':[]}
        elif args.mode=='strategy':
            day=strategy_target_day(now)
            result=run_strategy(args.root,day)
        else:
            day=strategy_target_day(now)
            result=run_members_catchup(args.root,day)
        print(json.dumps(result,ensure_ascii=False))
        if result['state'] not in ('updated','already_current','current','data_date_verified'):
            return 1 # Pending is not a successful data update; subsequent schedules can retry.
        return 0
    except (Exception,SystemExit) as exc:
        print(json.dumps({'state':'failed_keep_previous','changed':[],'reason':str(exc)},ensure_ascii=False));return 1

if __name__=='__main__':sys.exit(main())
