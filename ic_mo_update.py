"""Collect dated IC/MO snapshots and advance the isolated research model account.
Only data/ic_mo_strategy.json is written. Existing IC history and member modules untouched.
"""
import argparse,copy,csv,datetime as dt,hashlib,io,json,math,re,sys,urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from market_calendar import TZ,is_trading_day
from backend_io import write_json_atomic
import ic_strategy as legacy
from ic_mo_engine import step,replay_costs,implied_put_delta,CFG
ROOT=Path(__file__).resolve().parent
CSV_URL='http://www.cffex.com.cn/sj/hqsj/rtj/{ym}/{dd}/{ymd}_1.csv'
INDEX1000=legacy.INDEX_URL.replace('sh000905','sh000852')
UA={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'}
def fetch(url):
 with urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=18) as r:return r.read()
def positive(x):return isinstance(x,(int,float)) and math.isfinite(x) and x>0

def csv_snapshot(raw,date,idx500,idx1000,ma):
 rows=csv.DictReader(io.StringIO(raw.decode('gb18030')))
 if not rows.fieldnames or not {'合约代码','今收盘','今结算','Delta'}<=set(rows.fieldnames):raise ValueError('官方 CSV 未发布或表头无效')
 futures={};options={};hol=legacy.FUTURE_HOLIDAYS
 for r in rows:
  code=r['合约代码'].strip();opt=re.fullmatch(r'MO(\d{2})(\d{2})-P-(\d+)',code);ft=re.fullmatch(r'IC(\d{2})(\d{2})',code)
  if not opt and not ft:continue
  def number(k):
   x=r.get(k,'').strip();return None if x.lower() in ('','--','-','null','nan') else float(x)
  q={k:number(v) for k,v in [('open','今开盘'),('high','最高价'),('low','最低价'),('close','今收盘'),('settle','今结算'),('prev_settle','前结算'),('volume','成交量'),('oi','持仓量'),('delta','Delta')]}
  if not positive(q['settle']) and q['settle']!=0:raise ValueError('官方结算缺失')
  if q['volume'] is None or q['volume']<0 or q['oi'] is None or q['oi']<0:raise ValueError('官方量仓缺失')
  m=opt or ft;expiry=legacy.last_trade_day(2000+int(m[1]),int(m[2]),hol)
  q.update(expiry=expiry.isoformat(),days=(expiry-dt.date.fromisoformat(date)).days)
  if ft:
   if not positive(q['close']):raise ValueError('IC 收盘缺失')
   futures[code]=q
  else:
   if q['delta'] is None or not -1<=q['delta']<=0:continue
   q['strike']=float(m[3]);options[code]=q
 if len(futures)!=4 or not options:raise ValueError('官方四合约或 MO Put 链不完整')
 return {'date':date,'asof':date+'T15:00:00+08:00','index500':idx500,'index1000':idx1000,'ma250':ma,'futures':futures,'options':options,'delta_kind':'official','sources':[]}

def index_series(raw):
 rows=legacy.parse_jsonp_array(raw.decode('utf8','replace'));out={}
 for r in rows:
  if isinstance(r,dict) and 'day' in r and 'close' in r:
   price=round(float(r['close']),3)
   if not positive(price):raise ValueError('index nonpositive')
   date=r['day'][:10]
   if date in out:raise ValueError('duplicate index date')
   out[date]=price
 return out

def next_session(day):
 d=day+dt.timedelta(days=1)
 for _ in range(365):
  if is_trading_day(d):return d
  d+=dt.timedelta(days=1)
 raise ValueError('next trading date unknown')
def completed_day(now):
 d=now.date() if now.time()>=dt.time(15,30) else now.date()-dt.timedelta(days=1)
 for _ in range(370):
  if is_trading_day(d):return d
  d-=dt.timedelta(days=1)
 raise ValueError('completed trading date unknown')

def quote_parse(raw,symbol):
 txt=raw.decode('gb18030','replace');m=re.search(r'var hq_str_'+re.escape(symbol)+r'="([^"]*)";',txt)
 if not m or not m[1]:raise ValueError('quote missing '+symbol)
 return m[1].split(',')

