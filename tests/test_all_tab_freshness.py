"""Regression tests for all-tab publishing, preserved quote backfill, and clone retries."""
from pathlib import Path
import unittest,subprocess,tempfile,json,datetime as dt,os
from unittest.mock import patch
import yaml
import etf_share_flows as flows
import etf_index_update as overlay
ROOT=Path(__file__).resolve().parents[1]
class AllTabFreshness(unittest.TestCase):
 def test_quote_only_refresh_is_persisted_without_share_change(self):
  h=flows.empty_history();h['updated_at']='2026-11-26T23:00:00+08:00'
  h['days']['2026-11-26']={c:{'stat_date':'2026-11-26','tot_vol_wan':'100'} for c in flows.SH_CODES}
  def quote(c,beg,end,**kw):
   self.assertGreaterEqual(beg,dt.date(2026,11,1))
   return {'2026-11-26':{'close':'1','volume_hand':'100','amount_yuan':'10000'}}
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);hp=r/'h.json';hp.write_text(json.dumps(h))
   with patch.object(flows,'HISTORY_PATH',hp),patch.object(flows,'FLOWS_PATH',r/'f.json'),patch.object(flows,'SZSE_PATH',r/'s.json'),patch.object(flows,'fetch_kline',quote),patch.object(flows,'fetch_sse',return_value=(0,{})),patch.object(flows,'pull_szse_dated',return_value=({},[])):
    x=flows.update_recent(now=dt.datetime(2026,11,27,17,tzinfo=dt.timezone(dt.timedelta(hours=8))),sleep=lambda _:None)
    self.assertTrue(x['changed']);saved=json.loads(hp.read_text());self.assertEqual(len(saved['quotes']['2026-11-26']),17)
 def test_benchmark_append_no_old_revision_no_future_bar(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'index.json';p.write_text(json.dumps({'indices':{'上证180':{'code':'sh000010','as_of':'2026-11-25','dates':{'2026-11-25':100}}}}))
   raw='x('+json.dumps([{'day':'2026-11-25','close':999},{'day':'2026-11-26','close':110},{'day':'2026-11-27','close':120}])+')'
   overlay.update(p,dt.datetime(2026,11,27,9),lambda u:raw);x=json.loads(p.read_text())['indices']['上证180'];self.assertEqual(x['dates'],{'2026-11-25':100,'2026-11-26':110})
 def test_all_scheduled_publishers_cover_their_actual_files(self):
  import futures_pages_sync as p
  for n in ['data/market_cycle.json','data/etf_share_flows.json','data/etf_index_reference.json']:self.assertIn(n,p.ALLOWED)
  m=(ROOT/'.github/workflows/market-cycle.yml').read_text();self.assertIn('git rebase origin/main',m);self.assertIn('futures_pages_sync.py data/market_cycle.json',m)
  e=(ROOT/'.github/workflows/etf-share-flows.yml').read_text();self.assertIn('etf_index_update.py',e);self.assertIn('futures_pages_sync.py',e)
  self.assertIn('5*60*1000',(ROOT/'margin.html').read_text());self.assertIn("event.data?.kind==='view-visibility'",(ROOT/'dashboard-tabs.js').read_text())
 def test_clone_transport_retry_and_auth_no_retry(self):
  text=(ROOT/'run_ashare_task.sh').read_text();code=text[text.index('for attempt in 1 2 3; do'):text.index('\nif [[ "$TASK" == archive ]]; then',text.index('for attempt in 1 2 3; do'))]
  for auth in [False,True]:
   with tempfile.TemporaryDirectory() as t:
    r=Path(t);bin=r/'bin';bin.mkdir();git=bin/'git';count=r/'count'
    git.write_text('#!/bin/sh\nn=0; [ ! -f "$COUNT" ] || n=$(cat "$COUNT"); n=$((n+1)); printf "%s" "$n" > "$COUNT"; if [ "$n" = 1 ]; then printf "%s\\n" "$FAILURE" >&2; exit 1; fi; exit 0\n');git.chmod(0o755)
    sleep=bin/'sleep';sleep.write_text('#!/bin/sh\nexit 0\n');sleep.chmod(0o755)
    x=subprocess.run(['bash','-c',code],env=os.environ|{'RUN':t,'COUNT':str(count),'FAILURE':'Authentication failed' if auth else 'early EOF','PATH':str(bin)+':'+os.environ['PATH']},capture_output=True,text=True)
    self.assertEqual(int(count.read_text()),1 if auth else 2);self.assertEqual(x.returncode,1 if auth else 0)
if __name__=='__main__':unittest.main()
