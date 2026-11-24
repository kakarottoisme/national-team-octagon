"""Close updater: failure/idempotency/history gates without production writes."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import futures_close_update as m

DAY=dt.date(2026,11,20)
MEMBER_HEADER='交易日,合约,排名,会员简称,成交量,增减,会员简称,持买单量,增减,会员简称,持卖单量,增减\n'


def member_csv(product,date='20261120'):
    return (MEMBER_HEADER+f'{date},{product}2612,1,其他,900,9,中信期货(代客),100,1,中信期货(代客),150,2\n').encode('gbk')


def prepare(root,current=False):
    folder=root/'preview-data/official/futures';folder.mkdir(parents=True)
    for p in m.PRODUCTS:
        data={'schema_version':'futures-history-2.0','member':'中信期货(代客)','product':p,'source':'CFFEX','records':{'2026-11-19':{'source_status':'published','test':'old'}}}
        if current:data['records']['2026-11-20']={'source_status':'published'}
        (folder/f'{p}.json').write_text(json.dumps(data))
    data=root/'data';data.mkdir()
    (data/'ic_strategy.json').write_text(json.dumps({'latest':'2026-11-19','days':[{'date':'2026-11-19'}]}))
    (data/'holdings.json').write_text('{"untouched":true}')


class Members(unittest.TestCase):
    def test_published_append_preserves_old_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            def fetch(url):return 200,member_csv(url.split('/')[-1].split('_')[0])
            result=m.run_members(root,DAY,fetch)
            self.assertEqual(result['state'],'current');self.assertEqual(len(result['changed']),4)
            for p in m.PRODUCTS:
                d=m.load(root/f'preview-data/official/futures/{p}.json')
                self.assertEqual(d['records']['2026-11-19'],{'source_status':'published','test':'old'})
                self.assertEqual(d['records']['2026-11-20']['net_short'],50)
                self.assertEqual(d['coverage']['date_count'],2)
            before={p:p.read_bytes() for p in root.rglob('*.json')}
            result=m.run_members(root,DAY,lambda u:self.fail('already current should not refetch'))
            self.assertEqual(result['changed'],[])
            self.assertTrue(all(p.read_bytes()==raw for p,raw in before.items()))

    def test_absent_invalid_or_wrong_date_never_writes(self):
        for status,raw in [(404,b''),(200,b'bad'),(200,member_csv('IC','20261119')),(503,b'error'*20)]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);prepare(root);before={p:p.read_bytes() for p in root.rglob('*.json')}
                result=m.run_members(root,DAY,lambda u:(status,raw))
                self.assertEqual(result['state'],'pending');self.assertEqual(result['changed'],[])
                self.assertTrue(all(p.read_bytes()==b for p,b in before.items()))

    def test_single_product_failure_does_not_block_other_products(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            def fetch(url):
                p=url.split('/')[-1].split('_')[0]
                return (404,b'') if p=='IH' else (200,member_csv(p))
            result=m.run_members(root,DAY,fetch)
            self.assertEqual(result['state'],'pending');self.assertEqual(len(result['changed']),3)
            self.assertNotIn('2026-11-20',m.load(root/'preview-data/official/futures/IH.json')['records'])


class Strategy(unittest.TestCase):
    def test_repeat_skips_entire_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root);p=root/'data/ic_strategy.json'
            p.write_text('{"latest":"2026-11-20","days":[{"date":"2026-11-20"}]}');before=p.read_bytes()
            result=m.run_strategy(root,DAY,lambda u:self.fail('no refetch'),lambda a:self.fail('no rerun'))
            self.assertEqual(result['state'],'already_current');self.assertEqual(p.read_bytes(),before)

    def test_old_index_day_never_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root);p=root/'data/ic_strategy.json';before=p.read_bytes()
            data=[{'day':'2026-11-19','close':'6000'}]*250
            result=m.run_strategy(root,DAY,lambda u:(200,('x('+json.dumps(data)+');').encode()))
            self.assertEqual(result['state'],'pending_index');self.assertEqual(p.read_bytes(),before)

    def test_requires_official_closes_not_settlement(self):
        raw='合约代码,今收盘,今结算\nIC2612,5902,5934.8\n'.encode('gbk')
        self.assertEqual(m.parse_official_closes(raw,DAY),{'IC2612':5902})
        with self.assertRaises(ValueError):m.parse_official_closes(b'bad',DAY)

    def test_partial_official_keeps_production(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root);p=root/'data/ic_strategy.json';before=p.read_bytes()
            data=[{'day':(dt.date(2025,1,1)+dt.timedelta(days=i)).isoformat(),'close':'6000'} for i in range(249)]+[{'day':'2026-11-20','close':'6000'}]
            def fetch(url):
                return (200,('x('+json.dumps(data)+');').encode()) if 'getKLineData' in url else (200,'合约代码,今收盘\nIC2612,5902\n'.encode('gbk'))
            result=m.run_strategy(root,DAY,fetch)
            self.assertEqual(result['state'],'pending_official');self.assertEqual(p.read_bytes(),before)

    def test_approval_keeps_history_and_rejects_state_change(self):
        previous={'date':'2026-11-19','x':1}
        old={'days':[previous]}
        official={f'IC{x}':6000 for x in ['2611','2612','2703','2706']}
        row={'date':'2026-11-20','index_close':6000,'ma250':6000,'ma_upper':6060,'ma_lower':5940,'contracts':[{'code':k,'price':v,'days':1,'basis':0.0} for k,v in official.items()]}
        candidate={'latest':'2026-11-20','days':[previous,row]}
        index=[('2026-11-19',6000)]*249+[('2026-11-20',6000)]
        # Coverage must match unique source dates beyond the prior published day.
        self.assertEqual(m.approve_strategy(old,candidate,'2026-11-20',index,official)['days'][0],previous)
        bad=copy.deepcopy(candidate);bad['days'][0]['x']=2
        with self.assertRaises(ValueError):m.approve_strategy(old,bad,'2026-11-20',index,official)
        bad=copy.deepcopy(candidate);bad['days'][-1]['contracts'][0]['price']=5934.8
        with self.assertRaises(ValueError):m.approve_strategy(old,bad,'2026-11-20',index,official)

    def test_market_window_holiday_and_unknown_calendar(self):
        def now(d):return dt.datetime.fromisoformat(d).replace(tzinfo=m.TZ)
        self.assertFalse(m.permitted(now('2026-11-20T15:20:00')))
        self.assertTrue(m.permitted(now('2026-11-20T15:35:00')))
        self.assertFalse(m.permitted(now('2026-11-21T16:00:00')))
        self.assertFalse(m.permitted(now('2026-10-01T16:00:00')))
        with self.assertRaises(ValueError):m.permitted(now('2027-03-01T16:00:00'),lambda d:(_ for _ in ()).throw(ValueError('unknown')))


class LateStrategy(unittest.TestCase):
    def test_latest_completed_session_across_midnight_weekend_holiday(self):
        for stamp,expected in [
            ('2026-11-24T03:27:00+08:00','2026-11-23'),
            ('2026-11-24T11:40:00+08:00','2026-11-23'),
            ('2026-11-24T15:29:59+08:00','2026-11-23'),
            ('2026-11-24T15:30:00+08:00','2026-11-24'),
            ('2026-11-23T09:00:00+08:00','2026-11-20'),
            ('2026-11-21T18:00:00+08:00','2026-11-20'),
            ('2026-10-08T09:00:00+08:00','2026-09-30')]:
            self.assertEqual(m.strategy_target_day(dt.datetime.fromisoformat(stamp)).isoformat(),expected)
        with self.assertRaises(ValueError):
            m.strategy_target_day(dt.datetime.fromisoformat('2027-03-01T16:00:00+08:00'),lambda d:(_ for _ in ()).throw(ValueError('unknown')))

    def test_malformed_latest_future_or_duplicate_not_success(self):
        for payload in [
            {'latest':'2026-11-23','days':[]},
            {'latest':'2026-11-23','days':[{'date':'2026-11-20'}]},
            {'latest':'2026-11-24','days':[{'date':'2026-11-24'}]},
            {'latest':'2026-11-23','days':[{'date':'2026-11-23'},{'date':'2026-11-23'}]}]:
            with self.assertRaises(ValueError):m.validate_strategy_date(payload,'2026-11-23')

    def test_pending_is_nonzero_and_does_not_publish_old_date(self):
        now=dt.datetime.fromisoformat('2026-11-24T08:00:00+08:00')
        with patch.object(m,'run_strategy',return_value={'state':'pending_index','date':'2026-11-23','changed':[]}) as run,patch('builtins.print'):
            self.assertEqual(m.main(['--mode','strategy'],now=now),1)
        self.assertEqual(run.call_args.args[1],dt.date(2026,11,23))

    def test_business_date_gate_does_not_accept_matching_old_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            now=dt.datetime.fromisoformat('2026-11-24T08:00:00+08:00')
            with patch('builtins.print'):
                self.assertEqual(m.main(['--mode','strategy','--root',temp,'--verify-date','2026-11-23'],now=now),1)
            (root/'data/ic_strategy.json').write_text('{"latest":"2026-11-23","days":[{"date":"2026-11-23"}]}')
            with patch('builtins.print'):
                self.assertEqual(m.main(['--mode','strategy','--root',temp,'--verify-date','2026-11-23'],now=now),0)

    def test_unfinished_current_index_bar_excluded_from_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            old=m.load(root/'data/ic_strategy.json')
            data=[{'day':(dt.date(2025,1,1)+dt.timedelta(days=i)).isoformat(),'close':'6000'} for i in range(249)]
            data += [{'day':'2026-11-19','close':'6000'},{'day':'2026-11-20','close':'6000'},{'day':'2026-11-23','close':'9999'}]
            codes=['IC2611','IC2612','IC2703','IC2706']
            official=('合约代码,今收盘\n'+''.join(c+',6000\n' for c in codes)).encode('gbk')
            def fetch(url):return (200,('x('+json.dumps(data)+');').encode()) if url==m.ic.INDEX_URL else (200,official)
            def engine(args):
                out=Path(args[1]);cache=args[3]
                parsed=m.ic.parse_jsonp_array(m.ic._cache_path(cache,m.ic.INDEX_URL).read_text())
                self.assertEqual(parsed[-1]['day'],'2026-11-20')
                row={'date':'2026-11-20','index_close':6000,'ma250':6000,'ma_upper':6060,'ma_lower':5940,'contracts':[{'code':c,'price':6000,'days':1,'basis':0.0} for c in codes]}
                out.write_text(json.dumps({'latest':'2026-11-20','days':old['days']+[row]}))
            result=m.run_strategy(root,DAY,fetch,engine)
            self.assertEqual(result['state'],'updated')
            self.assertEqual(m.load(root/'data/ic_strategy.json')['days'][0],old['days'][0])
            self.assertEqual((root/'data/holdings.json').read_text(),'{"untouched":true}')

    def test_new_index_gap_stops_before_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            data=[{'day':(dt.date(2025,1,1)+dt.timedelta(days=i)).isoformat(),'close':'6000'} for i in range(249)]+[{'day':'2026-11-23','close':'6000'}]
            before=(root/'data/ic_strategy.json').read_bytes()
            with self.assertRaises(ValueError):
                m.run_strategy(root,dt.date(2026,11,23),lambda u:(200,('x('+json.dumps(data)+');').encode()),lambda a:self.fail('no engine'))
            self.assertEqual((root/'data/ic_strategy.json').read_bytes(),before)


class Workflow(unittest.TestCase):
    def test_schedules_and_write_scopes(self):
        root=Path(__file__).resolve().parents[1]
        for path,mode in [('ic-strategy.yml','strategy'),('preview-data.yml','members')]:
            text=(root/'.github/workflows'/path).read_text()
            self.assertIn('35,45,55 7 * * 1-5',text)
            self.assertIn('5,15,25,35,45,55 8-9 * * 1-5',text)
            self.assertIn('10 12,14 * * 1-5',text)
            self.assertIn('30 15 * * 1-5',text)
            self.assertIn('futures_close_update.py --mode '+mode,text)
            self.assertIn('cancel-in-progress: false',text)
            self.assertNotIn('git add .',text)
            self.assertIn('pages: write',text)
            self.assertIn('python3 futures_pages_sync.py',text)
        ic_text=(root/'.github/workflows/ic-strategy.yml').read_text()
        self.assertIn('ref: main',ic_text)
        self.assertIn('steps.compute.outputs.data_date',ic_text)
        self.assertIn('--verify-date "$EXPECTED_DATA_DATE"',ic_text)
        self.assertIn('set -euo pipefail',ic_text)
        text=(root/'.github/workflows/preview-data.yml').read_text()
        self.assertEqual(text.count("if: github.event.schedule == '30 15 * * 1-5' || inputs.full_refresh == true"),5)
        # Exact UTC slots -> 15:35..17:55, old 17:40, evening fallbacks.
        slots=[7*60+m for m in [35,45,55]]+[h*60+m for h in [8,9] for m in [5,15,25,35,45,55]]
        local=[m+8*60 for m in slots]
        self.assertEqual(local,list(range(15*60+35,18*60,10)))

class PagesPublishing(unittest.TestCase):
    def test_matching_data_does_not_request_build(self):
        import futures_pages_sync as p
        with patch('builtins.print'):
            result=p.main(['data/ic_strategy.json'],check=lambda paths:True,request=lambda:self.fail('no duplicate build'))
        self.assertEqual(result,0)

    def test_mismatch_requests_once_and_verifies(self):
        import futures_pages_sync as p
        checks=iter([False,False,True]);requests=[];clock=iter([0,1,2]);delays=[]
        with patch('builtins.print'):
            result=p.main(['data/ic_strategy.json'],check=lambda paths:next(checks),request=lambda:requests.append(1) or 'queued',sleep=delays.append,clock=lambda:next(clock))
        self.assertEqual(result,0);self.assertEqual(requests,[1]);self.assertEqual(delays,[12,10])
        with self.assertRaises(ValueError):p.main(['data/holdings.json'],check=lambda paths:True)

    def test_public_bytes_are_compared_exactly(self):
        import futures_pages_sync as p
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir();(root/'data/ic_strategy.json').write_bytes(b'{"latest":"2026-11-20"}')
            self.assertTrue(p.served(['data/ic_strategy.json'],root,lambda u:b'{"latest":"2026-11-20"}'))
            self.assertFalse(p.served(['data/ic_strategy.json'],root,lambda u:b'{"latest":"2026-11-19"}'))

if __name__=='__main__':unittest.main()
