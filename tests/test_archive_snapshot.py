"""Snapshot recovery uses immutable observations, never fills partial sessions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import datetime as dt
from unittest.mock import patch
import ashare_tasks as a
from backend_common import ETF_UNIVERSE, estimated_large_minutes
from backfill_index_minutes import expected_times
from index_quotes import INDEX_UNIVERSE
import restore_archive_snapshot as r

class RestoreSnapshot(unittest.TestCase):
    @staticmethod
    def sample():
        times=expected_times(1)
        rows={code:{'date':'20261120','t':times[:],'p':[2.5]*len(times),'a':[1.0,1.0001]+[0.0]*(len(times)-2),'pre':2.4} for _,code in ETF_UNIVERSE}
        values={name:estimated_large_minutes(rows[code]['a']) for name,code in ETF_UNIVERSE}
        values.update({'合计':round(sum(values.values()),2),'上证收盘':99.0,'上证涨跌幅':-1.0})
        return {'date':'20261120','updated':'2026-11-20 15:01:29','etfs':rows,'estimates':values,'sse':[['2026-11-19',0,100],['2026-11-20',0,99]],'index_minutes':{code:{'date':'20261120','t':times[:],'p':[6000.0]*len(times)} for _,code in INDEX_UNIVERSE}}

    def test_preserves_original_arrays_and_strict_threshold(self):
        source=self.sample();before=copy.deepcopy(source)
        summary,minutes=r.build(source,'2026-11-20',{'source_url':'https://example.com/preserved'})
        self.assertEqual(source,before)
        self.assertEqual(minutes['etfs'],before['etfs'])
        self.assertTrue(all(summary[name]==1.0 for name,_ in ETF_UNIVERSE))
        self.assertEqual(summary,source['estimates'])
        self.assertEqual(len(minutes['index_minutes']),7)
        self.assertTrue(all(not x['interpolated'] for x in minutes['index_minutes'].values()))

    def test_wrong_date_and_not_closed_rejected(self):
        for mutate in [lambda x:x.update(date='20261123'),lambda x:x.update(updated='2026-11-20 14:56:00'),lambda x:x['etfs']['sh510050'].update(date='20261119')]:
            source=self.sample();mutate(source)
            with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})

    def test_partial_or_invalid_amount_rejected(self):
        for value in [-1,None,float('nan'),True]:
            source=self.sample();source['etfs']['sh510050']['a'][1]=value
            with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})
        source=self.sample();source['etfs']['sh510050']['t']=source['etfs']['sh510050']['t'][:-1]
        with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})

    def test_missing_close_or_changed_summary_rejected(self):
        for mutate in [lambda x:x.update(sse=[['2026-11-19',0,100]]),lambda x:x['estimates'].update({'合计':123})]:
            source=self.sample();mutate(source)
            with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})

    def test_index_partial_rejected(self):
        source=self.sample();source['index_minutes']['sh000905']['t']=source['index_minutes']['sh000905']['t'][:-1]
        with self.assertRaises(ValueError):r.build(source,'2026-11-20',{'source_url':'https://example.com/preserved'})

    def test_existing_history_preserved_idempotent_no_overwrite(self):
        summary,minutes=r.build(self.sample(),'2026-11-20',{'source_url':'https://example.com/preserved'})
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);prior={'2026-11-19':{'合计':42}};(root/'history.json').write_text(json.dumps(prior))
            self.assertTrue(r.apply_missing(root,'2026-11-20',summary,minutes))
            self.assertEqual(json.loads((root/'history.json').read_text())['2026-11-19'],prior['2026-11-19'])
            self.assertFalse(r.apply_missing(root,'2026-11-20',summary,minutes))
            with self.assertRaises(ValueError):r.apply_missing(root,'2026-11-20',{},minutes)

    def test_archive_clock_boundary_no_quote_fetch_when_late(self):
        for clock,mode in [('15:09',None),('15:10','live_source'),('15:30','live_source'),('15:31','git_snapshot'),('23:59','git_snapshot')]:
            now=dt.datetime.fromisoformat('2026-11-20T'+clock)
            self.assertEqual(a.archive_mode(now),mode)

    def test_late_recovery_only_expected_paths_and_silent_success(self):
        summary,minutes=r.build(self.sample(),'2026-11-20',{'source_url':'https://example.com/preserved'})
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'data').mkdir();(root/'data/history.json').write_text('{"2026-11-19":{"合计":42}}')
            state={};timing={}
            with patch.object(a,'ROOT',root),patch.object(r,'from_git',return_value=(summary,minutes)) as source,patch.object(a,'commit_files',return_value=True) as push,patch.object(a,'verify_archive_pages') as publish_check,patch('archive.g',side_effect=AssertionError('No quote fetch')):
                out=a.archive(dt.datetime(2026,11,20,15,35),state,timing)
                self.assertFalse(out['notify']);self.assertEqual(out['state'],'archived');self.assertEqual(state['archive_success'],'2026-11-20')
                source.assert_called_once_with(root,'2026-11-20')
                self.assertEqual(push.call_args.args[0],['data/history.json','data/minutes/2026-11-20.json'])
                again=a.archive(dt.datetime(2026,11,20,20),state)
                self.assertEqual(again['state'],'already_archived');self.assertEqual(source.call_count,1);self.assertEqual(push.call_count,1)
                self.assertIn('snapshot_seconds',timing);self.assertIn('publish_seconds',timing)

    def test_late_invalid_source_and_partial_existing_archive_never_writes(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'data').mkdir();h=root/'data/history.json';h.write_text('{}')
            with patch.object(a,'ROOT',root),patch.object(r,'from_git',side_effect=ValueError('no valid source')),patch.object(a,'commit_files') as push:
                with self.assertRaises(ValueError):a.archive(dt.datetime(2026,11,20,15,35),{})
                self.assertEqual(h.read_text(),'{}');push.assert_not_called()
            h.write_text('{"2026-11-20":{}}')
            with patch.object(a,'ROOT',root),patch.object(r,'from_git') as source:
                with self.assertRaises(ValueError):a.archive(dt.datetime(2026,11,20,15,35),{})
                source.assert_not_called()

    def test_git_candidate_rejects_partial_then_uses_previous_valid_same_day(self):
        valid=self.sample();bad=copy.deepcopy(valid);bad['etfs']['sh510050']['a'][1]=-1
        def fake(cmd,**kw):
            op=cmd[3]
            if op=='rev-parse':return b'false\n'
            if op=='log':return b'bad\ngood\n'
            if op=='show':return json.dumps(bad if cmd[4].startswith('bad:') else valid).encode()
            raise AssertionError(cmd)
        with patch.object(r.subprocess,'check_output',side_effect=fake):
            summary,minutes=r.from_git('/tmp','2026-11-20')
            self.assertEqual(summary,valid['estimates']);self.assertEqual(minutes['archive_provenance']['source_commit'],'good')

    def test_source_retry_crossing_close_window_stops_quote_requests(self):
        import archive
        from urllib.error import URLError
        from tests.test_ashare_tasks import AutomationTests
        ticks=iter([0,0,0,61])
        with patch.object(a.time,'monotonic',side_effect=lambda:next(ticks)),patch.object(a.time,'sleep'),patch.object(archive,'g',side_effect=URLError('temporary')) as fetch:
            with self.assertRaisesRegex(ValueError,'实时抓源窗口已结束'):
                a.archive(dt.datetime(2026,11,20,15,30),{})
            self.assertEqual(fetch.call_count,1)

    def test_main_late_records_business_state_and_clock(self):
        import sys,io
        class Fixed(dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,11,20,15,35,36,tzinfo=tz)
        with tempfile.TemporaryDirectory() as t,patch.object(a.dt,'datetime',Fixed),patch.object(a,'is_trading_day',return_value=True),patch.object(a,'archive',return_value={'notify':False,'state':'already_archived'}) as work,patch.object(sys,'argv',['x','archive','--state-dir',t]),patch('sys.stdout',new_callable=io.StringIO):
            a.main()
            self.assertEqual(work.call_count,1)
            audit=json.loads((Path(t)/'archive-runs.jsonl').read_text())
            self.assertEqual(audit['business_state'],'already_archived');self.assertEqual(audit['entry_offset_seconds'],1416)
            self.assertIn('execution_seconds',audit);self.assertIn('lock_wait_seconds',audit)

if __name__=='__main__':unittest.main()
