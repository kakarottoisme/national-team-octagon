import copy,datetime as dt,json,math,unittest
from pathlib import Path
import ic_mo_engine as e
from ic_mo_update import holdings,implied_put_delta
class ICModel(unittest.TestCase):
 def test_grid(self):
  for price,want in [(5000,10),(5000.1,8),(5250,8),(5500,6),(6000,4),(6500,2),(10000,2)]:self.assertEqual(e.a_leg(price),want)
 def test_fifo(self):
  bs=e.replay_costs({},[{'code':'MO2612-P-6200','date':'x','quantity':2,'price':100,'tag':'a'},{'code':'MO2612-P-6200','date':'y','quantity':3,'price':200,'tag':'a'},{'code':'MO2612-P-6200','date':'z','quantity':-3,'price':300,'tag':'a'}]);self.assertEqual(bs['MO2612-P-6200'],[{'date':'y','qty':2,'price':200,'source':'a'}])
 def test_false_position(self):
  with self.assertRaises(ValueError):e.replay_costs({},[{'code':'X','date':'x','quantity':-1,'price':10,'tag':'a'}])
 def test_implied_guard(self):
  self.assertIsNone(e.implied_put_delta(6000,6200,20,1));self.assertTrue(-1<e.implied_put_delta(6000,5700,30,100)<0)
 def test_packet(self):
  p=json.loads((Path(__file__).resolve().parents[1]/'data/ic_mo_strategy.json').read_text());s=p['state'];r=p['close'];snap=r['snapshot'];self.assertEqual(s['date'],snap['date']);self.assertTrue(s['equity']>0)
  before=copy.deepcopy(s);ns,tr=e.step(s,snap,snap,r['valid_for'],False);self.assertEqual(before,s);self.assertEqual(ns['futures'],r['ic_target']);self.assertEqual(ns['options'],r['mo_target']);self.assertEqual(tr['trades'],r['trades'])
  for pack in (s['futures'],s['options']):
   for k,n in pack.items():self.assertEqual(sum(x['qty'] for x in s['batches'][k]),n)
  for z in r['expected']['mo']:self.assertAlmostEqual(z['value'],z['qty']*z['mark']*100);self.assertAlmostEqual(z['unrealized'],(z['mark']-z['average'])*z['qty']*100)
 def test_opt_candidates(self):
  pack={'MO2612-P-5700':{'close':100,'volume':500,'oi':100,'delta':-.4,'days':45,'strike':5700}};self.assertEqual(e.select_put(pack,6000),'MO2612-P-5700');pack['MO2612-P-5700']['delta']=None;self.assertIsNone(e.select_put(pack,6000))
if __name__=='__main__':unittest.main()
