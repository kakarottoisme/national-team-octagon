"""FIFO estimated holdings: actual-price-independent, full historical prefix checks."""
from pathlib import Path
from decimal import Decimal as D
import copy,json,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
PAGE=ROOT/'futures-preview.html'

def run(rows,checks=''):
    src=PAGE.read_text()
    datefn=src.split('function validAccountDate(',1)[1].split('function validateLocalAccount(',1)[0]
    replay=src.split('function replayExpectedHoldings(',1)[1].split('window.replayExpectedHoldings=',1)[0]
    code='const assert=require("assert");function validAccountDate('+datefn+'function replayExpectedHoldings('+replay+'\nconst rows='+json.dumps(rows)+';const result=replayExpectedHoldings(rows);'+checks+'\nconsole.log(JSON.stringify([...result.values()]));'
    r=subprocess.run(['node','-'],input=code,text=True,capture_output=True)
    if r.returncode:raise AssertionError(r.stderr)
    return json.loads(r.stdout)

def row(date,lots,price=100,contract='IC2612',action='hold',extra=None):
    return {'date':date,'total':lots,'contract':contract if lots else None,'action':action,'entry_pick':'IC2701','contracts':[{'code':contract,'price':price}]+(extra or [])}

class ExpectedHoldings(unittest.TestCase):
    def test_fifo_partial_reduction_preserves_newer_cost(self):
        rows=[row('2026-11-02',0),row('2026-11-03',2,100,action='open'),row('2026-11-04',4,200),row('2026-11-05',3,300),row('2026-11-06',2,400)]
        got=run(rows)
        self.assertEqual(got[3]['average'],500/3)
        self.assertEqual(got[-1]['average'],200)
        self.assertEqual(got[-1]['entryDate'],'2026-11-04')
        self.assertEqual(got[-1]['unrealized'],80000)
        self.assertEqual(got[2]['batches'][0]['lots'],2)
    def test_same_day_roll_and_size_change(self):
        rows=[row('2026-11-02',4,100,action='open'),row('2026-11-03',6,200,'IC2701','roll',[{'code':'IC2612','price':110}])]
        last=run(rows)[-1]
        self.assertEqual([(b['lots'],b['price']) for b in last['batches']],[(6,200)])
        self.assertEqual(last['realized'],8000)
        self.assertEqual(last['unrealized'],0)
    def test_initial_hold_is_unknown_until_full_exit_or_roll(self):
        rows=[row('2026-11-02',2),row('2026-11-03',4,200),row('2026-11-04',2,300)]
        got=run(rows)
        self.assertIsNone(got[0]['average']);self.assertIsNone(got[1]['average'])
        self.assertEqual(got[-1]['average'],200)
        self.assertIsNone(got[-1]['realized'])
    def test_flat_and_reopen_resets_cycle(self):
        got=run([row('2026-11-02',2,100,action='open'),row('2026-11-03',0,90),row('2026-11-04',3,200,action='open')])
        self.assertEqual(got[1]['lots'],0);self.assertEqual(got[1]['unrealized'],0)
        self.assertEqual(got[-1]['cycleStart'],'2026-11-04');self.assertEqual(got[-1]['realized'],0)
    def test_missing_price_does_not_use_index_or_other_contract(self):
        got=run([row('2026-11-02',2,None,action='open'),row('2026-11-03',2,200)])
        self.assertIsNone(got[0]['average']);self.assertIsNone(got[-1]['unrealized'])
        self.assertIsNone(got[0]['margin'])
    def test_bad_dates_duplicates_and_lots_rejected(self):
        for rows in [[row('2026-11-02',2)]*2,[row('2026-02-30',2)],[row('2026-11-02',11)],[row('2026-11-02',2.5)]]:
            with self.assertRaises(AssertionError):run(rows)
    def test_roll_price_does_not_create_cross_contract_pnl(self):
        last=run([row('2026-11-02',2,100,action='open'),row('2026-11-03',2,50,'IC2701','switch',[{'code':'IC2612','price':110}])])[-1]
        self.assertEqual(last['realized'],4000);self.assertEqual(last['average'],50)
    def test_entire_published_history_matches_decimal_fifo(self):
        rows=json.loads((ROOT/'data/ic_strategy.json').read_text())['days']
        actual=run(rows);batches=[];held=None
        for i,(day,out) in enumerate(zip(rows,actual)):
            target=day['total'];code=day['contract'];prices={r['code']:r['price'] for r in day['contracts']}
            n=sum(b[1] for b in batches)
            close=n if held!=code else max(0,n-target)
            while close:
                take=min(close,batches[0][1]);batches[0][1]-=take;close-=take
                if batches[0][1]==0:batches.pop(0)
            buy=target-sum(b[1] for b in batches)
            if buy:
                p=None if i==0 and day['action']=='hold' else prices.get(code)
                batches.append([day['date'],buy,None if p is None else D(str(p))])
            held=code if target else None
            self.assertEqual(out['lots'],target)
            self.assertEqual([(b['date'],b['lots']) for b in out['batches']],[(b[0],b[1]) for b in batches])
            if target and all(b[2] is not None for b in batches):
                avg=sum(b[1]*b[2] for b in batches)/target
                self.assertAlmostEqual(out['average'],float(avg),places=8)
                if prices.get(code) is not None:
                    pnl=sum(b[1]*(D(str(prices[code]))-b[2])*200 for b in batches)
                    self.assertEqual(D(str(out['unrealized'])),pnl)
        by={r['date']:r for r in actual};nov=by['2026-11-25']
        self.assertEqual([(b['date'],b['lots'],b['price']) for b in nov['batches']],[('2026-11-19',4,6179),('2026-11-25',2,5769)])
        self.assertAlmostEqual(nov['average'],6042.333333333333)
        self.assertEqual(nov['unrealized'],-328000)
        self.assertEqual(nov['margin'],830736)
        self.assertEqual(nov['cycleStart'],'2026-10-15')
        cutoff=[r for r in rows if r['date']<='2026-11-19']
        self.assertEqual(run(cutoff)[-1],by['2026-11-19'])
    def test_ui_estimate_is_not_real_account(self):
        src=PAGE.read_text()
        self.assertIn('预计当前持仓',src);self.assertIn('先进先出',src)
        self.assertIn('${expected(r)}',src)
        self.assertNotIn("readJSON('data/holdings.json",src)
        self.assertIn('累计期货净收益的50%',src)
        self.assertIn('不是已核验真实账户',src)
        self.assertIn('IC 预计合约 / 手数',src)

if __name__=='__main__':unittest.main()
