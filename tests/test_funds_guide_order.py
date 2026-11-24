from pathlib import Path
import re,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
class FundsGuideOrder(unittest.TestCase):
 def test_overview_and_guide_match(self):
  text=(ROOT/'index.html').read_text();cards=text.split('<div class="kpis">',1)[1].split('\n</div>',1)[0]
  labels=re.findall(r'<div class="lb">(.*?)</div>',cards)
  intro=text.split('<div class="msteps">',1)[1].split('   </div>',1)[0]
  self.assertEqual(re.findall(r'<b>(.*?)</b>',intro),labels)
  self.assertEqual(re.findall(r'<i>(.*?)</i>',intro),['1','2','3','4'])
  guide=text.split('<div class="msteps">',1)[1].split('<div class="mmore">',1)[0]
  descriptions=re.findall(r'<div class="fn">(.*?)</div>',guide)
  self.assertEqual(descriptions[:4],labels)
  self.assertEqual(set(descriptions),{'大额分钟成交额','主动净买入（推算）','同时点倍数','已归档天数','尾盘占比','七只合计成交额','上证收盘','图内元素'})
  self.assertNotIn('先看这个：',guide)
 def test_formula_and_other_sections_preserved(self):
  text=(ROOT/'index.html').read_text();before=subprocess.check_output(['git','show','ccbedd0f59c394e1b72763f0615a2bca5836b31e:index.html'],cwd=ROOT,text=True)
  formulas=lambda s:re.findall(r'<li>(.*?)</li>',s.split('<h4>公式</h4>',1)[1].split('<h4>样本与核对</h4>',1)[0])
  self.assertEqual(set(formulas(text)),set(formulas(before)))
  self.assertTrue(formulas(text)[0].startswith('大额分钟成交额'))
  self.assertTrue(formulas(text)[1].startswith('主动净买入'))
  self.assertTrue(formulas(text)[2].startswith('同时点倍数'))
  self.assertEqual(text.split('<div class="ui-mask" id="guideMask"',1)[0].replace('@media(min-width:700px){.msteps{grid-template-columns:repeat(4,minmax(0,1fr))}}','@media(min-width:700px){.msteps{grid-template-columns:repeat(3,minmax(0,1fr))}}'),before.split('<div class="ui-mask" id="guideMask"',1)[0])
  # This guard protects guide prose, not every later script on the page.
  samples=lambda s:s.split('<h4>样本与核对</h4>',1)[1].split('</ul>',1)[0]
  self.assertEqual(samples(text),samples(before))
 def test_requested_extra_copy_removed(self):
  text=(ROOT/'index.html').read_text();before=subprocess.check_output(['git','show','817ad84ac9b6819a71366e088ed10ce0492b86eb:index.html'],cwd=ROOT,text=True)
  subtitle='<span class="sub">每项都写清「是什么 / ≠ 什么」；公式与局限在底部</span>'
  limits=re.search(r'    <h4>已知局限</h4>\n    <ul>.*?    </ul>\n',before,re.S).group()
  self.assertEqual(text,before.replace(subtitle,'').replace(limits,''))
  self.assertNotIn('<h4>已知局限</h4>',text)
  self.assertNotIn(subtitle,text)
if __name__=='__main__':unittest.main()
