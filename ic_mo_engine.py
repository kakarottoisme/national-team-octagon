"""IC + bought MO Put. One deterministic model-account step; no brokerage/orders.
Frozen X0506 (45d/5%/75%) + A tail 2. Prices are actual contracts, no synthetic options.
"""
import copy, math, datetime as dt
CFG={'base':1.,'ma':250,'band':.01,'c_hi':.10,'c_lo':.03,'d_frac':.5,'d_max':4,'maxlots':10,'roll_days':3,'open_adv':.03,'switch_adv':.03,'leverage':2.,'ddstop':.30,'cooldown':20,'put_tenor':45,'put_otm':.05,'put_cover':.75,'put_roll':10,'put_budget':.15,'put_cap':220000,'cash_reserve':.10,'futures_margin':.15,'net_delta_cap':2.,'option_gross_cap':4.,'long_option_value_cap':.20,'all_option_lots':60,'min_volume':500,'min_oi':100,'tenor_slack':45,'participation':.01,'day_lots':30,'delta_band':.10,'option_fee':120}
GRID=[(5000,10),(5250,8),(5500,6),(6000,4),(6500,2)]
def a_leg(s):return next((n for k,n in GRID if s<=k),2)
def discount(s,q):return (s-q['close'])/s*365/max(1,q['days'])
def select_future(pack,s,held):
 eligible=[k for k in sorted(pack) if pack[k]['days']>=10]
 if not eligible:return held if held in pack and pack[held]['days']>1 else None
 ref=eligible[0];best=max(eligible,key=lambda k:discount(s,pack[k]))
 if held not in pack or pack[held]['days']<=CFG['roll_days']:return best if discount(s,pack[best])>=discount(s,pack[ref])+CFG['open_adv'] else ref
 return best if discount(s,pack[best])>=discount(s,pack[held])+CFG['switch_adv'] else held

def select_put(chain,spot):
 candidates=[]
 for k,q in chain.items():
  d=q.get('delta');days=q['days']
  if not k.startswith('MO') or '-P-' not in k or not q['close'] or q['volume']<500 or q['oi']<100 or d is None or not .05<=abs(d)<=.98:continue
  if not 15<=days<=90 or abs(q['strike']/spot-.95)>.04:continue
  score=abs(days-45)/45+abs(q['strike']/spot-.95)/.025
  candidates.append((score,-q['volume'],k))
 return min(candidates)[2] if candidates else None

