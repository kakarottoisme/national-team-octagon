#!/usr/bin/env python3
"""Resume-safe full official CFFEX member history; no production writes.

Requests every weekday from each listing day through the day before the existing
archive. Weekend not-published records are explicit; unavailable responses are
NOT called holidays. Checkpoint records and original CSVs are preserved.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('futures_history',ROOT/'preview-tools/futures_history.py')
fh=importlib.util.module_from_spec(spec);spec.loader.exec_module(fh)
LISTING={'IF':'2010-04-16','IH':'2015-04-16','IC':'2015-04-16','IM':'2022-07-22'}
LISTING_SOURCE='http://www.cffex.com.cn/u/cms/www/202605/291709222j1b.pdf'
lock=threading.Lock();last_start=0


def get(url):
    global last_start
    with lock:
        delay=max(0,.08-(time.monotonic()-last_start))
        if delay:time.sleep(delay)
        last_start=time.monotonic()
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'http://www.cffex.com.cn/'})
    try:
        with urllib.request.urlopen(req,timeout=14) as r:status,raw=r.status,r.read()
    except urllib.error.HTTPError as e:
        if e.code==404:return 404,b''
        raise
    # The official static server returns a recognizable 404 template with HTTP200.
    # Preserve source-unavailable, not holiday, status. Other HTML stays an error.
    if b'404_bg.gif' in raw and b'404_1.gif' in raw:return 404,raw
    return status,raw


def collect(task,folder):
    p,d=task;raw_dir=folder/'raw'/p/d[:4]
    cached=raw_dir/(p+'_'+d.replace('-','')+'.csv')
    def fetch(url):return (200,cached.read_bytes()) if cached.exists() else get(url)
    row=fh.backfill_one_day(p,dt.date.fromisoformat(d),raw_dir=str(raw_dir),fetcher=fetch)
    if row['source_status']=='not_published':row['note']='Official dated file unavailable; not inferred to be a holiday'
    return p,d,row


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--workers',type=int,default=6);args=ap.parse_args()
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=True)
    (folder/'staged').mkdir(exist_ok=True)
    old={p:json.loads((ROOT/'preview-data/official/futures'/f'{p}.json').read_text()) for p in LISTING}
    records={p:{} for p in LISTING};checkpoint=folder/'checkpoint.jsonl'
    if checkpoint.exists():
        for line in checkpoint.read_text().splitlines():
            x=json.loads(line);records[x['product']][x['date']]=x['record']
    tasks=[]
    for p,start in LISTING.items():
        day=dt.date.fromisoformat(start);end=dt.date.fromisoformat(old[p]['range_start'])-dt.timedelta(days=1)
        while day<=end:
            d=day.isoformat()
            if d not in records[p] or records[p][d]['source_status']=='error':
                if day.weekday()<5:tasks.append((p,d))
                else:
                    row=fh.backfill_one_day(p,day,fetcher=lambda _:(404,b''));row['note']='Weekend; not a member observation';records[p][d]=row
            day+=dt.timedelta(days=1)
    print(json.dumps({'remaining_requests':len(tasks),'starts':LISTING}),flush=True)
    done=0;started=time.monotonic()
    with checkpoint.open('a') as out,concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(collect,task,folder):task for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            p,d,row=future.result();records[p][d]=row
            out.write(json.dumps({'product':p,'date':d,'record':row},ensure_ascii=False)+'\n');out.flush()
            done+=1
            if done%300==0:print(json.dumps({'done':done,'total':len(tasks),'seconds':round(time.monotonic()-started),'errors':sum(v['source_status']=='error' for recs in records.values() for v in recs.values())}),flush=True)
    summary={}
    for p in LISTING:
        shard=old[p];combined=dict(sorted({**records[p],**shard['records']}.items()))
        shard['records']=combined;shard['range_start']=min(combined)
        shard['coverage']={'date_count':len(combined),'published':sum(v['source_status']=='published' for v in combined.values()),'missing':[d for d,v in combined.items() if v['source_status']!='published']}
        shard['full_backfill']={'listing_date':LISTING[p],'listing_source':LISTING_SOURCE,'earliest_published':next((d for d,v in combined.items() if v['source_status']=='published'),None),'attempted_every_weekday_before_existing':True,'unavailable_is_not_assumed_holiday':True}
        (folder/'staged'/f'{p}.json').write_text(json.dumps(shard,ensure_ascii=False,indent=1)+'\n')
        summary[p]={'published':shard['coverage']['published'],'start':shard['full_backfill']['earliest_published'],'end':max(combined),'errors':[d for d,v in records[p].items() if v['source_status']=='error'],'not_published_weekdays':sum(v['source_status']=='not_published' and dt.date.fromisoformat(d).weekday()<5 for d,v in records[p].items()),'net_points':sum(v.get('net_short') is not None for v in combined.values())}
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
