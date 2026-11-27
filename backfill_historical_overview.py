#!/usr/bin/env python3
"""Recover historical overview only from preserved, dated Git snapshots.

No network fetch, no changes to existing minute/history archives. A historical
baseline is accepted only when all 20-day samples precede the observation day.
"""
import hashlib
import json
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CODES = ('sh510050','sh510300','sz159352','sh510500','sh512100','sz159915','sh588000')


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)


def snapshots(path):
    revs=git('log','HEAD','--format=%H','--',path).decode().splitlines()
    raw=subprocess.check_output(['git','cat-file','--batch'],cwd=ROOT,input=('\n'.join(r+':'+path for r in revs)+'\n').encode())
    pos=0
    for rev in revs:
        end=raw.index(b'\n',pos);header=raw[pos:end].split();pos=end+1
        if len(header)!=3:continue
        size=int(header[2]);body=raw[pos:pos+size];pos+=size+1
        try:doc=json.loads(body)
        except (ValueError,UnicodeDecodeError):continue
        yield rev,doc,hashlib.sha256(body).hexdigest()


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def iso(day):
    text=str(day or '')
    return text if '-' in text else text[:4]+'-'+text[4:6]+'-'+text[6:8]


def complete_close(doc,day):
    if iso(doc.get('date'))!=day:return False
    for c in CODES:
        m=(doc.get('etfs') or {}).get(c) or {}
        t=m.get('t') or [];a=m.get('a') or [];p=m.get('p') or []
        if iso(m.get('date',doc.get('date')))!=day:return False
        if len(t)!=242 or len(a)!=242 or len(p)!=242 or len(set(t))!=242:return False
        if t[0]!='0930' or t[-1]!='1500':return False
        if any(not finite(v) or v<0 for v in a):return False
        if any(not finite(v) or v<=0 for v in p):return False
        if not finite(m.get('amt')) or m['amt']<=0:return False
    return str(doc.get('updated','')).startswith(day+' ') and doc['updated'][11:16]>='15:00'


def valid_base(base,day):
    if str(base.get('generated_at',''))[:10]>day:return False
    for c in CODES:
        b=(base.get('codes') or {}).get(c) or {}
        if b.get('sample_days')!=20 or not b.get('last_day') or b['last_day']>=day:return False
        curve=b.get('curve') or []
        if len(curve)!=48 or not finite(b.get('avg_amt_yi')) or b['avg_amt_yi']<=0:return False
        if not finite(curve[-1]) or abs(curve[-1]-1)>1e-6:return False
    return True


def build_close(doc,base=None):
    net=[];total=0
    for c in CODES:
        m=doc['etfs'][c];total+=m['amt']
        ob,os=m.get('ob'),m.get('os');n=m.get('net_buy')
        if finite(ob) and finite(os) and ob>=0 and os>=0 and ob+os>0 and finite(n):
            expected=(ob-os)/(ob+os)*m['amt']
            if abs(expected-n)<=.000501:net.append(n)
    denominator=sum(base['codes'][c]['avg_amt_yi']*base['codes'][c]['curve'][-1] for c in CODES) if base else None
    return {'time':'15:00','net_buy_yi':round(sum(net),3) if len(net)==7 else None,
            'net_covered':len(net),'total_amt_yi':round(total,4),
            'same_time_ratio':total/denominator if denominator else None,
            'same_time_den_yi':denominator,'same_time_covered':7 if denominator else 0}


def main():
    history=json.loads((ROOT/'data/history.json').read_text());chosen={};bases={}
    for rev,doc,sha in snapshots('data/live.json'):
        d=iso(doc.get('date'))
        if d in history and d not in chosen and complete_close(doc,d):chosen[d]=(rev,doc,sha)
    for rev,doc,sha in snapshots('preview-data/turnover-baseline.json'):
        for d in chosen:
            if d not in bases and valid_base(doc,d):bases[d]=(rev,doc,sha)
    target=ROOT/'data/historical_overview.json'
    existing=json.loads(target.read_text()).get('days',{}) if target.exists() else {}
    days=dict(existing) # A shallow daily checkout must never delete prior verified history.
    for d,(rev,doc,sha) in sorted(chosen.items()):
        if d in days:continue
        b=bases.get(d);row=build_close(doc,b[1] if b else None)
        if row['net_buy_yi'] is None and row['same_time_ratio'] is None:continue
        row['source']={'snapshot_commit':rev,'snapshot_sha256':sha,'snapshot_updated':doc['updated'],
                       'snapshot_url':'https://github.com/kakarottoisme/national-team-octagon/commit/'+rev}
        if b:row['source'].update(baseline_commit=b[0],baseline_sha256=b[2],baseline_last_day=max(x['last_day'] for x in b[1]['codes'].values()))
        days[d]=row
    result={'schema':'historical-overview-1','method':'Preserved same-day complete close snapshot; prior 20-day baseline only; no interpolation','days':days}
    text=json.dumps(result,ensure_ascii=False,indent=1,sort_keys=True)+'\n'
    target=ROOT/'data/historical_overview.json'
    if not target.exists() or target.read_text()!=text:target.write_text(text)
    print(json.dumps({'days':len(days),'net_days':sum(v['net_buy_yi'] is not None for v in days.values()),'ratio_days':sum(v['same_time_ratio'] is not None for v in days.values()),'dates':list(days)},ensure_ascii=False))


if __name__=='__main__':main()