def step(state,signal,quotes,execution_date,apply=True):
 """Signals known before execution. Same snapshot is allowed only for labelled preview planning."""
 s=copy.deepcopy(state);c=CFG; cash=s['cash'];cumf=s['cumf'];fut=s['futures'];opts=s['options'];used={};trades=[];attempts=[];fees=0
 fq=quotes['futures'];chain=quotes['options'];sigq=signal['futures'];sigchain=signal['options'];spot=signal['index500'];im=quotes['index1000'];sp=signal['index1000'];fm=c['futures_margin']
 for k in fut:
  if k not in fq:raise ValueError('held_future_missing '+k)
 for k in opts:
  if k not in chain:raise ValueError('held_option_missing '+k)
 cash+=sum(n*(fq[k]['close']-s['fprev'][k])*200 for k,n in fut.items());cumf+=sum(n*(fq[k]['close']-s['fprev'][k])*200 for k,n in fut.items())
 def mark(k):return chain[k]['close'] if chain[k]['close'] and chain[k]['volume'] else chain[k]['settle']
 def eq():return cash+sum(n*mark(k)*100 for k,n in opts.items())
 def settlement_cash():return cash+sum(n*(fq[k]['settle']-fq[k]['close'])*200 for k,n in fut.items())
 def margin():return sum(n*fq[k]['settle']*200*fm for k,n in fut.items())
 def stress_free():return settlement_cash()-margin()-sum(n*fq[k]['close']*200 for k,n in fut.items())*.10
 def exposure():
  fn=sum(n*fq[k]['close']*200 for k,n in fut.items());de=fn+sum(n*chain[k]['delta']*im*100 for k,n in opts.items());gr=sum(n*im*100 for n in opts.values());va=sum(n*mark(k)*100 for k,n in opts.items());return de,gr,va
 def add_trade(asset,k,qty,price,fee,tag):
  nonlocal fees
  fees+=fee;trades.append({'date':execution_date,'asset':asset,'code':k,'quantity':qty,'price':price,'fee':fee,'tag':tag})
 def fillopt(k,dn,tag):
  nonlocal cash
  q=chain[k];old=opts.get(k,0);capvol=min(q['volume'],sigchain.get(k,{}).get('volume',0));cap=max(0,min(30-sum(used.values()),int(capvol*.01)-used.get(k,0)));n=min(abs(int(dn)),cap)
  attempts.append({'code':k,'requested':abs(dn),'capacity':cap,'tag':tag})
  if not q['close'] or q['close']<=0 or not q['volume'] or not n:return 0
  if dn<0:n=min(n,old)
  if dn>0:
   if sigchain.get(k,{}).get('volume',0)<500:return 0
   de,gr,va=exposure();e=eq()
   while n>0:
    if abs(de+n*q['delta']*im*100)<=e*2 and gr+n*im*100<=e*4 and va+n*q['close']*100<=e*.20 and sum(opts.values())+n<=60:break
    n-=1
  if not n:return 0
  qty=n if dn>0 else -n;fee=n*c['option_fee'];cash-=qty*q['close']*100+fee;opts[k]=old+qty;used[k]=used.get(k,0)+n
  add_trade('option',k,qty,q['close'],fee,tag)
  if opts[k]==0:del opts[k]
  return n
 def fillfuture(target,k,tag):
  nonlocal cash,cumf
  target=min(10,max(0,int(target)));cost=0
  for old,n in list(fut.items()):
   diff=(target if old==k else 0)-n
   if diff:
    fee=abs(diff)*(20 if diff>0 else 50);cost+=fee;add_trade('future',old,diff,fq[old]['close'],fee,tag)
  if k and k not in fut and target:
   fee=target*20;cost+=fee;add_trade('future',k,target,fq[k]['close'],fee,tag)
  cash-=cost;cumf-=cost;fut.clear()
  if target and k:fut[k]=target
 # Expiry cash uses official settlement, never assumes intrinsic or a sell fill.
 for k,n in list(opts.items()):
  if chain[k]['days']==0:
   cash+=n*chain[k]['settle']*100-n*2;add_trade('option',k,-n,chain[k]['settle'],n*2,'expiry_cash');del opts[k]
 peak=max(s['peak'],eq());dd=1-eq()/peak;cool=s['cool']
 if cool:
  cool-=1
  if cool==0:peak=eq()
 elif dd>=.30:cool=20
 ma=signal['ma250'];b=2 if spot>ma*1.01 else 0 if spot<ma*.99 else s['b'];cl=s['c'];cinfo=copy.deepcopy(s['cinfo']);near=sorted(sigq)[0]
 if sigq[near]['expiry']==execution_date and len(sigq)>1:
  nxt=sorted(sigq)[1];rate=discount(spot,sigq[nxt]);cl=2 if rate>=.10 else -2 if rate<.03 else 0;cinfo={'date':signal['date'],'code':nxt,'rate':rate}
 a=a_leg(spot);base=max(0,min(10,a+b+cl));d=s['d'];anchor=s['danchor'];profit_signal=state['cumf']
 if base==0 or spot<ma*.99:d=0;anchor=profit_signal
 held=next(iter(fut),None);code=select_future(sigq,spot,held)
 if not code:raise ValueError('no eligible IC future')
 if base>0 and spot>=ma*.99:d=max(d,min(4,max(0,10-base),int(.5*max(0,profit_signal-anchor)/(sigq[code]['close']*200*.12))))
 model=base+d;target=max(0,min(model,int(max(0,s['equity'])*2/(sigq[code]['close']*200)),10));risk_reason=[]
 if cool:target=0;risk_reason.append('回撤冷却')
 if code not in fq or fq[code]['days']<=0:
  elig=[k for k in sorted(sigq) if k in fq and fq[k]['days']>0]
  if not elig:raise ValueError('no executable future')
  code=elig[0]
 if eq()>0 and (margin()/eq()>.55 or stress_free()<eq()*.10):target=min(target,max(0,int(eq()*.35/(fq[code]['close']*200))));risk_reason.append('压力现金或保证金上限')
 affordable=min(target,max(0,int((settlement_cash()-eq()*.10)/(fq[code]['settle']*200*fm))))
 if affordable<target:risk_reason.append('可用现金上限')
 fillfuture(affordable,code,'signal_or_risk');nom=sum(n*fq[k]['close']*200 for k,n in fut.items())
 for k,n in list(opts.items()):
  if chain[k]['days']<=10 or nom==0:fillopt(k,-n,'put_roll_or_flat')
 heldput=next(iter(opts),None);pk=heldput or select_put(sigchain,sp)
 if pk not in chain:pk=None
 putinfo={'code':pk,'theoretical':None,'capped':None,'target':None,'band':None,'reason':None}
 if nom>0 and pk and not cool:
  q=chain[pk];sq=sigchain.get(pk)
  if not sq or sq.get('delta') is None:raise ValueError('no dated Delta '+pk)
  ref=sq['close'] or sq['settle'];nom_sig=sum(n*sigq[k]['close']*200 for k,n in fut.items() if k in sigq);desired=int(nom_sig*.75/(sp*100*max(abs(sq['delta']),.1)));existing=opts.get(pk,0)
  limit=int(sum(fut.values())*220000/(ref*100));capped=min(desired,int(s['equity']*.15/(ref*100)),limit)
  if existing>limit:fillopt(pk,-(existing-limit),'put_value_cap');existing=opts.get(pk,0)
  gap=capped-existing;band=max(1,int(max(existing,capped)*.10));intent=capped
  if abs(gap)<band:capped=existing
  elif gap<0:fillopt(pk,gap,'delta_trim');existing=opts.get(pk,0)
  buy=max(0,capped-existing)
  if buy and settlement_cash()-margin()>buy*(q['close']*100+120)+eq()*.10:fillopt(pk,buy,'put_buy')
  putinfo.update(theoretical=desired,capped=intent,target=capped,band=band,delta=sq['delta'],ref=ref,signal_volume=sq['volume'],signal_oi=sq['oi'],signal_nominal=nom_sig)
 elif nom>0:
  putinfo['reason']='回撤冷却，不新增保护' if cool else '没有满足期限、档位与成交门槛的 Put'
 if eq()>0:
  de,gr,va=exposure()
  if abs(de)>eq()*2 or gr>eq()*4 or va>eq()*.20:
   for k,n in sorted(list(opts.items()),key=lambda z:z[1],reverse=True):
    de,gr,va=exposure()
    if abs(de)<=eq()*2 and gr<=eq()*4 and va<=eq()*.20:break
    if gr>eq()*4 or va>eq()*.20 or de*n*chain[k]['delta']>0 and abs(de)>eq()*2:fillopt(k,-n,'exposure_cap_reduce')
 if eq()>0 and (margin()>eq()*.55 or settlement_cash()-margin()<0 or stress_free()<eq()*.10):
  k=next(iter(fut),None)
  if k:fillfuture(min(sum(fut.values()),max(0,int((settlement_cash()-eq()*.10)/(fq[k]['close']*200*(fm+.10))))),k,'emergency_delever');risk_reason.append('压力现金紧急减仓')
 s.update(date=execution_date,cash=cash,cumf=cumf,danchor=anchor,b=b,c=cl,d=d,cinfo=cinfo,peak=max(peak,eq()),cool=cool,equity=eq(),fprev={k:fq[k]['close'] for k in fut},futures=fut,options=opts)
 trace={'signal_date':signal['date'],'date':execution_date,'a':a,'b':b,'c':cl,'d':d,'model':model,'ic_lots':sum(fut.values()),'ic_code':next(iter(fut),code),'risk_reason':risk_reason,'put':putinfo,'d_pnl':profit_signal-anchor,'d_need':None if d>=min(4,10-base) else max(0,(d+1)*sigq[code]['close']*200*.12/.5-(profit_signal-anchor)),'cool':cool,'cinfo':cinfo,'margin':margin(),'free':settlement_cash()-margin(),'equity':eq(),'trades':trades,'attempts':attempts}
 return s,trace

