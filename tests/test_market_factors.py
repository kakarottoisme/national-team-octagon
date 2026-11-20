"""Offline synthetic and saved-output invariants for factor-v2; no market acquisition."""
import copy
import datetime as dt
import json
from pathlib import Path
import unittest
import market_cycle as m
import market_factors as f

class FactorTests(unittest.TestCase):
    def matrix(self, kind='flat', n=6):
        days=[(dt.date(2025,1,1)+dt.timedelta(days=i)).isoformat() for i in range(64)]
        base={'close':100,'ma20':100,'ma50':100,'bias200':0,'bias_pctile':50,'ret63':0,'ret5':0,'rsi14':50,'drawdown':0,'vol20':20,'above20_3':False,'above50_3':False,'hot':False,'washed':False}
        if kind=='oversold':base.update(close=70,ma20=90,ma50=95,drawdown=-30,rsi14=20,washed=True)
        matrix={str(i):{d:dict(base) for d in days} for i in range(n)}
        return days,matrix
    def test_equal_groups_and_fixed_denominator(self):
        days,matrix=self.matrix('oversold');r=f.score_day(days[-1],matrix,days[-63:],'0')
        b=r['scores']['bottom'];self.assertEqual(b['score'],25);self.assertEqual(b['met'],2);self.assertEqual(b['improvement_met'],0)
        self.assertEqual(b['score'],sum(x['score'] for x in b['groups'])/4)
        self.assertEqual(b['total'],8)
    def test_missing_not_zero_or_smaller_denominator(self):
        days,matrix=self.matrix();matrix['0'][days[-1]]['ret5']=None
        r=f.score_day(days[-1],matrix,days[-63:],'0')
        for s in r['scores'].values():self.assertIsNone(s['score']);self.assertEqual(s['total'],8);self.assertEqual(s['missing'],8)
    def test_warmup_is_not_low_score(self):
        days,matrix=self.matrix();r=f.score_day(days[-1],matrix,days[-30:],'0')
        self.assertIsNone(r['scores']['top']['score'])
    def test_future_context_rejected(self):
        days,matrix=self.matrix()
        with self.assertRaises(ValueError):f.score_day(days[-2],matrix,days[-63:],'0')
    def test_decline_without_hot_not_top(self):
        days,matrix=self.matrix('oversold');r=f.score_day(days[-1],matrix,days[-63:],'0')
        self.assertEqual(r['scores']['top']['score'],0)
    def test_top_thresholds_and_majority(self):
        days,matrix=self.matrix()
        for i in range(3):matrix[str(i)][days[-1]].update(bias200=15,bias_pctile=90,ret63=20,rsi14=70,hot=True)
        r=f.score_day(days[-1],matrix,days[-63:],'0');self.assertEqual(r['scores']['top']['score'],50)
        matrix['2'][days[-1]].update(bias200=14.99,bias_pctile=89.99,ret63=19.99,rsi14=69.99)
        self.assertEqual(f.score_day(days[-1],matrix,days[-63:],'0')['scores']['top']['score'],0)
    def test_recovery_requires_oversold_context(self):
        days,matrix=self.matrix()
        for mm in matrix.values():mm[days[-1]].update(close=110,above20_3=True,above50_3=True,ret5=5,rsi14=60,vol20=10)
        self.assertEqual(f.score_day(days[-1],matrix,days[-63:],'0')['scores']['bottom']['score'],0)
        for mm in matrix.values():mm[days[10]]['washed']=True
        r=f.score_day(days[-1],matrix,days[-63:],'0')['scores']['bottom']
        self.assertEqual(r['score'],75);self.assertEqual(r['improvement_met'],6)
    def test_bin_edges(self):
        self.assertEqual([f.bin_id(x) for x in (0,24.99,25,49.99,50,74.99,75,100)],[0,0,1,1,2,2,3,3])
        self.assertIsNone(f.bin_id(None));self.assertIsNone(f.bin_id(101))
    def test_future_windows_null(self):
        history=[{'date':str(i),'close':100+i} for i in range(80)]
        self.assertIsNone(f.forward(history,79,21));self.assertEqual(f.forward(history,0,21)['end_close'],121)
    def test_stat_no_samples_null(self):
        self.assertIsNone(f.stats([],'top')['hit_rate'])
    def test_no_circular_probability_fields(self):
        days,matrix=self.matrix();r=f.score_day(days[-1],matrix,days[-63:],'0')
        self.assertNotIn('probability',json.dumps(r));self.assertNotIn('holdings',json.dumps(r))
    def test_saved_package_contract(self):
        p=Path(m.ROOT)/'data/market_cycle.json'
        if not p.exists():self.skipTest('no local payload')
        data=json.loads(p.read_text())
        if data.get('schema')!='market-factor-2':self.skipTest('pre-upgrade payload')
        for market in data['markets'].values():
            if market['status']!='ok':continue
            for side,score in market['latest']['scores'].items():
                self.assertEqual(score['total'],8)
                if score['score'] is not None:self.assertEqual(score['score'],score['met']/8*100)
            for b in market['evaluation']['bins']:
                self.assertEqual(len(b['events']),b['stats']['n'])
                for a,z in zip(b['events'],b['events'][1:]):self.assertGreater(z['idx']-a['idx'],b['horizon'])

if __name__=='__main__':unittest.main()