def live_snapshot(base,now):
 date=now.date().isoformat();codes=sorted(base['futures']);raw=fetch('https://hq.sinajs.cn/list=sh000905,sh000852,'+','.join('nf_'+k for k in codes));indices={}
 def fresh(timestamp):
  stamp=dt.datetime.fromisoformat(timestamp).replace(tzinfo=TZ)
  if stamp.date()!=now.date() or not -10<=(now-stamp).total_seconds()<=180:raise ValueError('盘中报价过时 '+timestamp)
  return stamp
 stamps=[]
 for sym,key in [('sh000905','index500'),('sh000852','index1000')]:
  arr=quote_parse(raw,sym);stamps.append(fresh(arr[30]+' '+arr[31]));indices[key]=round(float(arr[3]),3)
 futures={}
 for k in codes:
  arr=quote_parse(raw,'nf_'+k);i=next(i for i,v in enumerate(arr) if re.fullmatch(r'\d{4}-\d{2}-\d{2}',v));stamps.append(fresh(arr[i]+' '+arr[i+1]));q=copy.deepcopy(base['futures'][k]);q['days']=(dt.date.fromisoformat(q['expiry'])-now.date()).days;q['close']=float(arr[3]);futures[k]=q
 if not all(positive(v) for v in indices.values()) or not all(positive(v['close']) for v in futures.values()):raise ValueError('bad live underlying')
 selected=[k for k,q in base['options'].items() if q['days']>=0];options={}
 for start in range(0,len(selected),75):
  keys=selected[start:start+75];syms={k:'P_OP_'+k[:2].lower()+k[2:6]+k[7]+k[9:] for k in keys};raw=fetch('https://hq.sinajs.cn/list='+','.join(syms.values()))
  for k,sym in syms.items():
   try:
    arr=quote_parse(raw,sym);stamp=fresh(arr[32]);q=copy.deepcopy(base['options'][k]);q['days']=(dt.date.fromisoformat(q['expiry'])-now.date()).days;q['close']=float(arr[2]);q['volume']=float(arr[41]);q['oi']=float(arr[5]);q['delta']=implied_put_delta(indices['index1000'],q['strike'],q['days'],q['close']);q['quote_time']=stamp.isoformat();q['delta_kind']='implied-intraday'
    if q['delta'] is None or not positive(q['close']):continue
    options[k]=q;stamps.append(stamp)
   except ValueError:continue
 if not options:raise ValueError('no verified dated live MO options')
 return {'date':date,'asof':min(stamps).isoformat(timespec='seconds'),**indices,'ma250':None,'futures':futures,'options':options,'delta_kind':'implied-intraday','sources':[{'url':'https://hq.sinajs.cn/','collected_at':now.isoformat(timespec='seconds')} ]}

def holdings(state,snap):
 batches=state.get('batches',{});out={'ic':[],'mo':[]};fn=0;on=0
 for kind,pack in [('ic',state['futures']),('mo',state['options'])]:
  for code,qty in pack.items():
   qs=snap['futures' if kind=='ic' else 'options'];q=qs.get(code);bs=copy.deepcopy(batches.get(code,[]));costKnown=sum(b['qty'] for b in bs)==qty and all(positive(b['price']) for b in bs)
   avg=sum(b['qty']*b['price'] for b in bs)/qty if costKnown and qty else None;mark=(q['close'] if positive(q['close']) and q['volume'] else q['settle']) if q else None
   mult=200 if kind=='ic' else 100;pnl=(mark-avg)*qty*mult if mark is not None and avg is not None else None
   item={'code':code,'qty':qty,'average':avg,'mark':mark,'cost_known':costKnown,'unrealized':pnl,'batches':bs,'days':q['days'] if q else None,'expiry':q['expiry'] if q else None,'last_change':bs[-1]['date'] if bs else None,'delta':q.get('delta') if q else None}
   if kind=='ic':item['margin']=qty*mark*200*.15 if mark is not None else None;fn+=qty*mark*200 if mark is not None else 0
   else:item['value']=qty*mark*100 if mark is not None else None;on+=qty*abs(q['delta'])*snap['index1000']*100 if q and q['delta'] is not None else 0
   out[kind].append(item)
 out['coverage']=on/fn if fn else None;out['state_date']=state['date'];out['asof']=snap['asof'];return out

def recommendation(state,snap):
 nex=dt.date.fromisoformat(snap['date']) if snap['delta_kind']=='implied-intraday' else next_session(dt.date.fromisoformat(snap['date']));s,tr=step(state,snap,snap,nex.isoformat(),False)
 # The planning step must not re-mark today's carried futures twice; state is already marked at snapshot close.
 pi=tr['put'];code=tr['ic_code'];q=snap['futures'].get(code);batches=state.get('batches',{})
 return {**tr,'ic_target':s['futures'],'mo_target':s['options'],'delta_kind':snap['delta_kind'],'snapshot':snap,'expected':holdings(state,snap),'valid_for':nex.isoformat(),'basis':'model_estimate','order_note':'相对连续模拟账户的预计持仓；真实仓位或未成交不同必须另算','state_date':state['date']}