def replay_costs(batches,trades):
 out=copy.deepcopy(batches)
 for t in trades:
  k=t['code'];rows=out.setdefault(k,[]);n=t['quantity']
  if n>0:rows.append({'date':t['date'],'price':t['price'],'qty':n,'source':t['tag']})
  else:
   left=-n
   if sum(r['qty'] for r in rows)<left:raise ValueError('FIFO oversell '+k)
   while left:
    r=rows[0];z=min(left,r['qty']);r['qty']-=z;left-=z
    if not r['qty']:rows.pop(0)
  if not rows:out.pop(k,None)
 return out

def implied_put_delta(spot,strike,days,price):
 """Labelled intraday estimate from observed premium, not official Delta."""
 if min(spot,strike,days,price)<=0:return None
 T=days/365;disc=math.exp(-.02*T);N=lambda x:(1+math.erf(x/math.sqrt(2)))/2
 if price<=max(0,strike*disc-spot) or price>=strike*disc:return None
 def valuation(v):
  a=v*math.sqrt(T);d1=(math.log(spot/strike)+(.02+.5*v*v)*T)/a;d2=d1-a
  return strike*disc*N(-d2)-spot*N(-d1)
 lo,hi=.0001,4.
 if valuation(hi)<price:return None
 for _ in range(80):
  mid=(lo+hi)/2
  if valuation(mid)<price:lo=mid
  else:hi=mid
 vol=(lo+hi)/2;d1=(math.log(spot/strike)+(.02+.5*vol*vol)*T)/(vol*math.sqrt(T))
 return N(d1)-1
