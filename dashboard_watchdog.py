"""Silent two-class watchdog. No orders or financial-rule edits.
Deterministic repair only; unknown writes/auth/corrupt old records stop and persist.
"""
from pathlib import Path
import argparse,datetime as dt,fcntl,json,subprocess,sys,time,urllib.request,urllib.error
from zoneinfo import ZoneInfo
from backend_io import write_json_atomic
from market_calendar import TZ,is_trading_day
ROOT=Path(__file__).resolve().parent;REPO='kakarottoisme/national-team-octagon';SITE='https://kakarottoisme.github.io/national-team-octagon/'
MODULES={
 'live':('live.yml',['data/live.json']),
 'futures':('preview-data.yml',[f'preview-data/official/futures/{p}.json' for p in ['IH','IF','IC','IM']]),
 'ic':('ic-strategy.yml',['data/ic_strategy.json']),
 'cycle':('market-cycle.yml',['data/market_cycle.json']),
 'flows':('etf-share-flows.yml',['data/etf_share_flows.json','data/etf_share_history.json','data/etf_index_reference.json','data/historical_overview.json']),
 'global':('preview-data.yml',['preview-data/global.json']),
 'margin':(None,['data/margin_balance.json']),
 'archive':(None,['data/history.json'])}
def previous(day):
 day-=dt.timedelta(days=1)
 while not is_trading_day(day):day-=dt.timedelta(days=1)
 return day

def due(mode,now):
 minute=now.hour*60+now.minute;day=now.date()
 if mode=='daily':
  if not 40<=minute<=240:return {}
  # Only the prior calendar day's market session; do not recheck Friday on Sunday/Monday.
  prior=day-dt.timedelta(days=1)
  if not is_trading_day(prior):return {}
  target=prior.isoformat()
  return {'flows':target,'global':target,'archive':target,'margin':previous(prior).isoformat()}
 if not is_trading_day(day):return {}
 target=day.isoformat();out={}
 # A scheduled node may arrive up to 4 minutes late; no backwards catch-up burst.
 node=minute-minute%5
 if 560<=node<=695 or 775<=node<=905:out['live']=target
 if 510<=node<=559:out['cycle']=previous(day).isoformat()
 if (940<=node<=1080 and node%10==0) or node in (1220,1340,1420):out.update(futures=target,ic=target)
 if node==1040:out['cycle']=target
 return out

def read(path):
 request=urllib.request.Request(SITE+path+'?watch='+str(time.time_ns()),headers={'Cache-Control':'no-cache','User-Agent':'Dashboard-Watchdog'})
 for attempt in range(2):
  try:
   with urllib.request.urlopen(request,timeout=20) as r:return json.load(r)
  except urllib.error.HTTPError as exc:
   if exc.code not in (408,429) and exc.code<500:raise
   if attempt:raise
  except (urllib.error.URLError,TimeoutError):
   if attempt:raise
  time.sleep(2)
def load(path):return json.loads((ROOT/path).read_text())
def assess(name,target,now,reader=read):
 paths=MODULES[name][1];docs={p:reader(p) for p in paths};problems=[]
 for p,d in docs.items():
  if d!=load(p):problems.append('Pages differs from latest repository: '+p)
 d=docs[paths[0]]
 if name=='live':
  stamp=dt.datetime.fromisoformat(d['updated']).replace(tzinfo=TZ);age=(now-stamp).total_seconds()
  if d.get('date')!=target.replace('-','') or age>660:problems.append('live source date/time stale')
  from live_runner import validate_payload
  try:validate_payload(d,now)
  except ValueError:problems.append('live minute completeness/date/time invalid')
 elif name=='futures':
  from futures_close_update import member_missing_days,verify_members_date
  if member_missing_days(ROOT,dt.date.fromisoformat(target)):problems.append('futures recent trading-day gap')
  else:verify_members_date(ROOT,dt.date.fromisoformat(target))
  for p,x in docs.items():
   if x.get('records',{}).get(target,{}).get('source_status')!='published':problems.append(p+' missing '+target)
 elif name=='ic':
  if d.get('latest','')<target or not d.get('days') or d['days'][-1]['date']!=d['latest']:problems.append('IC closed signal missing')
 elif name=='cycle':
  # Same provider's completed bars determine US date; no CN calendar substitution.
  import market_cycle as c
  for market,row in d.get('markets',{}).items():
   code=c.MARKETS[market][0][0];ref=c.fetch(code,now)['history'][-1]['date']
   if row.get('as_of','')<ref or row.get('status')!='ok':problems.append(market+' cycle source date stale: '+ref)
 elif name=='flows':
  if d.get('as_of','')<target or d.get('summary',{}).get('inflow_1d_covered',0)!=17:problems.append('ETF published date/coverage incomplete')
  if any(x.get('as_of','')<target for x in docs['data/etf_index_reference.json']['indices'].values()):problems.append('ETF benchmarks stale')
  recent=dt.date.fromisoformat(target)-dt.timedelta(days=20)
  history=docs['data/etf_share_history.json']['days'];overview=docs['data/historical_overview.json']['days']
  while recent.isoformat()<=target:
   day=recent.isoformat()
   if is_trading_day(recent):
    rows=history.get(day,{})
    if len(rows)!=17 or any(x.get('stat_date')!=day or x.get('tot_vol_wan') is None for x in rows.values()):problems.append('ETF missing dated coverage '+day)
   recent+=dt.timedelta(days=1)
  if target not in overview:problems.append('historical overview target missing')
 elif name=='global':
  stamp=dt.datetime.fromisoformat(d['generated_at']).astimezone(TZ)
  if stamp.date().isoformat()<target or d.get('errors'):problems.append('global attempt or sources incomplete')
  import sys
  sys.path.insert(0,str(ROOT/'preview-tools'))
  import global_sources as g
  import public_macro as pm
  reference=pm.collect_public_macro(years=(now.year,),now=now,fetcher=lambda url:pm._get(url,timeout=12,retries=0))
  by_id={m['id']:m for m in d.get('metrics',[])}
  mapping={**g._PUBLIC_MACRO_MAP,'bei_10y':'S8_bei_10y'}
  for metric in reference.get('metrics',[]):
   name=mapping.get(metric['id'])
   if name and name in by_id and by_id[name].get('status','').startswith('connected'):
    if metric.get('as_of') and (by_id[name].get('as_of') or '')<metric['as_of']:problems.append(name+' behind its actual published source')
  if reference.get('errors'):problems.append('source reference check incomplete; preserve dates')
 elif name=='margin':
  import ashare_tasks as checker
  if checker.margin_missing(d,target):problems.append('margin prior disclosure date/internal gap missing')
 elif name=='archive':
  import archive
  if target not in d:problems.append('archive missing')
  else:
   archive.validate_complete(d[target],reader('data/minutes/'+target+'.json'),target)
 return problems

