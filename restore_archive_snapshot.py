#!/usr/bin/env python3
"""Restore a missing day from a preserved Git live snapshot, without any quote fetch.

Only history.json and minutes/<date>.json may be written with --apply.
Existing archive entries, live, holdings, strategies and schedules are untouched.
"""
from __future__ import annotations
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import subprocess

from backend_common import ETF_UNIVERSE, estimated_large_minutes
from backend_io import write_json_atomic
from backfill_index_minutes import canonical_date, expected_times, merge_indices
from index_quotes import INDEX_UNIVERSE

ROOT=Path(__file__).resolve().parent


def positive(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
        raise ValueError('Invalid price or previous close')


def build(snapshot, date, provenance):
    date=canonical_date(date)
    if canonical_date(snapshot.get('date',''))!=date:raise ValueError('Snapshot date mismatch')
    stamp=dt.datetime.fromisoformat(snapshot['updated'])
    if stamp.date().isoformat()!=date or stamp.time()<dt.time(15):raise ValueError('Snapshot is not after close on the target day')
    codes={c for _,c in ETF_UNIVERSE}
    if set(snapshot.get('etfs',{}))!=codes:raise ValueError('Incomplete ETF universe')
    summary={}
    required=set(expected_times(1))
    for name,code in ETF_UNIVERSE:
        row=snapshot['etfs'][code]
        if canonical_date(row.get('date',''))!=date:raise ValueError('Mixed ETF dates')
        ticks,prices,amounts=row['t'],row['p'],row['a']
        if not(len(ticks)==len(prices)==len(amounts)) or not required.issubset(ticks):raise ValueError('Incomplete or misaligned ETF minutes')
        if ticks!=sorted(set(ticks)) or any(not isinstance(t,str) or len(t)!=4 or not t.isdigit() or int(t[:2])>23 or int(t[2:])>59 for t in ticks):raise ValueError('Unordered or invalid ETF minutes')
        positive(row.get('pre'))
        for p in prices:positive(p)
        for a in amounts:
            if isinstance(a,bool) or not isinstance(a,(int,float)) or not math.isfinite(a) or a<0:raise ValueError('Invalid minute amount; no clamping or omission')
        summary[name]=estimated_large_minutes(amounts)
    summary['合计']=round(sum(summary.values()),2)
    kline=snapshot['sse'];i=next((i for i,r in enumerate(kline) if r[0]==date),None)
    if i is None or i==0:raise ValueError('Missing same-day SSE close or previous reference')
    if kline[i-1][0]>=date:raise ValueError('Invalid SSE previous reference')
    close,previous=float(kline[i][2]),float(kline[i-1][2]);positive(close);positive(previous)
    summary['上证收盘']=close;summary['上证涨跌幅']=round((close/previous-1)*100,2)
    # Preserve the original observation; never replace missing estimates with zeros.
    if snapshot.get('estimates')!=summary:raise ValueError('Recomputed summary differs from preserved live estimates')
    minutes={'date':date,'etfs':copy.deepcopy(snapshot['etfs']),'archive_provenance':copy.deepcopy(provenance)}
    indices={}
    for _,code in INDEX_UNIVERSE:
        if code not in snapshot.get('index_minutes',{}):raise ValueError('Incomplete index universe')
        row=copy.deepcopy(snapshot['index_minutes'][code])
        row.update(interval_minutes=1,source='tencent_minutes_preserved_snapshot',source_url=provenance['source_url'],price_type='minute_quote',interpolated=False)
        indices[code]=row
    minutes=merge_indices(minutes,indices)
    return summary,minutes


def apply_missing(directory,date,summary,minutes):
    directory=Path(directory);hpath=directory/'history.json';mpath=directory/'minutes'/f'{date}.json'
    history=json.loads(hpath.read_text(encoding='utf-8'))
    if date in history or mpath.exists():
        if date in history and mpath.exists() and history[date]==summary and json.loads(mpath.read_text())==minutes:return False
        raise ValueError('Existing archive entry: refusing overwrite')
    history[date]=summary
    write_json_atomic(mpath,minutes,separators=(',',':'))
    write_json_atomic(hpath,dict(sorted(history.items())),ensure_ascii=False,indent=1)
    return True


def from_git(root, date):
    """Find the newest valid same-day preserved close; deepen only that day's history.

    Reading Git history is not a quote fetch. Do not fall back to another date or
    edit the observation when a series is invalid. Candidates remain immutable.
    """
    root=Path(root);date=canonical_date(date)
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE)
    if git('rev-parse','--is-shallow-repository').strip()==b'true':
        git('fetch','--quiet','--shallow-since='+date+'T00:00:00+08:00','origin','main')
    commits=git('log','--format=%H','--since='+date+'T00:00:00+08:00','--until='+date+'T23:59:59+08:00','HEAD','--','data/live.json').decode().splitlines()
    for commit in commits:
        raw=git('show',commit+':data/live.json')
        try:
            snapshot=json.loads(raw)
            if canonical_date(snapshot.get('date',''))!=date:continue
            provenance={'kind':'preserved_live_snapshot','source_commit':commit,'source_url':'https://github.com/kakarottoisme/national-team-octagon/commit/'+commit,'snapshot_updated':snapshot['updated'],'snapshot_sha256':hashlib.sha256(raw).hexdigest(),'note':'Newest validated complete same-day close snapshot; no quote fetch or interpolation'}
            summary,minutes=build(snapshot,date,provenance)
            return summary,minutes
        except (ValueError,TypeError,KeyError,OverflowError):
            continue
    raise ValueError('没有可验证的当日完整Git收盘快照，未补档；缺点不补0、不借用其他日期')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot-commit',required=True);p.add_argument('--date',required=True)
    p.add_argument('--data-dir',type=Path,default=ROOT/'data');p.add_argument('--apply',action='store_true')
    args=p.parse_args();date=canonical_date(args.date)
    commit=subprocess.check_output(['git','rev-parse',args.snapshot_commit+'^{commit}'],cwd=ROOT,text=True).strip()
    raw=subprocess.check_output(['git','show',commit+':data/live.json'],cwd=ROOT)
    provenance={'kind':'preserved_live_snapshot','source_commit':commit,'source_url':'https://github.com/kakarottoisme/national-team-octagon/commit/'+commit,'snapshot_updated':json.loads(raw)['updated'],'snapshot_sha256':hashlib.sha256(raw).hexdigest(),'note':'Original same-day complete close snapshot, recovered without fetching or interpolation'}
    summary,minutes=build(json.loads(raw),date,provenance)
    changed=apply_missing(args.data_dir,date,summary,minutes) if args.apply else False
    print(json.dumps({'date':date,'summary':summary,'etf_points':{c:len(m['t']) for c,m in minutes['etfs'].items()},'index_points':{c:len(m['t']) for c,m in minutes['index_minutes'].items()},'applied':changed,'source_url':provenance['source_url']},ensure_ascii=False))

if __name__=='__main__':main()
