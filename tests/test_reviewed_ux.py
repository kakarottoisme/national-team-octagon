"""Regression tests for the four-page UX review; no live market or account writes."""
from pathlib import Path
import re, subprocess, unittest
ROOT=Path(__file__).resolve().parents[1]
class ReviewedUX(unittest.TestCase):
 def source(self,name):return (ROOT/name).read_text()
 def js(self,code):
  r=subprocess.run(['node','-e',code],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr)
 def test_baseline_uses_exact_matching_denominator(self):
  s=self.source('index.html')
  self.assertIn('alignedTotal/dens.reduce',s)
  self.assertIn('dens.push(st.den)',s)
  self.assertNotIn('dens.push(amt/st.x)',s)
 def test_date_changes_are_queued_and_not_mixed(self):
  s=self.source('index.html')
  self.assertIn('if(busy){refreshQueued=true;return}',s)
  self.assertIn('if(view!==requestView)return',s)
 def test_missing_minute_values_do_not_throw(self):
  s=self.source('index.html');self.assertIn('value:uiNumber(v)',s)
  self.assertNotIn('value:+v.toFixed(3)',s)
 def test_d_boundary_and_missing_input(self):
  s=self.source('futures-preview.html');a=s.index(' function liveAddon(');b=s.index(' function accountSnap(',a)
  self.js(s[a:b]+"const assert=require('assert');assert.deepStrictEqual(liveAddon(4,null,6000),{d:0,need:null});assert.equal(liveAddon(4,600000,6000).d,2);assert.equal(liveAddon(9,9999999,6000).d,1);assert.equal(liveAddon(10,9999999,6000).d,0);assert.equal(liveAddon(4,-100,6000).d,0)")
 def test_d_exit_respected_and_history_not_real_account(self):
  s=self.source('futures-preview.html');self.assertIn('day.index_close<day.ma_lower||base<=0',s);self.assertIn("day.date!==latest.date?' · 回测'",s)
 def test_negative_c_and_limits_are_visible(self):
  s=self.source('futures-preview.html');self.assertIn("['C',+day.leg_c||0,'c']",s);self.assertIn('底仓限幅至',s);self.assertIn('3% ≤ 贴水 <10% 为 0',s)
 def test_net_short_definition_is_not_reversed(self):
  s=self.source('futures-preview.html');self.assertIn('空−多',s);self.assertNotIn('净空=多−空',s)
 def test_grid_width_scoped(self):
  s=self.source('futures-preview.html');self.assertIn('table.lv{min-width:0',s);self.assertIn('width:65%',s)
 def test_margin_no_fake_connection_or_dual_axis(self):
  s=self.source('margin.html');self.assertNotIn('connectNulls:true',s);self.assertNotIn('type:"log"',s);self.assertIn("gridIndex:1",s);self.assertIn('setMarginRange',s)
 def test_risk_filter_and_negative_real_yields(self):
  s=self.source('risk-preview.html');self.assertIn("curveBox.hidden=cur!=='全部'&&cur!=='宏观利率'",s);self.assertNotIn('yAxis.min=0',s);self.assertIn("data:hist.map(h=>h.date),axisLabel",s)
 def test_source_values_are_escaped(self):
  s=self.source('risk-preview.html');self.assertIn('esc(typeof m.value',s);self.assertIn('esc(ten)',s)
 def test_safe_fetch_timeout(self):
  for name in ['index.html','margin.html','futures-preview.html','risk-preview.html','etf-flows.html']:
   s=self.source(name);self.assertIn('AbortController',s);self.assertIn('12000',s);self.assertIn('cdnjs.cloudflare.com/ajax/libs/echarts/5.4.3/',s)
if __name__=='__main__':unittest.main()