def gh(*args):
 p=subprocess.run(['gh',*args],capture_output=True,text=True,timeout=60)
 if p.returncode:raise RuntimeError('GitHub read/write not confirmed; stop without retry')
 return p.stdout

def active(workflow):
 # Filter EACH active state server-side, across branches, so old queued jobs are
 # not hidden by 30 newer completed runs. One result suffices to block a writer.
 for status in ('queued','in_progress','waiting','pending','requested'):
  d=json.loads(gh('api',f'repos/{REPO}/actions/workflows/{workflow}/runs','--method','GET','-f','per_page=1','-f','status='+status))
  if d.get('total_count',0):return True
 return False
def verify_publication(paths):
 import ashare_tasks as task
 expected={p:(ROOT/p).read_bytes() for p in paths}
 def matches():
  for p,b in expected.items():
   try:
    with urllib.request.urlopen(SITE+p+'?verify='+str(time.time_ns()),timeout=15) as response:
     if response.read()!=b:return False
   except Exception:return False
  return True
 if matches():return
 # Request the existing branch deployment only. Never change Pages settings.
 gh('api',f'repos/{REPO}/pages/builds','--method','POST')
 task.verify_archive_pages(paths,timeout=300)

def repair(name,state,key,persist,target=None):
 # The same source writer may serve multiple modules; reserve by workflow+node.
 workflow=MODULES[name][0]
 if workflow:
  reservation=key.rsplit('-',1)[0]+'-'+workflow
  if active(workflow):return 'existing run; no duplicate'
  if any(k.endswith('-'+workflow) and v=='reserved' for k,v in state['attempts'].items()):return 'prior dispatch unconfirmed; inspect logs before any new dispatch'
  if state['attempts'].get(reservation):return 'node already attempted'
  state['attempts'][reservation]='reserved';persist(state)
  args=['workflow','run',workflow,'--repo',REPO,'--ref','main']
  if name=='global':args+=['-f','full_refresh=true']
  gh(*args);state['attempts'][reservation]='accepted';persist(state);return 'existing workflow dispatched'
 if state['attempts'].get(key):return 'node already attempted'
 # Recheck direct targets immediately before reserving a write.
 if name=='margin':
  import ashare_tasks as checker
  if not checker.margin_missing(load('data/margin_balance.json'),target):
   verify_publication(['data/margin_balance.json']);return 'already complete; publication rechecked'
 if name=='archive' and target in load('data/history.json') and (ROOT/f'data/minutes/{target}.json').exists():
  import ashare_tasks as checker
  verify_publication(['data/history.json',f'data/minutes/{target}.json'])
  return 'archive already present; publication rechecked'
 state['attempts'][key]='reserved';persist(state)
 # Direct recovery is limited to date-verified append-only outputs. Never change
 # holdings, financial formulas or old valid observations to make a gate green.
 import ashare_tasks as task
 if name=='archive':
  import restore_archive_snapshot as recovery
  summary,minutes=recovery.from_git(ROOT,target)
  changed=recovery.apply_missing(ROOT/'data',target,summary,minutes)
  if changed:task.commit_files(['data/history.json','data/minutes/'+target+'.json'],'watchdog restore '+target)
  verify_publication(['data/history.json','data/minutes/'+target+'.json'])
 elif name=='margin':
  old=load('data/margin_balance.json');candidate=task.build_margin_update(old,dt.datetime.now(TZ),target)
  if candidate is None:return 'source not yet available; old values preserved'
  write_json_atomic(ROOT/'data/margin_balance.json',candidate,ensure_ascii=False,indent=1)
  task.commit_files(['data/margin_balance.json'],'watchdog margin '+target)
  verify_publication(['data/margin_balance.json'])
 state['attempts'][key]='completed';persist(state);return 'scoped append-only repair completed'