def run(root,mode,now=None,get=fetch):
 now=(now or dt.datetime.now(TZ)).astimezone(TZ);target=Path(root)/'data/ic_mo_strategy.json';rawold=target.read_bytes();packet=json.loads(rawold);state=packet['state'];date=completed_day(now).isoformat()
 if state['date']<date:
  with ThreadPoolExecutor(max_workers=2) as pool:r0,r1=list(pool.map(get,[legacy.INDEX_URL,INDEX1000]))
  ix=index_series(r0);im=index_series(r1);hist={v['date']:v['close'] for v in packet['index_history']};hist.update({d:p for d,p in ix.items() if d<=date})
  day=dt.date.fromisoformat(state['date'])+dt.timedelta(days=1)
  while day.isoformat()<=date:
   ds=day.isoformat()
   if is_trading_day(day):
    if ds not in ix or ds not in im:raise ValueError('index not updated '+ds)
    prices=[hist[d] for d in sorted(hist) if d<=ds][-250:]
    if len(prices)<250:raise ValueError('MA250 data insufficient')
    ymd=ds.replace('-','');u=CSV_URL.format(ym=ymd[:6],dd=ymd[6:],ymd=ymd);raw=get(u);snap=csv_snapshot(raw,ds,ix[ds],im[ds],math.fsum(prices)/250);snap['sources']=[{'url':u,'sha256':hashlib.sha256(raw).hexdigest()},{'url':legacy.INDEX_URL,'sha256':hashlib.sha256(r0).hexdigest()},{'url':INDEX1000,'sha256':hashlib.sha256(r1).hexdigest()}]
    new,tr=step(state,packet['previous_signal'],snap,ds);new['batches']=replay_costs(state.get('batches',{}),tr['trades']);state=new;packet['state']=new;packet['previous_signal']=snap;packet['history']=(packet['history']+[{'date':ds,'trades':tr['trades']}])[-90:];packet['index_history']=[{'date':d,'close':hist[d]} for d in sorted(hist) if d<=ds][-251:]
    packet['close']=recommendation(state,snap);packet['close']['published_at']=now.isoformat(timespec='seconds');packet['latest']=ds
   day+=dt.timedelta(days=1)
 if mode=='close' and packet.get('close') and state['date']==date and packet['state']['date']==json.loads(rawold)['state']['date']:
  return {'state':'already_current','date':date,'mode':mode}
 if mode=='preview':
  if not is_trading_day(now.date()) or not dt.time(14,45)<=now.time()<dt.time(15,0):return {'state':'skip_window'}
  if packet['state']['date']!=completed_day(now).isoformat():raise ValueError('previous close gap')
  snap=live_snapshot(packet['previous_signal'],now);history=packet['index_history'];snap['ma250']=(sum(x['close'] for x in history[-249:])+snap['index500'])/250
  rec=recommendation(state,snap);rec['date']=now.date().isoformat();rec['published_at']=now.isoformat(timespec='seconds');rec['valid_for']=now.date().isoformat();packet['preview']=rec
  if any(k not in snap['options'] for k in state['options']):raise ValueError('held MO live quote unavailable')
 elif not packet.get('close'):
  packet['close']=recommendation(state,packet['previous_signal']);packet['close']['published_at']=now.isoformat(timespec='seconds');packet['latest']=state['date']
 packet['updated']=now.isoformat(timespec='seconds');packet['generation']=packet.get('generation',0)+1
 if target.read_bytes()!=rawold:raise ValueError('concurrent write')
 write_json_atomic(target,packet,ensure_ascii=False,separators=(',',':'),allow_nan=False)
 return {'state':'updated','date':packet.get('latest'),'mode':mode}

def main():
 p=argparse.ArgumentParser();p.add_argument('--mode',choices=['close','preview'],default='close');p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args()
 try:r=run(a.root,a.mode);print(json.dumps(r,ensure_ascii=False));return 0
 except Exception as e:print(json.dumps({'state':'failed_keep_previous','reason':str(e)},ensure_ascii=False));return 1
if __name__=='__main__':sys.exit(main())
