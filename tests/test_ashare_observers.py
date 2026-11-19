import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ashare_observers as o


def clock(day='2026-11-19', time='20:30'):
    return dt.datetime.fromisoformat(day+'T'+time).replace(tzinfo=o.TZ)


def payload(day='2026-11-19', a=2, b=0, c=2, d=0):
    total=min(10,min(10,max(0,a+b+c))+d)
    sign='−' if c<0 else '+'
    text=f'A{a} + B{b} {sign} C{abs(c)} + D{d} = {total}'
    if total-d!=a+b+c:text+=f'（底仓未限制前 {a+b+c}）'
    return {'latest':day,'days':[{'date':day,'leg_a':a,'leg_b':b,'b_open':b==2,'leg_c':c,'leg_d':d,'total':total,'target':min(10,max(0,a+b+c)),'sum_text':text}],
            'd_leg':{'margin_rate':.12,'d_max':4,'total_cap':10}}


class ObserverTests(unittest.TestCase):
    def test_windows_and_holidays_no_io(self):
        for task, time in [('live-recover','09:19'),('live-recover','11:36'),('live-recover','12:54'),('live-recover','15:06'),('ic-signal','20:29'),('ic-signal','21:31')]:
            with patch.object(o,'load') as read,patch.object(o,'gh') as gh:
                self.assertFalse(o.execute(task,clock(time=time),{},Mock())['notify'])
                read.assert_not_called();gh.assert_not_called()
        for task,time in [('live-recover','09:20'),('ic-signal','20:30')]:
            for day in ('2026-11-21','2026-10-01'):
                with patch.object(o,'load') as read,patch.object(o,'gh') as gh:
                    self.assertFalse(o.execute(task,clock(day,time),{},Mock())['notify'])
                    read.assert_not_called();gh.assert_not_called()

    def test_busy_even_stale_never_dispatches(self):
        with patch.object(o,'load') as read:
            dispatch=Mock()
            r=o.live_recover(clock(time='09:20'),{},Mock(),busy=lambda:True,dispatch=dispatch)
            self.assertFalse(r['notify']);dispatch.assert_not_called();read.assert_not_called()

    def test_reserve_before_dispatch_once_per_half(self):
        state={};events=[]
        def persist(s):events.append(copy.deepcopy(s))
        dispatch=Mock(side_effect=lambda: self.assertEqual(events[-1]['attempts']['2026-11-19-am']['dispatch'],'reserved'))
        with patch.object(o,'load',return_value={'date':'20261118','updated':'2026-11-18 15:00:00'}):
            r=o.live_recover(clock(time='09:20'),state,persist,busy=lambda:False,dispatch=dispatch)
            self.assertEqual(r['followup_at'],'2026-11-19T09:35:00+08:00');self.assertFalse(r['notify'])
            self.assertFalse(o.live_recover(clock(time='09:22'),state,persist,busy=lambda:False,dispatch=dispatch)['notify'])
        self.assertEqual(dispatch.call_count,1)

    def test_afternoon_dispatch_is_separate_once(self):
        state={};dispatch=Mock()
        with patch.object(o,'load',return_value={'date':'20261119','updated':'2026-11-19 11:30:00'}):
            r=o.live_recover(clock(time='12:55'),state,Mock(),busy=lambda:False,dispatch=dispatch)
        self.assertEqual(r['followup_at'],'2026-11-19T13:10:00+08:00')
        self.assertEqual(list(state['attempts']),['2026-11-19-pm'])

    def test_unconfirmed_write_not_replayed(self):
        state={};dispatch=Mock(side_effect=ValueError('timeout'))
        with patch.object(o,'load',return_value={'date':'20261118','updated':'2026-11-18 15:00:00'}):
            r=o.live_recover(clock(time='09:20'),state,Mock(),busy=lambda:False,dispatch=dispatch)
            self.assertFalse(r['notify'])
            o.live_recover(clock(time='09:21'),state,Mock(),busy=lambda:False,dispatch=dispatch)
        self.assertEqual(dispatch.call_count,1)
        self.assertEqual(state['attempts']['2026-11-19-am']['dispatch'],'unconfirmed')

    def test_success_silent_no_repeat(self):
        state={};dispatch=Mock()
        with patch.object(o,'load',return_value={'date':'20261118','updated':'2026-11-18 15:00:00'}):
            o.live_recover(clock(time='09:20'),state,Mock(),busy=lambda:False,dispatch=dispatch)
        with patch.object(o,'load',return_value={'date':'20261119','updated':'2026-11-19 09:30:00'}):
            r=o.live_recover(clock(time='09:35'),state,Mock(),busy=lambda:False,dispatch=dispatch)
            self.assertFalse(r['notify']);self.assertEqual(r['reason'],'恢复成功')
            o.live_recover(clock(time='09:36'),state,Mock(),busy=lambda:False,dispatch=dispatch)
        self.assertEqual(dispatch.call_count,1)

    def test_fail_only_after_fifteen_minutes_daily_dedupe(self):
        state={};dispatch=Mock()
        with patch.object(o,'load',return_value={'date':'20261118','updated':'2026-11-18 15:00:00'}):
            o.live_recover(clock(time='09:20'),state,Mock(),busy=lambda:False,dispatch=dispatch)
            self.assertFalse(o.live_recover(clock(time='09:34'),state,Mock())['notify'])
            self.assertTrue(o.live_recover(clock(time='09:35'),state,Mock())['notify'])
            self.assertFalse(o.live_recover(clock(time='12:55'),state,Mock(),busy=lambda:False,dispatch=dispatch)['notify'])
        self.assertEqual(dispatch.call_count,1)

    def test_future_timestamp_not_success(self):
        with self.assertRaises(ValueError):o.live_stamp({'updated':'2026-11-19 16:00:00'},clock(time='09:20'))

    def test_active_api_all_statuses_no_dispatch_other_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'.github/workflows').mkdir(parents=True)
            (p/'.github/workflows/live.yml').write_text('concurrency:\n  group: refresh-live-json\n  cancel-in-progress: false\n')
            meta=json.dumps({'name':'Refresh live.json','path':'.github/workflows/live.yml','state':'active'})
            with patch.object(o,'ROOT',p),patch.object(o,'gh',side_effect=[meta]+['[]']*5) as gh:
                self.assertFalse(o.active_live_runs())
                self.assertEqual([c.args[5] for c in gh.call_args_list[1:]],['status='+s for s in o.ACTIVE_STATUSES])
            with patch.object(o,'ROOT',p),patch.object(o,'gh',side_effect=[meta,'[{"head_branch":"other","status":"queued"}]']):
                self.assertTrue(o.active_live_runs())
        with patch.object(o,'gh') as gh:
            o.dispatch_live();gh.assert_called_once_with('workflow','run','live.yml','--repo',o.REPO,'--ref','main')

    def test_first_baseline_silent_only_current_day(self):
        state={}
        with patch.object(o,'load',return_value=payload('2026-11-18')):
            self.assertFalse(o.ic_signal(clock(),state,Mock())['notify']);self.assertEqual(state,{})
        with patch.object(o,'load',return_value=payload()):
            self.assertFalse(o.ic_signal(clock(),state,Mock())['notify'])
        self.assertEqual(state['last_notified']['date'],'2026-11-19')

    def test_no_change_ignores_target_and_price_updates(self):
        base=o.signal_snapshot(payload('2026-11-18'),clock('2026-11-18'))
        state={'last_notified':base}
        new=payload();new['days'][0].update(target=999,index_close=9999)
        with patch.object(o,'load',return_value=new):
            self.assertFalse(o.ic_signal(clock(),state,Mock())['notify'])
        self.assertEqual(state['last_notified']['date'],'2026-11-18')
        self.assertEqual(state['last_seen_day'],'2026-11-19')

    def test_each_observed_leg_changes_and_message(self):
        base=o.signal_snapshot(payload('2026-11-18'),clock('2026-11-18'))
        holding={'positions':[{'contract':'IC2612','direction':'多','lots':4}]}
        for kwargs in ({'a':4},{'b':2},{'c':-2},{'d':1}):
            state={'last_notified':copy.deepcopy(base)}
            with patch.object(o,'load',side_effect=[payload(**kwargs),holding]):
                r=o.ic_signal(clock(),state,Mock())
            self.assertTrue(r['notify']);self.assertIn(payload(**kwargs)['days'][0]['sum_text'],r['message'])
            self.assertIn('变化：',r['message']);self.assertEqual(state['notified_day'],'2026-11-19')
            with patch.object(o,'load',return_value=payload(a=6)):
                self.assertFalse(o.ic_signal(clock(),state,Mock())['notify'])

    def test_same_date_revision_silent(self):
        state={'last_notified':o.signal_snapshot(payload(),clock())}
        with patch.object(o,'load',return_value=payload(a=4)):
            self.assertFalse(o.ic_signal(clock(),state,Mock())['notify'])

    def test_direction_and_position_difference(self):
        def h(lots,direction='多'):return {'positions':[{'contract':'IC2612','direction':direction,'lots':lots}]}
        self.assertIn('应加2手',o.position_text(h(2),4))
        self.assertIn('应减2手',o.position_text(h(6),4))
        self.assertIn('无需调整',o.position_text(h(4),4))
        self.assertIn('先平空4手，再开多6手',o.position_text(h(4,'空'),6))
        with self.assertRaises(ValueError):o.position_text(h(3.5),4)

    def test_invalid_signal_date_formula_limits(self):
        bads=[]
        bad=payload();bad['days'][0]['date']='2026-11-18';bads.append(bad)
        bad=payload();bad['days'][0]['sum_text']='A2 + B0 + C2 + D0 = 9';bads.append(bad)
        for key,val in [('leg_d',5),('total',11),('b_open',True),('leg_a',2.0)]:
            bad=payload();bad['days'][0][key]=val;bads.append(bad)
        bad=payload();bad['d_leg']['margin_rate']=.14;bads.append(bad)
        for bad in bads:
            with self.assertRaises(ValueError):o.signal_snapshot(bad,clock())
        self.assertEqual(o.signal_snapshot(payload(c=-2),clock())['values']['leg_c'],-2)
        self.assertEqual(o.signal_snapshot(payload(a=10,c=2),clock())['values']['total'],10)

    def test_error_daily_dedupe_no_repo_write(self):
        state={}
        with patch.object(o,'load',side_effect=ValueError('bad source')),patch.object(o,'write_json_atomic') as write,patch.object(o,'gh') as gh:
            self.assertTrue(o.execute('ic-signal',clock(),state,Mock())['notify'])
            self.assertFalse(o.execute('ic-signal',clock(),state,Mock())['notify'])
            write.assert_not_called();gh.assert_not_called()

    def test_error_does_not_lose_unnotified_signal(self):
        base=o.signal_snapshot(payload('2026-11-18'),clock('2026-11-18'))
        state={'last_notified':base}
        with patch.object(o,'load',side_effect=[payload(a=4),ValueError('bad holdings')]):
            self.assertTrue(o.execute('ic-signal',clock(),state,Mock())['notify'])
        self.assertNotIn('last_seen_day',state)
        with patch.object(o,'load',side_effect=[payload(a=4),{'positions':[{'contract':'IC2612','direction':'多','lots':4}]}]):
            self.assertFalse(o.ic_signal(clock(),state,Mock())['notify'])
        with patch.object(o,'load',side_effect=[payload('2026-11-20',a=4),{'positions':[{'contract':'IC2612','direction':'多','lots':4}]}]):
            self.assertTrue(o.ic_signal(clock('2026-11-20'),state,Mock())['notify'])

    def test_new_commands_wired_without_old_changes(self):
        script=(o.ROOT/'run_ashare_task.sh').read_text()
        self.assertIn('ic|archive|margin|live-health|live-recover|ic-signal',script)
        self.assertIn('SCRIPT=ashare_tasks.py',script)
        self.assertIn('SCRIPT=ashare_observers.py',script)


if __name__=='__main__':unittest.main()
