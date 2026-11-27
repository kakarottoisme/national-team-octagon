"""Append only verified completed daily closes for ETF benchmark overlay.
Never touch archived closes; no shares/holdings or strategy rules are changed.
"""
from pathlib import Path
import datetime as dt,json,math,urllib.request
from zoneinfo import ZoneInfo
from backend_io import write_json_atomic
ROOT=Path(__file__).resolve().parent
URL='https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/CN_MarketDataService.getKLineData?symbol={code}&scale=240&ma=no&datalen=60'
def parse(text,cutoff):
    rows=json.loads(text[text.index('['):text.rindex(']')+1]);out={}
    for r in rows:
        d=str(r['day'])[:10];dt.date.fromisoformat(d);p=float(r['close'])
        if not math.isfinite(p) or p<=0:raise ValueError('invalid benchmark close')
        if d in out:raise ValueError('duplicate date')
        if d<=cutoff:out[d]=p
    return out

def update(path=ROOT/'data/etf_index_reference.json',now=None,fetch=None):
    now=now or dt.datetime.now(ZoneInfo('Asia/Shanghai'));day=now.date()
    if now.hour*60+now.minute<915:day-=dt.timedelta(days=1)
    d=json.loads(Path(path).read_text());errors={};added={}
    for name,row in d['indices'].items():
        url=URL.format(code=row['code'])
        try:
            if fetch:text=fetch(url)
            else:
                req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn'})
                with urllib.request.urlopen(req,timeout=20) as r:text=r.read().decode()
            data=parse(text,day.isoformat());old=row['dates'];fresh={k:v for k,v in data.items() if k>max(old)}
            old.update(fresh);row['as_of']=max(old);row['update_source_url']=url;added[name]=len(fresh)
        except Exception as e:errors[name]=type(e).__name__
    if any(added.values()):
        from backend_io import write_text_atomic
        write_text_atomic(Path(path),json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1)+'\n')
    return {'added':added,'errors':errors,'as_of':{k:v['as_of'] for k,v in d['indices'].items()}}
if __name__=='__main__':
    x=update();print(json.dumps(x,ensure_ascii=False));raise SystemExit(int(bool(x['errors'])))