def sync_checkout():
 # Refresh before observing; never compare Pages with a hours-old checkout.
 def git(*args):
  p=subprocess.run(['git','-C',str(ROOT),*args],capture_output=True,text=True,timeout=60)
  if p.returncode:raise RuntimeError('checkout operation unconfirmed; preserve work')
  return p.stdout.strip()
 if git('diff','--name-only') or git('diff','--cached','--name-only') or git('log','origin/main..HEAD','--oneline'):
  raise RuntimeError('unpublished work retained; stop without replay')
 git('fetch','--quiet','origin','main');git('merge','--ff-only','origin/main')

def inspect(mode,state,persist,check_only=False,followup=False):
 now=dt.datetime.now(TZ);results={};targets=due(mode,now);verify_only=False
 if followup and not targets:
  verify_only=True
  prior=state.get('last_result_'+mode,{})
  targets={n:v['target'] for n,v in prior.items() if v.get('target') and n in MODULES}
  # Followups observe old closed targets, never start a live writer outside session.
  if not (560<=now.hour*60+now.minute<=695 or 775<=now.hour*60+now.minute<=905):targets.pop('live',None)
 for name,target in targets.items():
  key=(now.strftime('%Y-%m-%d-%H')+'-'+str((now.minute//5)*5)+'-'+name) if mode=='intraday' else target+'-daily-'+name
  try:
   problems=assess(name,target,now)
   action=repair(name,state,key,persist,target) if problems and not check_only and not verify_only else 'no repair'
   # Dispatch is not success. Retain unresolved until date+publication recheck.
   if problems and action=='scoped append-only repair completed':problems=assess(name,target,dt.datetime.now(TZ))
   results[name]={'target':target,'problems':problems,'action':action}
   if problems:state['unresolved'][name]=results[name]
   else:state['unresolved'].pop(name,None)
  except Exception as e:
   # Network/missing-object failures may be a publication gap. A source collector
   # is only dispatched after transient read failures, never auth/schema errors.
   action='stop pending investigation'
   if isinstance(e,(urllib.error.URLError,TimeoutError)) and not (isinstance(e,urllib.error.HTTPError) and e.code in (401,403)) and not check_only and not verify_only:
    try:action=repair(name,state,key,persist,target)
    except Exception:action='repair unconfirmed; no replay'
   results[name]={'target':target,'error':type(e).__name__,'action':action};state['unresolved'][name]=results[name]
 state['checked_at']=now.isoformat();state['last_result']=results;state['last_result_'+mode]=results;persist(state)
 return results

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['intraday','daily']);p.add_argument('--state-dir',required=True);p.add_argument('--check-only',action='store_true');p.add_argument('--followup',action='store_true');a=p.parse_args()
 r=Path(a.state_dir);r.mkdir(parents=True,exist_ok=True);path=r/'dashboard-watchdog.json';now=dt.datetime.now(TZ)
 # Use the SAME lock as archive/margin writers, not just other watchdogs.
 with (r/'lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);state=json.loads(path.read_text()) if path.exists() else {'attempts':{},'unresolved':{}}
  persist=lambda x:write_json_atomic(path,x,ensure_ascii=False,indent=2)
  sync_checkout();results=inspect(a.mode,state,persist,a.check_only,a.followup)
  with (r/'watchdog-runs.jsonl').open('a') as f:f.write(json.dumps({'at':now.isoformat(),'mode':a.mode,'result':results},ensure_ascii=False)+'\n')
 # Followup is inside the same consolidated cron execution, not a new task.
 followup=None;minute=now.hour*60+now.minute
 if a.mode=='intraday':
  if 500<=minute<510:followup=now.replace(hour=8,minute=30,second=0,microsecond=0)
  elif 560<=minute<695 or 775<=minute<905:followup=now.replace(second=0,microsecond=0)+dt.timedelta(minutes=5-now.minute%5)
  elif 940<=minute<1080:followup=now.replace(second=0,microsecond=0)+dt.timedelta(minutes=10-now.minute%10)
  elif not a.followup and results:followup=now+dt.timedelta(minutes=15)
 elif not a.followup and any(v.get('problems') or v.get('error') for v in results.values()):followup=now+dt.timedelta(minutes=20)
 print(json.dumps({'notify':False,'results':results,'state':'checked','followup_at':followup.isoformat() if followup else None},ensure_ascii=False))
if __name__=='__main__':main()
