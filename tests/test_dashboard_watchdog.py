import datetime as dt,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import dashboard_watchdog as w
class WatchdogTests(unittest.TestCase):
 def now(self,h,m,day=27):return dt.datetime(2026,11,day,h,m,tzinfo=w.TZ)
 def test_node_windows_and_daily_last_session(self):
  self.assertEqual(set(w.due('intraday',self.now(9,20))),{'live'})
  self.assertEqual(w.due('intraday',self.now(12,5)),{})
  self.assertEqual(set(w.due('intraday',self.now(16,10))),{'futures','ic'})
  self.assertEqual(set(w.due('intraday',self.now(17,20))),{'futures','ic','cycle'})
  self.assertEqual(w.due('daily',self.now(0,40,28))['archive'],'2026-11-27')
  self.assertEqual(w.due('daily',self.now(0,40,29)),{})
 def test_workflow_dedupe_and_reserve_before_dispatch(self):
  state={'attempts':{}};saved=[];calls=[]
  def gh(*a):
   self.assertEqual(next(iter(state['attempts'].values())),'reserved');calls.append(a);return ''
  with patch.object(w,'active',return_value=False),patch.object(w,'gh',gh):
   a=w.repair('futures',state,'2026-11-27-16-20-futures',lambda s:saved.append(1))
   b=w.repair('global',state,'2026-11-27-16-20-global',lambda s:saved.append(1))
  self.assertEqual(len(calls),1);self.assertIn('already',b)
 def test_busy_does_not_dispatch(self):
  with patch.object(w,'active',return_value=True),patch.object(w,'gh') as g:
   w.repair('ic',{'attempts':{}},'node',lambda _:None);g.assert_not_called()
 def test_unknown_dispatch_outcome_not_retried(self):
  state={'attempts':{}}
  with patch.object(w,'active',return_value=False),patch.object(w,'gh',side_effect=RuntimeError('unconfirmed')):
   with self.assertRaises(RuntimeError):w.repair('ic',state,'node-ic',lambda _:None)
  self.assertEqual(next(iter(state['attempts'].values())),'reserved')
  with patch.object(w,'active',return_value=False),patch.object(w,'gh') as g:w.repair('ic',state,'node-ic',lambda _:None);g.assert_not_called()
 def test_old_values_not_changed_when_complete(self):
  state={'attempts':{}}
  with patch('ashare_tasks.margin_missing',return_value=[]),patch.object(w,'verify_publication') as verify,patch.object(w,'load',return_value={}),patch('ashare_tasks.build_margin_update') as build:
   w.repair('margin',state,'test-margin',lambda _:None,'2026-11-26');build.assert_not_called();verify.assert_called_once()
 def test_all_active_states_server_filter(self):
  calls=[]
  def gh(*a):
   calls.append(a)
   return json.dumps({'total_count':1 if 'status=pending' in a else 0})
  with patch.object(w,'gh',gh):self.assertTrue(w.active('live.yml'))
  self.assertEqual(len(calls),4);self.assertTrue(all('per_page=1' in a for a in calls))
 def test_complete_archive_never_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'data/minutes').mkdir(parents=True);(root/'data/minutes/2026-11-27.json').touch()
   with patch.object(w,'ROOT',root),patch.object(w,'load',return_value={'2026-11-27':{}}),patch.object(w,'verify_publication') as verify,patch('restore_archive_snapshot.from_git') as recovery:
    w.repair('archive',{'attempts':{}},'a',lambda _:None,'2026-11-27');recovery.assert_not_called();verify.assert_called_once()
 def test_dispatch_stays_unresolved_until_rechecked(self):
  state={'attempts':{},'unresolved':{}}
  with patch.object(w,'due',return_value={'ic':'2026-11-27'}),patch.object(w,'assess',return_value=['missing']),patch.object(w,'repair',return_value='existing workflow dispatched'):
   w.inspect('intraday',state,lambda _:None)
  self.assertIn('ic',state['unresolved'])
  with patch.object(w,'due',return_value={'ic':'2026-11-27'}),patch.object(w,'assess',return_value=[]):w.inspect('intraday',state,lambda _:None)
  self.assertNotIn('ic',state['unresolved'])
 def test_daily_skips_weekend_and_unknown_calendar(self):
  self.assertEqual(w.due('daily',dt.datetime(2026,11,30,0,40,tzinfo=w.TZ)),{})
  with patch.object(w,'is_trading_day',side_effect=ValueError('unknown')):
   with self.assertRaises(ValueError):w.due('daily',self.now(0,40,28))
if __name__=='__main__':unittest.main()
