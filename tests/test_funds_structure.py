"""申赎两融页面结构与原金融逻辑保护；不联网、不写账户数据。"""
from pathlib import Path
import re, subprocess, unittest
ROOT=Path(__file__).resolve().parents[1]
class FundsStructure(unittest.TestCase):
    def test_four_main_tabs(self):
        for name in ['index.html','margin.html','futures-preview.html','risk-preview.html']:
            src=(ROOT/name).read_text()
            nav=re.search(r'<nav class="view-tabs".*?</nav>',src,re.S).group()
            self.assertEqual(nav.count('class="view-tab"'),4)
            self.assertNotIn('etf-flows.html',nav)
            self.assertIn('<span>申赎两融</span>',nav)
    def test_requested_sections_removed_from_page(self):
        for page in ['index.html','margin.html']:
            s=(ROOT/page).read_text()
            for token in ['holdAnnualTable','holdInterimTable','flowTable','etf_official_holdings.json','function renderHolds','function renderTable','<h2>明细</h2>']:
                self.assertNotIn(token,s)
        self.assertIn('id="tb"',(ROOT/'index.html').read_text())
    def test_reading_order_and_separate_periods(self):
        s=(ROOT/'margin.html').read_text()
        self.assertLess(s.index('id="shareFlows"'),s.index('id="marginSection"'))
        self.assertIn('<h1>申赎两融</h1>',s)
        self.assertIn('id="flowStatus"',s);self.assertIn('id="st"',s)
        self.assertNotIn('id="shareFlows"',(ROOT/'index.html').read_text())
    def test_share_controls_and_isolated_state(self):
        s=(ROOT/'margin.html').read_text()
        self.assertIn('window.etfFlows={setSpan,drawOne,refresh:refreshFlows}',s)
        self.assertIn('flow_code',s);self.assertIn('flow_window',s)
        self.assertIn('data/etf_share_flows.json?t=',s)
        self.assertIn('connectNulls:false',s)
        self.assertIn("[data-flow-window]",s)
    def test_legacy_link_is_redirect_and_parses(self):
        s=(ROOT/'etf-flows.html').read_text()
        self.assertIn("new URL('margin.html'",s)
        self.assertIn("target.hash='shareFlows'",s)
        for script in re.findall(r'<script>(.*?)</script>',s,re.S):
            res=subprocess.run(['node','--check','-'],input=script,text=True,capture_output=True)
            self.assertEqual(res.returncode,0,res.stderr)
    def test_live_financial_logic_unchanged(self):
        s=(ROOT/'index.html').read_text()
        old=subprocess.run(['git','show','73752ba:index.html'],cwd=ROOT,capture_output=True,text=True,check=True).stdout
        a='const E=[["上证50"';b='// State can change at lunch/close without a new market snapshot.'
        self.assertEqual(s[s.index(a):s.index(b)],old[old.index(a):old.index(b)])
    def test_margin_financial_logic_unchanged(self):
        s=(ROOT/'margin.html').read_text()
        old=subprocess.run(['git','show','ee6653c:margin.html'],cwd=ROOT,capture_output=True,text=True,check=True).stdout
        a='let marginData=null';b="addEventListener('resize',()=>marginChart&&marginChart.resize());refreshMargin();"
        self.assertEqual(s[s.index(a):s.index(b)],old[old.index(a):old.index(b)])
    def test_shared_styles_scope_flow_chart(self):
        css=(ROOT/'dashboard-shell.css').read_text()
        self.assertIn('.funds-flow-grid',css)
        self.assertIn('[data-ui-view="margin"] .ch.funds-flow-chart',css)
        self.assertIn('var(--type-sec-title)',css)
if __name__=='__main__':unittest.main()
