"""Display-only ETF/index mapping, date alignment, dual units and preservation."""
from pathlib import Path
import json,re,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'margin.html').read_text()
HELPERS=SOURCE[SOURCE.index('const ETF_BENCHMARKS='):SOURCE.index('function readRecentIndex(')]
class Overlay(unittest.TestCase):
 def js(self,expression):
  code="const uiNumber=v=>v==null||v===''||!Number.isFinite(+v)?null:+v;\n"+HELPERS+'\nconsole.log(JSON.stringify('+expression+'));'
  return json.loads(subprocess.run(['node','-'],input=code,text=True,capture_output=True,check=True).stdout)
 def test_complete_exact_mapping(self):
  expected={'510300':['沪深300','sh000300'],'510310':['沪深300','sh000300'],'510330':['沪深300','sh000300'],'159919':['沪深300','sh000300'],'510050':['上证50','sh000016'],'510180':['上证180','sh000010'],'510500':['中证500','sh000905'],'512500':['中证500','sh000905'],'512100':['中证1000','sh000852'],'159845':['中证1000','sh000852'],'560010':['中证1000','sh000852'],'159629':['中证1000','sh000852'],'588000':['科创50','sh000688'],'588080':['科创50','sh000688'],'159915':['创业板指','sz399006'],'159977':['创业板指','sz399006'],'159352':['中证A500','sh000510']}
  self.assertEqual(self.js('ETF_BENCHMARKS'),expected)
 def test_by_date_not_row_or_forward_fill(self):
  self.assertEqual(self.js("benchmarkValues('510300',['2026-01-02','2026-01-05','2026-01-06'],{'沪深300':{'2026-01-06':3900,'2026-01-02':4000}})"),[4000,None,3900])
 def test_invalid_index_is_missing_not_zero(self):
  self.assertEqual(self.js("benchmarkValues('159352',['a','b','c','d'],{'中证A500':{a:0,b:-5,c:'oops',d:4200}})"),[None,None,None,4200])
  self.assertEqual(self.js("benchmarkValues('unknown',['a'],{'沪深300':{a:123}})"),[None])
 def test_jsonp_filters_future_and_invalid(self):
  self.assertEqual(self.js("validateIndexRows([{day:'2026-11-23',close:'4000'},{day:'2026-11-24',close:'4100'},{day:'bad',close:999},{day:'2026-11-20',close:0}], '2026-11-23')"),{'2026-11-23':4000})
 def test_two_series_different_units(self):
  one=SOURCE[SOURCE.index('function drawOne()'):SOURCE.index('async function refreshIndexReference()')]
  self.assertIn("y('份额（亿份）'",one);self.assertIn("y('指数（点）'",one)
  self.assertIn("yAxisIndex:0,data:series",one);self.assertIn("yAxisIndex:1,data:indexSeries",one)
  self.assertIn("isIndex?' 点':' 亿份'",one);self.assertIn('connectNulls:false',one)
  self.assertNotIn('etf.close',one)
 def test_no_new_workflow_or_existing_data_write(self):
  self.assertNotIn('workflow_dispatch',SOURCE)
  self.assertIn("'data/etf_index_reference.json?t='",SOURCE)
  ref=json.loads((ROOT/'data/etf_index_reference.json').read_text())
  self.assertEqual(set(ref['indices']),{'上证180','中证A500'})
  for name,row in ref['indices'].items():
   self.assertEqual(min(row['dates']),'2012-01-04');self.assertGreaterEqual(max(row['dates']),'2026-11-23')
   self.assertTrue(all(isinstance(v,(int,float)) and v>0 for v in row['dates'].values()))
   self.assertIn('finance.sina.com.cn/realstock/company/',row['source_url'])
 def test_share_calculations_preserved(self):
  old=subprocess.check_output(['git','show','origin/main:margin.html'],cwd=ROOT,text=True)
  for a,b in [('function drawAgg()','function drawOne()'),('function renderKpis()','async function refreshFlows()')]:
   self.assertEqual(SOURCE[SOURCE.index(a):SOURCE.index(b)],old[old.index(a):old.index(b)])
if __name__=='__main__':unittest.main()
