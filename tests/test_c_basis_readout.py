from pathlib import Path
import subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
class CBasisReadout(unittest.TestCase):
 def test_decision_current_and_next_are_distinct_and_date_bound(self):
  s=(ROOT/'futures-preview.html').read_text();code=s.split(' function cBasisReadout(',1)[1].split(' function liveAddon(',1)[0];code='function cBasisReadout('+code
  stub="const assert=require('assert');const esc=s=>String(s).replace(/</g,'&lt;');const pct=v=>v==null?'—':(v*100).toFixed(2)+'%';\n"
  checks="""
let d={date:'2026-11-23',c_set_on:'2026-11-19',c_contract:'IC2612',c_basis:.146876,next_roll:'2026-12-17',contracts:[{code:'IC2701',role:'next',basis:.135502},{code:'IC2612',role:'near',basis:.169112}]};
let r=cBasisReadout(d,d.date);assert.equal(r.decisionValue,'14.69%');assert.equal(r.currentValue,'16.91%');assert.equal(r.currentTitle,'最新贴水');assert(r.decisionNote.includes('2026-11-19 · IC2612'));assert(r.currentNote.includes('2026-11-23 收盘 · IC2612'));assert(r.nextNote.includes('IC2701 · 2026-11-23 收盘参考 13.55%'));assert(!JSON.stringify(r).includes('今日'));
r=cBasisReadout(d,'2026-11-24');assert.equal(r.currentTitle,'当日贴水');assert(r.currentNote.includes('2026-11-23'));
d={...d,contracts:[{code:'IC2701',role:'next',basis:.135502}]};r=cBasisReadout(d,d.date);assert.equal(r.currentValue,'—');assert(r.currentNote.includes('数据缺失'));assert.equal(r.decisionValue,'14.69%');
d={...d,contracts:[]};r=cBasisReadout(d,d.date);assert(r.nextNote.includes('数据缺失'));
"""
  p=subprocess.run(['node','-'],input=stub+code+checks,text=True,capture_output=True);self.assertEqual(p.returncode,0,p.stderr)
  self.assertIn("blk('本轮判定',cRead.decisionValue,cRead.decisionNote)",s)
  self.assertIn('blk(cRead.currentTitle,cRead.currentValue,cRead.currentNote)',s)
if __name__=='__main__':unittest.main()
