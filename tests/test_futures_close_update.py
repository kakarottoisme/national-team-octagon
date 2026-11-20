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
            p.write_text('{"latest":"2026-11-20","days":[]}');before=p.read_bytes()
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
            data=[{'day':'2026-11-20','close':'6000'}]*250
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
        text=(root/'.github/workflows/preview-data.yml').read_text()
        self.assertEqual(text.count("if: github.event.schedule == '30 15 * * 1-5' || inputs.full_refresh == true"),5)
        # Exact UTC slots -> 15:35..17:55, old 17:40, evening fallbacks.
        slots=[7*60+m for m in [35,45,55]]+[h*60+m for h in [8,9] for m in [5,15,25,35,45,55]]
        local=[m+8*60 for m in slots]
        self.assertEqual(local,list(range(15*60+35,18*60,10)))

if __name__=='__main__':unittest.main()
