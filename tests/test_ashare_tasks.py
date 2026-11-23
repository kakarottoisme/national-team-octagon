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
            with patch.object(a,'ROOT',root),patch.dict(sys.modules,{'margin':fake}),patch.object(a,'verify_archive_pages'),patch.object(a,'commit_files') as push:
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
    @staticmethod
    def archive_response(code='sh510050', falling=False, date='20261119'):
        clocks = []
        for start, end in ((570, 690), (780, 900)):
            clocks.extend(f'{m//60:02d}{m%60:02d}' for m in range(start, end+1))
        clocks.extend(f'15{m:02d}' for m in range(6, 31))
        rows = [f'{t} 2.5 {i+1} {(i+1)*100000001}' for i,t in enumerate(clocks)]
        if falling:
            i = clocks.index('1523')
            rows[i] = f'1523 2.5 {i+1} 1'
        return {'data':{code:{'data':{'date':date,'data':rows},'qt':{code:['0','name',code,'2.5','2.4']}}}}

    def test_archive_retries_full_corrected_response(self):
        url='https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh510050'
        bad=self.archive_response(falling=True); good=self.archive_response()
        from unittest.mock import Mock
        fetch=Mock(side_effect=[bad,good])
        with patch.object(a.time,'sleep') as sleep:
            self.assertIs(a.fetch_archive_source(fetch,url,'2026-11-19'),good)
            sleep.assert_called_once_with(15)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(bad['data']['sh510050']['data']['data'][-8].split()[3],'1')

    def test_archive_persistent_bad_data_never_writes(self):
        import archive
        now=dt.datetime(2026,11,19,15,12,tzinfo=c.TZ)
        with patch.object(archive,'g',return_value=self.archive_response(falling=True)) as fetch, \
             patch.object(a.time,'sleep') as sleep, \
             patch.object(a,'write_json_atomic') as write,patch.object(a,'commit_files') as push:
            with self.assertRaisesRegex(ValueError,'累计成交额倒退'):
                a.archive(now,{})
            self.assertEqual(fetch.call_count,3)
            self.assertEqual([x.args[0] for x in sleep.call_args_list],[15,30])
            write.assert_not_called();push.assert_not_called()

    def test_archive_transient_http_retry_auth_no_retry(self):
        from unittest.mock import Mock
        from urllib.error import HTTPError
        url='https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh510050'
        good=self.archive_response()
        fetch=Mock(side_effect=[HTTPError(url,503,'temporary',{},None),good])
        with patch.object(a.time,'sleep') as sleep:
            self.assertIs(a.fetch_archive_source(fetch,url,'2026-11-19'),good)
            sleep.assert_called_once_with(15)
        for status in (401,403,404):
            fetch=Mock(side_effect=HTTPError(url,status,'blocked',{},None))
            with patch.object(a.time,'sleep') as sleep:
                with self.assertRaises(HTTPError): a.fetch_archive_source(fetch,url,'2026-11-19')
                sleep.assert_not_called()
            self.assertEqual(fetch.call_count,1)

    def test_archive_wrong_day_no_retry(self):
        from unittest.mock import Mock
        fetch=Mock(return_value=self.archive_response(date='20261118'))
        with patch.object(a.time,'sleep') as sleep:
            with self.assertRaisesRegex(ValueError,'不是目标交易日'):
                a.fetch_archive_source(fetch,'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh510050','2026-11-19')
            sleep.assert_not_called()
        self.assertEqual(fetch.call_count,1)

    def test_archive_retry_keeps_original_calculation(self):
        import archive,copy
        from backend_common import ETF_UNIVERSE
        kline={'data':{'sh000001':{'day':[['2026-11-18',0,100],['2026-11-19',0,99]]}}}
        def good(url):
            return copy.deepcopy(kline) if '/minute/query' not in url else self.archive_response(url.rsplit('=',1)[1])
        expected=archive.build_archive(fetch=good)
        bad_count={}
        def once_bad(url):
            if '/minute/query' not in url:return good(url)
            code=url.rsplit('=',1)[1];bad_count[code]=bad_count.get(code,0)+1
            return self.archive_response(code,falling=bad_count[code]==1)
        with patch.object(a.time,'sleep'):
            actual=archive.build_archive(fetch=lambda url:a.fetch_archive_source(once_bad,url,'2026-11-19'))
        self.assertEqual(json.dumps(actual,ensure_ascii=False),json.dumps(expected,ensure_ascii=False))
        self.assertEqual(set(bad_count),{code for _,code in ETF_UNIVERSE})
        self.assertTrue(all(n==2 for n in bad_count.values()))

    def test_expiry_and_scope(self):
        self.assertEqual(a.contract_expiry('IC2612'),dt.date(2026,12,18))
        self.assertEqual(a.candidate_codes(dt.date(2026,11,18)),['IC2611','IC2612'])
        self.assertEqual(a.candidate_codes(dt.date(2026,12,21)),['IC2612','IC2701'])

if __name__=='__main__': unittest.main()
