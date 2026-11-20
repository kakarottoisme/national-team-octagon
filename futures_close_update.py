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


def approve_strategy(old,candidate,day,index_rows,official):
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
    latest=added[-1]
    if latest['index_close']!=index_rows[-1][1]:raise ValueError('信号指数收盘不一致')
    ma=math.fsum(x[1] for x in index_rows[-250:])/250
    for key,value in [('ma250',ma),('ma_upper',ma*1.01),('ma_lower',ma*.99)]:
        if latest[key]!=round(value,2):raise ValueError('均线核验失败')
    if len(latest['contracts'])!=4:raise ValueError('缺少挂牌合约')
    for row in latest['contracts']:
        if row['code'] not in official or row['price']!=official[row['code']]:raise ValueError('合约今收盘缺失或不一致')
        basis=None if row['days']<=0 else round((latest['index_close']-row['price'])/latest['index_close']*365/row['days'],6)
        if row['basis']!=basis:raise ValueError('合约贴水核验失败')
    result={**candidate,'days':old_days+added}
    if result['days'][:len(old_days)]!=old_days:raise ValueError('旧历史被修改')
    return result


def run_strategy(root,day,fetch=source_get,engine=ic.main):
    root=Path(root);target=root/'data/ic_strategy.json';old=load(target);date=day.isoformat()
    if old.get('latest','')>=date:return {'state':'already_current','date':date,'changed':[]}
    status,raw_index=fetch(ic.INDEX_URL)
    if status!=200 or not raw_index:return {'state':'pending_index','date':date,'changed':[]}
    parsed=ic.parse_jsonp_array(raw_index.decode('utf-8','replace'))
    index_rows=sorted((r['day'][:10],round(float(r['close']),3)) for r in parsed if isinstance(r,dict) and 'day' in r and 'close' in r)
    if len(index_rows)<250 or index_rows[-1][0]!=date:
        return {'state':'pending_index','date':date,'changed':[]}
    ymd=day.strftime('%Y%m%d');url=ic.CFFEX_URL.format(ym=ymd[:6],dd=ymd[6:],ymd=ymd)
    status,raw_official=fetch(url)
    if status==404 or not raw_official:return {'state':'pending_official','date':date,'changed':[]}
    if status!=200:raise ValueError('官方日行情读取失败')
    official=parse_official_closes(raw_official,day)
    slots=ic.list_slots(day,ic.infer_holidays([d for d,_ in index_rows]))
    if not all(s['code'] in official for s in slots):return {'state':'pending_official','date':date,'changed':[]}
    with tempfile.TemporaryDirectory(prefix='ic-close-') as temp:
        cache=str(Path(temp)/'source-cache');out=Path(temp)/'candidate.json'
        for source,raw in [(ic.INDEX_URL,raw_index),(url,raw_official)]:
            p=ic._cache_path(cache,source);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        write_text_atomic(out,ic.dump_payload(old))
        with contextlib.redirect_stdout(io.StringIO()):engine(['--out',str(out),'--cache',cache])
        approved=approve_strategy(old,load(out),date,index_rows,official)
    if load(target)!=old:raise ValueError('信号文件并发更新，停止')
    write_text_atomic(target,ic.dump_payload(approved))
    return {'state':'updated','date':date,'changed':['data/ic_strategy.json'],'source_urls':[ic.INDEX_URL,url]}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['members','strategy'],required=True)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args(argv);now=dt.datetime.now(TZ)
    try:
        if not permitted(now):result={'state':'outside_close_window_or_nontrading','changed':[]}
        elif args.mode=='members':result=run_members(args.root,now.date())
        else:result=run_strategy(args.root,now.date())
        print(json.dumps(result,ensure_ascii=False));return 0
    except (Exception,SystemExit) as exc:
        print(json.dumps({'state':'failed_keep_previous','changed':[],'reason':str(exc)},ensure_ascii=False));return 1

if __name__=='__main__':sys.exit(main())
