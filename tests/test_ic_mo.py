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
 def test_ui_plan_projection(self):
  import subprocess
  root=Path(__file__).resolve().parents[1]
  script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
  let code=fs.readFileSync('ic_mo_panel.js','utf8');code=code.replace('setInterval(()=>{if(!document.hidden)render()},30000);window.refresh();','globalThis.verify={set:p=>PACK=p,projectExpected,orderReason,putCosts,freshPut};');
  const c={document:{getElementById:()=>({})},window:{uiEsc:s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')},Intl,Date,console};vm.createContext(c);vm.runInContext(code,c);
  const p=JSON.parse(fs.readFileSync('data/ic_mo_strategy.json')),saved=JSON.stringify(p),f=c.verify;f.set(p);const r=p.close,out=f.projectExpected(r);
  assert.deepStrictEqual(Object.fromEntries(out.ic.map(z=>[z.code,z.qty])),r.ic_target);assert.deepStrictEqual(Object.fromEntries(out.mo.map(z=>[z.code,z.qty])),r.mo_target);
  for(const z of [...out.ic,...out.mo]){assert.strictEqual(z.batches.reduce((a,b)=>a+b.qty,0),z.qty);assert.ok(Number.isFinite(z.average));}
  const fresh=f.freshPut(r.snapshot);const eligible=Object.entries(r.snapshot.options).filter(([code,q])=>code.includes('-P-')&&q.close>0&&q.volume>=500&&q.oi>=100&&q.delta!=null&&Math.abs(q.delta)>=.05&&Math.abs(q.delta)<=.98&&q.days>=15&&q.days<=90&&Math.abs(q.strike/r.snapshot.index1000-.95)<=.04).sort((a,b)=>((Math.abs(a[1].days-45)/45+Math.abs(a[1].strike/r.snapshot.index1000-.95)/.025)-(Math.abs(b[1].days-45)/45+Math.abs(b[1].strike/r.snapshot.index1000-.95)/.025))||b[1].volume-a[1].volume||a[0].localeCompare(b[0]));assert.strictEqual(fresh?.code,eligible[0]?.[0]);
  if(!r.trades.some(t=>t.asset==='option'&&t.quantity>0))assert.ok(f.putCosts(r).includes('本次无需买入'));assert.ok(f.putCosts(r).includes('不是当前账本的加仓指令'));
  const old=Object.keys(p.state.futures)[0],next=Object.keys(r.ic_target)[0],switchTrade=r.trades.find(t=>t.asset==='future');if(old&&next&&old!==next&&switchTrade)assert.ok(/主动切换|强制换月/.test(f.orderReason(switchTrade,r)));
  for(const z of out.ic){const added=r.trades.filter(t=>t.code===z.code&&t.quantity>0).reduce((a,t)=>a+t.quantity,0);if(!p.state.futures[z.code]&&added===z.qty)assert.strictEqual(z.average,r.snapshot.futures[z.code].close);}
  assert.strictEqual(JSON.stringify(p),saved);
  const flat=JSON.parse(JSON.stringify(r));flat.ic_target={};flat.mo_target={};flat.trades=[...Object.entries(p.state.futures).map(([code,n])=>({code,quantity:-n,asset:'future'})),...Object.entries(p.state.options).map(([code,n])=>({code,quantity:-n,asset:'option'}))];const empty=f.projectExpected(flat);assert.strictEqual(empty.ic.length+empty.mo.length,0);assert.strictEqual(empty.coverage,null);
  console.log('Projection, FIFO, concrete switch reason, fresh candidate and no state mutation verified');'''
  result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
if __name__=='__main__':unittest.main()
