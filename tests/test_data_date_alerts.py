import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import ashare_tasks as a

DAY='2026-11-20'
NOW=dt.datetime(2026,11,23,9,5,tzinfo=a.TZ)
IDX={'one':'sh000300','two':'sh000905','three':'sh000016','four':'sh000852','five':'sz399006','six':'sh000688'}

class DataDateTests(unittest.TestCase):
    def fake(self, available=True, wrong=False, missing_index=False):
        rows=[{'信用交易日期':'20261120','融资融券余额':1139447767407}] if available else []
        return types.SimpleNamespace(IDX=IDX,ak=types.SimpleNamespace(stock_margin_sse=lambda **kw:types.SimpleNamespace(to_dict=lambda _:rows)),
            szse_official=lambda d:{'date':'2026-11-19' if wrong else d,'balance_yi':10332.78,'source_url':'https://www.szse.cn/'},
            idx=lambda c:{} if missing_index else {DAY:3980})
    def old(self):
        return {'margin':{'2026-11-19':22150.57},'index':{k:{'2026-11-19':4000} for k in IDX},'codes':IDX,'updated':'old'}
    def call(self,fake,old=None,state=None,final=False):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'data').mkdir();p=root/'data/margin_balance.json';p.write_text(json.dumps(old or self.old()))
            before=p.read_bytes();state={} if state is None else state
            with patch.object(a,'ROOT',root),patch.dict(sys.modules,{'margin':fake}),patch.object(a,'verify_archive_pages'),patch.object(a,'commit_files',return_value=True) as push:
                result=a.margin(NOW,state,final)
                return result,state,json.loads(p.read_text()),before,push.call_count
    def test_monday_expects_friday(self):
        self.assertEqual(a.previous_trading_day(NOW.date()).isoformat(),DAY)
    def test_holiday_previous_day(self):
        self.assertEqual(a.previous_trading_day(dt.date(2026,10,8)).isoformat(),'2026-09-30')
    def test_official_total_and_preserve_old(self):
        result,state,out,before,n=self.call(self.fake())
        self.assertEqual(out['margin'][DAY],21727.26)
        self.assertEqual(out['margin']['2026-11-19'],22150.57)
        for k in IDX:self.assertEqual(out['index'][k]['2026-11-19'],4000)
        self.assertEqual(state['margin_success_data_date'],DAY)
        self.assertEqual(result['status'],'success');self.assertFalse(result['notify']);self.assertEqual(n,1)
    def test_run_day_flag_does_not_skip(self):
        result,state,out,_,n=self.call(self.fake(),state={'margin_success_day':'2026-11-23'})
        self.assertIn(DAY,out['margin']);self.assertNotIn('margin_success_day',state);self.assertEqual(n,1)
    def test_pending_never_writes(self):
        result,state,out,before,n=self.call(self.fake(False))
        self.assertEqual(result['status'],'pending');self.assertEqual(out,self.old());self.assertEqual(n,0);self.assertFalse(state)
    def test_final_missing_raises(self):
        with self.assertRaisesRegex(ValueError,'补查结束仍缺'):self.call(self.fake(False),final=True)
    def test_wrong_szse_day_never_writes(self):
        with self.assertRaisesRegex(ValueError,'同日校验失败'):self.call(self.fake(wrong=True))
    def test_missing_one_index_never_writes(self):
        result,state,out,_,n=self.call(self.fake(missing_index=True))
        self.assertEqual(out,self.old());self.assertEqual(n,0);self.assertEqual(result['status'],'pending')
    def test_existing_data_no_source_or_push(self):
        old=self.old();old['margin'][DAY]=21727.26
        for k in IDX:old['index'][k][DAY]=3980
        fake=self.fake();fake.ak.stock_margin_sse=lambda **kw:self.fail('unexpected network')
        result,state,out,_,n=self.call(fake,old)
        self.assertEqual(n,0);self.assertEqual(out,old);self.assertEqual(result['data_date'],DAY)
    def test_internal_hole_detected(self):
        old=self.old();old['margin']['2026-11-17']=1
        with patch.dict(sys.modules,{'margin':self.fake()}):
            self.assertIn('2026-11-18余额',a.margin_missing(old,DAY))
    def test_window_missed_alert_no_collection(self):
        state={}
        with patch.object(a,'archive',side_effect=ValueError('越窗快照缺失，未恢复')) as fetch:
            r=a.execute_task('archive',NOW.replace(hour=15,minute=35),state)
            self.assertTrue(r['notify']);self.assertEqual(r['status'],'failed');self.assertEqual(fetch.call_count,1)
            self.assertFalse(a.execute_task('archive',NOW.replace(hour=15,minute=36),state)['notify'])
    def test_late_recovery_still_reports_missed_window(self):
        with patch.object(a,'archive',return_value={'notify':False,'reason':'晚到补档成功'}):
            r=a.execute_task('archive',NOW.replace(hour=15,minute=35),{})
            self.assertTrue(r['notify']);self.assertEqual(r['status'],'warning')
            self.assertIn('晚到补档成功',r['message'])
    def test_margin_window_missed_alert(self):
        with patch.object(a,'margin') as fetch:
            r=a.execute_task('margin',NOW.replace(hour=9,minute=45),{})
            self.assertTrue(r['notify']);fetch.assert_not_called()
    def test_early_error_pending_no_dedupe(self):
        state={}
        with patch.object(a,'margin',side_effect=ValueError('source fail')):
            self.assertEqual(a.execute_task('margin',NOW,state)['status'],'pending')
            self.assertEqual(state,{})
            self.assertTrue(a.execute_task('margin',NOW.replace(minute=25),state)['notify'])
    def test_weekend_silent_even_window_missed(self):
        r=a.execute_task('archive',dt.datetime(2026,11,21,15,35,tzinfo=a.TZ),{})
        self.assertFalse(r['notify'])
    def test_unknown_calendar_alert(self):
        with patch.object(a,'is_trading_day',side_effect=ValueError('日历未知')):
            self.assertTrue(a.execute_task('data-audit',NOW.replace(hour=18),{})['notify'])
    def test_audit_never_writes_and_catches_skipped_archive(self):
        old=self.old();old['margin'][DAY]=21727.26
        for k in IDX:old['index'][k][DAY]=3980
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'data').mkdir();(root/'data/margin_balance.json').write_text(json.dumps(old));(root/'data/history.json').write_text('{}')
            with patch.object(a,'ROOT',root),patch.dict(sys.modules,{'margin':self.fake()}),patch.object(a,'commit_files') as push:
                r=a.execute_task('data-audit',NOW.replace(hour=18,minute=5),{})
                self.assertTrue(r['notify']);self.assertIn('八宫格',r['message']);push.assert_not_called()
    def test_audit_missing_margin_and_archive(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'data').mkdir();(root/'data/margin_balance.json').write_text(json.dumps(self.old()));(root/'data/history.json').write_text('{}')
            with patch.object(a,'ROOT',root),patch.dict(sys.modules,{'margin':self.fake()}):
                r=a.execute_task('data-audit',NOW.replace(hour=18),{})
                self.assertIn(DAY,r['message']);self.assertIn('八宫格',r['message'])
    def test_szse_metadata_required(self):
        import margin
        with self.assertRaisesRegex(ValueError,'统计日不匹配'):
            margin.szse_official(DAY,lambda u:[{'metadata':{'subname':'2026-11-19'},'data':[{'jrrzrjye':'10,332.78'}]}])
        r=margin.szse_official(DAY,lambda u:[{'metadata':{'subname':DAY},'data':[{'jrrzrjye':'10,332.78'}]}])
        self.assertEqual(r['balance_yi'],10332.78)

if __name__=='__main__':unittest.main()
