#!/usr/bin/env python3
"""Independent CSV-column audit of full backfill staging, never writes production."""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


def audit(folder,repo):
    result={}
    for p in ('IF','IH','IC','IM'):
        shard=json.loads((folder/'staged'/f'{p}.json').read_text())
        old=json.loads((repo/'preview-data/official/futures'/f'{p}.json').read_text())
        for day,row in old['records'].items():assert shard['records'][day]==row,(p,day,'old changed')
        count=0;last=None;earliest=None;nulls=0
        for day,row in shard['records'].items():
            if day in old['records'] or row['source_status']!='published':continue
            raw=(folder/'raw'/p/day[:4]/f'{p}_{day.replace("-","")}.csv').read_bytes()
            assert hashlib.sha256(raw).hexdigest()==row['source_sha256'],(p,day,'sha')
            rows=list(csv.reader(raw.decode('gbk').splitlines()))
            header=next((i for i,r in enumerate(rows) if '会员简称' in r and '持买单量' in r and '持卖单量' in r),None)
            assert header is not None
            names=rows[header];groups=rows[header] if '交易日' in rows[header] else rows[header-1]
            idx={k:groups.index(k) for k in ('交易日','合约','排名')};li=names.index('持买单量');si=names.index('持卖单量')
            exact={};present=set()
            for r in rows[header+1:]:
                if len(r)<=si+1 or not r[idx['交易日']].strip().isdigit():continue
                assert r[idx['交易日']].strip()==day.replace('-',''),(p,day,'date')
                c=r[idx['合约']].strip().upper()
                if not re.fullmatch(p+r'\d+',c):continue
                present.add(c)
                for value_col,label in ((li,'long'),(si,'short')):
                    # Exact source member, never match 中信建投 or group totals.
                    if re.split('[（(]',r[value_col-1].strip())[0]=='中信期货':
                        e=exact.setdefault(c,{'long':None,'short':None,'long_change':None,'short_change':None,'long_rank':None,'short_rank':None})
                        e[label]=int(r[value_col]);e[label+'_change']=int(r[value_col+1]);e[label+'_rank']=int(r[idx['排名']])
            archived={r['contract']:r for r in row['contracts']}
            assert set(archived)==set(exact),(p,day,'contracts')
            for c,e in exact.items():
                for k,v in e.items():assert archived[c][k]==v,(p,day,c,k)
            assert set(row['both_absent_contracts'])==present-set(exact)
            longs=[v['long'] for v in exact.values() if v['long'] is not None];shorts=[v['short'] for v in exact.values() if v['short'] is not None]
            long=sum(longs) if longs else None;short=sum(shorts) if shorts else None
            assert row['long_observed']==long and row['short_observed']==short
            net=short-long if longs and shorts and len(longs)==len(shorts)==len(exact) else None
            assert row['net_short']==net,(p,day,'net');nulls+=net is None;count+=1;earliest=earliest or day;last=day
        assert not any(v['source_status']=='error' for d,v in shard['records'].items() if d not in old['records']),p
        result[p]={'independently_audited_new_published_days':count,'old_records_preserved':len(old['records']),'earliest':earliest,'latest_new':last,'null_net_days':nulls,'total_published':sum(v['source_status']=='published' for v in shard['records'].values())}
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--folder',required=True);ap.add_argument('--repo',required=True);a=ap.parse_args()
    result=audit(Path(a.folder),Path(a.repo));(Path(a.folder)/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
