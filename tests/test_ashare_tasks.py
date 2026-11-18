import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ashare_tasks as a
import market_calendar as c

class AutomationTests(unittest.TestCase):
    def test_calendar_holidays_makeup_weekend_unknown(self):
        self.assertFalse(c.is_trading_day(dt.date(2026,10,1)))
        self.assertFalse(c.is_trading_day(dt.date(2026,10,10)))
        self.assertTrue(c.is_trading_day(dt.date(2026,11,19)))
        with self.assertRaises(ValueError): c.is_trading_day(dt.date(2027,1,4),lambda u:'2026年休市安排')
    def test_sessions(self):
        for clock,expected in [('09:14',False),('09:15',True),('11:35',True),('11:36',False),('12:54',False),('12:55',True),('15:05',True),('15:06',False)]:
            self.assertEqual(c.in_live_session(dt.datetime.fromisoformat('2026-11-19T'+clock).replace(tzinfo=c.TZ)),expected)
    def test_basis_threshold_and_forced(self):
        day=dt.date(2026,11,18)
        pos=[{'contract':'IC2612','direction':'多','lots':4}]
        q={'sh000905':{'price':6000},'IC2612':{'price':6000}}
        r=a.evaluate_ic(pos,q,day)
        self.assertFalse(r['triggered'])
        pos=[{'contract':'IC2703','direction':'多','lots':4}]
        days=(a.contract_expiry('IC2703')-day).days
        q['IC2703']={'price':6000+6000*.02*days/365}
        self.assertTrue(a.evaluate_ic(pos,q,day)['triggered'])
        q['IC2703']={'price':6000+6000*.0199*days/365}
        self.assertFalse(a.evaluate_ic(pos,q,day)['triggered'])
        pos=[{'contract':'IC2611','direction':'多','lots':4}]
        q['IC2611']={'price':6000}
        r=a.evaluate_ic(pos,q,day)
        self.assertTrue(r['forced']);self.assertTrue(r['triggered']);self.assertEqual(r['new']['contract'],'IC2612')
    def test_fresh_quotes_not_settlement(self):
        now=dt.datetime(2026,11,18,14,44,tzinfo=c.TZ)
        index=['0']*33;index[3]='6000';index[30:32]=['2026-11-18','14:43:50']
        fut=['0']*49;fut[3]='5900';fut[7]='5800';fut[36:38]=['2026-11-18','14:43:50']
        text='var hq_str_sh000905="'+','.join(index)+'";var hq_str_nf_IC2612="'+','.join(fut)+'";'
        self.assertEqual(a.quotes(text,now,['IC2612'])['IC2612']['price'],5900)
        with self.assertRaises(ValueError): a.quotes(text.replace('14:43:50','14:00:00'),now,['IC2612'])
    def test_old_margin_silent_before_indices(self):
        import types
        empty=types.SimpleNamespace(to_dict=lambda orient:[])
        fake=types.SimpleNamespace(margin=lambda:{'2026-11-17':100},IDX={},ak=types.SimpleNamespace(stock_margin_sse=lambda **kw:empty))
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'data').mkdir();(root/'data/margin_balance.json').write_text(json.dumps({'margin':{'2026-11-17':100}}))
            with patch.object(a,'ROOT',root),patch.dict(sys.modules,{'margin':fake}),patch.object(a,'commit_files') as push:
                r=a.margin(dt.datetime(2026,11,18,16,35,tzinfo=c.TZ),{})
                self.assertFalse(r['notify']);push.assert_not_called()
    def test_live_health_dedupe(self):
        now=dt.datetime(2026,11,18,14,45,tzinfo=c.TZ)
        state={}
        old=json.dumps({'date':'20261118','updated':'2026-11-18 13:00:00'})
        with patch.object(a,'get',return_value=old):
            self.assertFalse(a.live_health(now,state)['notify'])
            self.assertFalse(a.live_health(now,state)['notify'])
            self.assertTrue(a.live_health(now,state)['notify'])
            self.assertFalse(a.live_health(now,state)['notify'])
    def test_archive_failure_does_not_write(self):
        import archive
        now=dt.datetime(2026,11,18,15,12,tzinfo=c.TZ)
        raw={'data':{'sh510050':{'data':{'date':'20261117','data':['1500 1 1 100']}}}}
        with patch.object(archive,'g',return_value=raw),patch.object(a,'write_json_atomic') as write,patch.object(a,'commit_files') as push:
            with self.assertRaises(ValueError):a.archive(now,{})
            write.assert_not_called();push.assert_not_called()
    def test_expiry_and_scope(self):
        self.assertEqual(a.contract_expiry('IC2612'),dt.date(2026,12,18))
        self.assertEqual(a.candidate_codes(dt.date(2026,11,18)),['IC2611','IC2612'])
        self.assertEqual(a.candidate_codes(dt.date(2026,12,21)),['IC2612','IC2701'])

if __name__=='__main__': unittest.main()
