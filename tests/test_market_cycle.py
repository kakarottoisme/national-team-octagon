"""Offline rule and integration tests. Fixtures are synthetic, never market data."""
import datetime as dt
from pathlib import Path
import json
import math
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import market_cycle as m

ROOT=Path(__file__).resolve().parents[1]

def series(n=350):
    return [{'date':(dt.date(2025,1,1)+dt.timedelta(days=i)).isoformat(),'close':100+i*.07+math.sin(i/8)*3} for i in range(n)]

class CycleTests(unittest.TestCase):
    def test_features_no_lookahead(self):
        a=series();prefix=a[:300];expected=m.compute_features(prefix)
        a[-1]['close']=5000
        actual=m.compute_features(a)
        for d,f in expected.items():self.assertEqual(f,actual[d])
    def test_flat_features(self):
        a=series()
        for r in a:r['close']=100
        f=m.features(a)
        self.assertEqual(f['rsi14'],50)
        self.assertEqual(f['vol20'],0)
        self.assertFalse(f['hot']);self.assertFalse(f['washed'])
    def test_completed_bar_gate(self):
        a=series(350)
        rows=[[r['date'],r['close'],r['close'],r['close'],r['close']] for r in a]
        now=dt.datetime.fromisoformat(a[-1]['date']+'T14:00:00+08:00')
        raw={'data':{'sh000300':{'day':rows}}}
        r=m.fetch('sh000300',now,lambda _:raw)
        self.assertLess(r['history'][-1]['date'],now.date().isoformat())
    def test_bad_prices_rejected(self):
        rows=[[r['date'],r['close'],r['close'],r['close'],r['close']] for r in series()]
        rows[0][2]='NaN'
        with self.assertRaises(ValueError):m.fetch('sh000300',dt.datetime(2026,1,1,tzinfo=m.TZ),lambda _:{'data':{'sh000300':{'day':rows}}})
    def test_duplicate_rejected(self):
        rows=[[r['date'],r['close'],r['close'],r['close'],r['close']] for r in series()]
        rows.append(rows[0])
        with self.assertRaises(ValueError):m.fetch('sh000300',dt.datetime(2026,1,1,tzinfo=m.TZ),lambda _:{'data':{'sh000300':{'day':rows}}})
    def test_future_outcomes_not_filled(self):
        a=series(350);sig=[{'date':a[-1]['date'],'top':'x','bottom':'y'}]
        f=m.outcomes(a,sig,'x')[0]['forward']
        self.assertIsNone(f['21']);self.assertIsNone(f['63'])
    def test_margin_never_uses_future(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'m.json';p.write_text(json.dumps({'margin':{'2026-11-18':20000,'2026-11-20':90000}}))
            self.assertEqual(m.margin_context(p,'2026-11-19')['balance'],20000)
    def test_missing_market_rejected(self):
        with self.assertRaises(ValueError):m.build_market('cn',{},dt.datetime.now(m.TZ))
    def test_failure_preserves_date(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'result.json';p.write_text(json.dumps({'markets':{'cn':{'name':'A股','as_of':'2026-11-18','status':'ok'},'us':{'name':'美股','as_of':'2026-11-18','status':'ok'}}}))
            with patch.object(m,'fetch',side_effect=ValueError('source unavailable')):m.collect(p,dt.datetime(2026,11,20,14,tzinfo=m.TZ))
            r=json.loads(p.read_text());self.assertEqual(r['markets']['cn']['as_of'],'2026-11-18');self.assertEqual(r['markets']['cn']['status'],'stale')
    def test_page_js_parses(self):
        import re
        text=(ROOT/'market-cycle.html').read_text()
        for script in re.findall(r'<script>(.*?)</script>',text,re.S):
            r=subprocess.run(['node','--check','-'],input=script,text=True,capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr)
    def test_independent_output_workflow(self):
        y=(ROOT/'.github/workflows/market-cycle.yml').read_text()
        self.assertIn('git add -- data/market_cycle.json',y)
        self.assertNotIn('workflow run',y)
        for file in ['live.json','holdings.json','ic_strategy.json']:self.assertNotIn(file,y)
    def test_judgment_hot_then_break(self):
        base={'close':110,'ma20':100,'ma50':100,'ma60':100,'ma200':100,'below200_3':False,'above20_3':False,'above50_3':False,'dd63':0,'hot':True,'washed':False,'rebound':0,'rsi14':75,'vol20':20}
        matrices={str(i):{'old':dict(base),'new':dict(base,close=90,dd63=-8,hot=False)} for i in range(3)}
        self.assertEqual(m.judge('new',matrices,['old','new'],'0')['top'],'顶部风险确认')
    def test_oversold_is_not_confirmed_bottom(self):
        base={'close':70,'ma20':90,'ma50':95,'ma60':100,'ma200':100,'below200_3':True,'above20_3':False,'above50_3':False,'dd63':-30,'hot':False,'washed':True,'rebound':0,'rsi14':20,'vol20':35}
        matrices={str(i):{'day':dict(base)} for i in range(3)}
        r=m.judge('day',matrices,['day'],'0')
        self.assertEqual(r['top'],'中期下行压力');self.assertEqual(r['bottom'],'底部观察区')
    def test_volatility_peak_blocks_improvement(self):
        old={'close':70,'ma20':90,'ma50':95,'ma60':100,'ma200':100,'below200_3':True,'above20_3':False,'above50_3':False,'dd63':-30,'hot':False,'washed':True,'rebound':0,'rsi14':20,'vol20':35}
        new=dict(old,close=94,ma20=90,above20_3=True,rebound=10,rsi14=50,washed=False,vol20=40)
        matrices={str(i):{'old':dict(old),'new':dict(new)} for i in range(3)}
        r=m.judge('new',matrices,['old','new'],'0')
        self.assertEqual(r['bottom'],'底部观察区');self.assertEqual(r['breadth']['improving'],0)
    def test_navigation(self):
        self.assertIn("'market-cycle.html'",(ROOT/'dashboard.html').read_text())
        self.assertIn("page === 'market-cycle.html'",(ROOT/'dashboard-tabs.js').read_text())
        self.assertIn('repeat(5,minmax(0,1fr))',(ROOT/'dashboard-shell.css').read_text())

if __name__=='__main__':unittest.main()
