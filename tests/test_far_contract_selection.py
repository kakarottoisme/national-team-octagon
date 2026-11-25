"""Four-listed selection expansion effective on future signals; old history preserved."""
from pathlib import Path
import datetime as dt,json,subprocess,unittest
import ic_strategy as ic
ROOT=Path(__file__).resolve().parents[1]

class FarContractSelection(unittest.TestCase):
 def test_historical_rule_then_future_rule(self):
  for day,want in [(dt.date(2026,11,25),3),(dt.date(2026,11,26),4)]:
   slots=ic.list_slots(day,set());prices={s['code']:6000 for s in slots}
   self.assertEqual(len(ic.eligible_codes(slots,prices)),want)
 def test_far_can_win_open_and_switch(self):
  slots=ic.list_slots(dt.date(2026,11,26),set());prices={s['code']:6000 for s in slots}
  rates={slots[0]['code']:.10,slots[1]['code']:.11,slots[2]['code']:.12,slots[3]['code']:.15}
  self.assertEqual(ic.fresh_entry(slots,prices,rates),slots[3]['code'])
  got=ic.decide_position(dt.date(2026,11,26),slots,prices,rates,slots[0]['code'],set(),4)
  self.assertEqual(got['contract'],slots[3]['code']);self.assertEqual(got['action'],'switch')
  rates[slots[3]['code']]=.119
  self.assertEqual(ic.choose_switch(slots[0]['code'],ic.eligible_codes(slots,prices),rates),slots[2]['code'])
 def test_original_thresholds_and_missing_quote_still_apply(self):
  slots=ic.list_slots(dt.date(2026,11,26),set());prices={s['code']:6000 for s in slots};prices[slots[3]['code']]=None
  self.assertNotIn(slots[3]['code'],ic.eligible_codes(slots,prices))
  prices[slots[3]['code']]=6000;slots[3]['days']=9
  # eligibility derives as-of from dated first slot, never from a missing date.
  self.assertNotIn(slots[3]['code'],ic.eligible_codes(slots,prices))
  self.assertEqual((ic.OPEN_GAP,ic.SWITCH_GAP,ic.MIN_DAYS),(.03,.02,10))
 def test_old_signal_decisions_identical_after_expansion(self):
  source=json.loads((ROOT/'data/ic_strategy.json').read_text());days=source['days'];hol=ic.infer_holidays([d['date'] for d in days]);held=days[0]['contract']
  original=json.dumps(source,sort_keys=True)
  for day in days[1:]:
   slots=ic.list_slots(dt.date.fromisoformat(day['date']),hol)
   prices={c['code']:c['price'] for c in day['contracts']}
   rates={s['code']:ic.ann_discount(day['index_close'],prices[s['code']],s['days']) for s in slots if s['days']>0}
   result=ic.decide_position(dt.date.fromisoformat(day['date']),slots,prices,rates,held,hol,day['target'])
   self.assertEqual(result['contract'],day['contract'],day['date']);held=result['contract']
  self.assertEqual(original,json.dumps(source,sort_keys=True))
 def test_far_status_changes_only_at_effective_date(self):
  self.assertEqual(ic.status_text('far',200,6000,'IC2706',None,False),'下季不参与')
  self.assertEqual(ic.status_text('far',200,6000,'IC2706',None,True),'可比较')
  self.assertEqual(ic.status_text('far',200,None,'IC2706',None,True),'无行情')
 def test_js_comparison_matches_current_and_can_choose_far(self):
  src=(ROOT/'futures-preview.html').read_text();fn='function fourContractComparison('+src.split('function fourContractComparison(',1)[1].split('window.fourContractComparison=',1)[0]
  day=next(d for d in json.loads((ROOT/'data/ic_strategy.json').read_text())['days'] if d['date']=='2026-11-25')
  check='''
const assert=require('assert');let x=fourContractComparison(day,true);assert.equal(x.pick,'IC2612');assert.equal(x.switchPick,null);
let f=x.rows.find(r=>r.code==='IC2706');assert.equal(f.status,'参与比较，未触发');assert(Math.abs(f.nearGap+0.19537)<0.00001);
let old=fourContractComparison(day,false);assert.equal(old.rows.find(r=>r.code==='IC2706').status,'旧规则未纳入');
day.contracts.at(-1).price=day.index_close*(1-.40*day.contracts.at(-1).days/365);
x=fourContractComparison(day,true);assert.equal(x.pick,'IC2706');assert.equal(x.switchPick,'IC2706');
day.contracts.at(-1).price=null;x=fourContractComparison(day,true);assert.equal(x.pick,'IC2612');
'''
  r=subprocess.run(['node','-'],input=fn+'const day='+json.dumps(day)+';'+check,text=True,capture_output=True)
  self.assertEqual(r.returncode,0,r.stderr)
 def test_only_authorized_ic_functions_change(self):
  import ast,hashlib
  baseline=subprocess.check_output(['git','show','915544a:ic_strategy.py'],cwd=ROOT,text=True)
  expected={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest() for n in ast.parse(baseline).body if isinstance(n,ast.FunctionDef) and n.name!='main'}
  current={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest() for n in ast.parse((ROOT/'ic_strategy.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name!='main'}
  allowed={'eligible_codes','status_text','simulate','build_payload','far_enabled'}
  self.assertEqual({k:v for k,v in expected.items() if k not in allowed},{k:v for k,v in current.items() if k not in allowed})

if __name__=='__main__':unittest.main()
