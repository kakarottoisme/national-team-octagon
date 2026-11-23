"""Front-end contract tests. Run: python3 -m unittest discover -s tests -p test_dashboard_ui.py

These tests do not fetch market data or modify runtime state. They guard the
four published pages against visual/interaction drift and accidental formula changes.
"""
from pathlib import Path
import re
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("index.html", "margin.html", "futures-preview.html", "risk-preview.html")


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.dialogs = []
        self.icons = []
        self.scripts = []
        self.current_script = None
        self.details = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if a.get("role") == "dialog":
            self.dialogs.append(a)
        if tag == "link" and a.get("rel") in ("icon", "apple-touch-icon"):
            self.icons.append(a)
        if tag == "details":
            self.details += 1
        if tag == "script" and "src" not in a:
            self.current_script = []

    def handle_data(self, text):
        if self.current_script is not None:
            self.current_script.append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.current_script is not None:
            self.scripts.append("".join(self.current_script))
            self.current_script = None


class DashboardContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {name: (ROOT / name).read_text(encoding="utf-8") for name in PAGES}
        cls.parsed = {}
        for name, text in cls.sources.items():
            p = Collector()
            p.feed(text)
            cls.parsed[name] = p

    def test_common_style_is_identical(self):
        styles = [re.search(r"/\* ===== Reviewed shared UI:.*?/\* ===== End reviewed shared UI ===== \*/", text, re.S).group() for text in self.sources.values()]
        self.assertEqual(len(set(styles)), 1)
        self.assertIn("min-height:44px", styles[0])
        self.assertIn("repeat(4,minmax(0,1fr))", styles[0])
        self.assertNotIn("auto-fit", styles[0])

    def test_brand_and_favicons_are_shared(self):
        self.assertEqual(len({repr(p.icons) for p in self.parsed.values()}), 1)
        for text in self.sources.values():
            self.assertIn('src="favicon.svg?v=ui-2" width="38" height="38"', text)
            self.assertIn('data-ui-version="3"', text)
            self.assertNotIn('rel="icon" type="image/png" sizes="64x64" href="data:', text)

    def test_no_duplicate_ids_or_accordion(self):
        for name, p in self.parsed.items():
            with self.subTest(page=name):
                self.assertEqual(len(p.ids), len(set(p.ids)))
                self.assertEqual(p.details, 0 if name != "futures-preview.html" else 0)
                self.assertGreater(len(p.dialogs), 0)
                for d in p.dialogs:
                    self.assertEqual(d.get("aria-modal"), "true")
                    self.assertIn(d["aria-labelledby"], p.ids)

    def test_dialog_and_navigation_behavior_shared(self):
        blocks = []
        for text in self.sources.values():
            blocks.append(re.search(r"/\* ===== Unified dashboard dialogs.*?/\* ===== End unified dashboard UI behavior ===== \*/", text, re.S).group())
            self.assertIn("if(event.key==='Escape')", text)
            self.assertIn("el.inert=true", text)
            self.assertIn("state.focus.focus()", text)
            for target in PAGES:
                self.assertIn('href="' + target + '"', text)
        self.assertEqual(len(set(blocks)), 1)

    def test_futures_bc_rules_use_matching_chinese_actions(self):
        text = self.sources["futures-preview.html"]
        self.assertIn('&gt;MA250×1.01 加 2 手<br>&lt;MA250×0.99 减 2 手', text)
        self.assertIn('≥10% 加 2 手<br>&lt;3% 减 2 手<br>3% ≤ 贴水 <10% 不加不减', text)
        self.assertNotIn('C = +2 手', text)
        self.assertNotIn('C = −2 手', text)
        self.assertIn('低于 MA250×0.99 减 2 手（撤出趋势加仓）', text)
        self.assertIn('不低于 10% 加 2 手，低于 3% 减 2 手，介于其间不加不减', text)

    def test_funds_indicator_dom_order(self):
        t = self.sources["index.html"]
        kpis = re.search(r'<div class="kpis">(.*?)\n</div>', t, re.S).group(1)
        labels = re.findall(r'<div class="lb">(.*?)</div>', kpis)
        self.assertEqual(labels, ["大额分钟成交额", "主动净买入（推算）", "同时点倍数", "已归档天数"])

    def test_data_paths_and_financial_formulas_preserved(self):
        funds = self.sources["index.html"]
        for token in ['"./data/live.json?t="', '"./data/history.json?t="', "'./data/minutes/'", "preview-data/turnover-baseline.json", "alignedTotal/dens.reduce((a,b)=>a+b,0)", "amt/den", "v=>v>1", "setInterval(()=>{if(view==='live'&&!document.hidden)refresh()},60000)"]:
            self.assertIn(token, funds)
        futures = self.sources["futures-preview.html"]
        for token in ["preview-data/official/futures/", "data/ic_strategy.json?t=", "rec.net_short", "rec.long_observed", "rec.short_observed", "connectNulls:false"]:
            self.assertIn(token, futures)
        self.assertIn("data/margin_balance.json?", self.sources["margin.html"])
        risk = self.sources["risk-preview.html"]
        self.assertIn("preview-data/global.json?t=", risk)
        self.assertIn("m.status==='connected'||m.status==='connected_stale'", risk)
        self.assertIn("m.status==='restricted'?'受限不转录':'未接入，不能据此判断风险'", risk)

    def test_header_master_is_one_stylesheet(self):
        css = (ROOT / "dashboard-shell.css").read_text(encoding="utf-8")
        self.assertIn("--shell-head-h:120px", css)
        self.assertIn("--shell-head-h:68px", css)
        self.assertIn("--shell-icon:38px", css)
        self.assertIn("--shell-title-size:18px", css)
        self.assertIn("--shell-title-lh:26px", css)
        self.assertIn("text-overflow:ellipsis", css)
        crumb = re.search(r"header\.top \.crumb\{[^}]+\}", css).group()
        self.assertNotIn("ellipsis", crumb)
        self.assertNotIn("nowrap", crumb)
        expected = {
            "index.html": "宽基 ETF · 盘中活跃度与主动买卖方向",
            "margin.html": "ETF 申赎 · 沪深两融 · 日度数据",
            "futures-preview.html": "中信期货(代客) · CFFEX 前20会员",
            "risk-preview.html": "利率条件与融资成本 · 读最新值与历史",
            "etf-flows.html": "宽基 ETF · 全市场申赎，非国家队专属",
        }
        for name, text in self.sources.items():
            with self.subTest(page=name):
                self.assertIn('href="dashboard-shell.css"', text)
                tail = text.split("/* ===== End reviewed shared UI ===== */", 1)[1]
                self.assertNotIn(".ui-page .top .shell-head{", tail)
                self.assertNotIn(".ui-page .crumb{font-size:13px}", tail)
                header = re.search(r"<header\b[^>]*>(.*?)</header>", text, re.S).group(1)
                self.assertIn("<h1>", header)
                self.assertIn('title="' + expected[name] + '"', header)
                self.assertIn(">" + expected[name] + "<", header)
        self.assertNotIn("不评级不构成建议", self.sources["risk-preview.html"])
        self.assertNotIn("这是用户自己的回测规则，不是投资建议，也不保证将来重复。", self.sources["futures-preview.html"])
        self.assertNotIn("schema 2.0-safe：", self.sources["risk-preview.html"])
        self.assertIn('grid-template-areas:"title actions" "meta actions"', css)
        self.assertIn("--type-sec-title:16px", css)
        self.assertIn("--type-sec-meta:13px", css)
        self.assertIn("--type-ctrl-h:36px", css)

    def test_funds_history_collapses_to_latest_thirty(self):
        source = self.sources['index.html']
        self.assertIn('<h2>今日分时走势</h2>', source)
        self.assertNotIn('历史只含已归档日期（data/history.json），不补造。', source)
        code = source.split('let historyExpanded=false,historyCurrentDate=null;', 1)[1].split('let TODAY=null;', 1)[0]
        stub = r'''
const assert=require('assert');
const elements={tb:{rows:[],set innerHTML(v){this.rows=[]},appendChild(r){this.rows.push(r)}},historyMore:{hidden:true},historyToggle:{attrs:{},setAttribute(k,v){this.attrs[k]=v},scrollIntoView(){}}};
const document={getElementById:id=>elements[id],createElement:()=>({})};
let H={},historyExpanded=false,historyCurrentDate=null,sel={value:'live'},selected=null;
const todayStr=()=>null,onSel=()=>{selected=sel.value},kpi=()=>{};
for(let i=1;i<=40;i++)H[new Date(Date.UTC(2026,0,i)).toISOString().slice(0,10)]={'合计':i,'上证收盘':3000+i,'上证涨跌幅':0};
'''
        checks = r'''
const original=JSON.stringify(H),dates=Object.keys(H).sort().reverse(),cur=dates[2];
table(cur);assert.equal(elements.tb.rows.length,30);assert(elements.tb.rows[0].innerHTML.includes(dates[0]));assert.equal(elements.tb.rows[2].className,'sel');assert.equal(elements.historyToggle.attrs['aria-expanded'],'false');assert.equal(elements.historyMore.hidden,false);
elements.historyToggle.onclick();assert.equal(elements.tb.rows.length,40);assert.equal(elements.historyToggle.textContent,'收起');assert.equal(elements.historyToggle.attrs['aria-expanded'],'true');table(cur);assert.equal(elements.tb.rows.length,40);
elements.tb.rows[35].onclick();assert.equal(selected,dates[35]);
elements.historyToggle.onclick();assert.equal(elements.tb.rows.length,30);assert.equal(elements.historyToggle.textContent,'展开全部');assert.equal(JSON.stringify(H),original);
H=Object.fromEntries(Object.entries(H).slice(0,5));table(cur);assert.equal(elements.tb.rows.length,5);assert.equal(elements.historyMore.hidden,true);
'''
        result = subprocess.run(['node','-'],input=stub+code+checks,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_cycle_navigation_has_all_shared_icons(self):
        cycle = (ROOT / 'market-cycle.html').read_text(encoding='utf-8')
        nav = re.search(r'<nav class="view-tabs"[^>]*>(.*?)</nav>', cycle, re.S).group(1)
        links = re.findall(r'<a\b[^>]*>(.*?)</a>', nav, re.S)
        self.assertEqual(len(links), 5)
        for link in links:
            self.assertEqual(link.count('<svg '), 1)
            self.assertIn('<span>', link)
        original_nav = re.search(r'<nav class="view-tabs"[^>]*>(.*?)</nav>', self.sources['risk-preview.html'], re.S).group(1)
        self.assertEqual(re.findall(r'<svg .*?</svg>', nav, re.S)[:4], re.findall(r'<svg .*?</svg>', original_nav, re.S))
        cycle_icon = re.findall(r'<svg .*?</svg>', nav, re.S)[4]
        runtime = (ROOT / 'dashboard-tabs.js').read_text(encoding='utf-8')
        self.assertIn(cycle_icon, runtime)
        self.assertIn('stroke-linejoin="round"', cycle_icon)
        for name in (*PAGES, 'market-cycle.html'):
            self.assertRegex((ROOT / name).read_text(encoding='utf-8'), r'dashboard-tabs\.js\?v=[A-Za-z0-9_-]+')
        css = (ROOT / 'dashboard-shell.css').read_text(encoding='utf-8')
        self.assertIn('.ui-page .view-tab{gap:6px}', css)
        self.assertIn('.ui-page .view-tab svg{display:none;width:16px;height:16px;flex:none}', css)

    def test_futures_refresh_follows_latest_and_retains_history(self):
        source = self.sources['futures-preview.html']
        code = source.split('function nextObservationDate(', 1)[1].split('async function refreshHistory()', 1)[0]
        stub = "const assert=require('assert');function nextObservationDate(" + code
        checks = r'''
const dates=['2026-11-18','2026-11-19','2026-11-20'];
assert.equal(nextObservationDate(null,null,dates),'2026-11-20');
assert.equal(nextObservationDate('2026-11-19','2026-11-19',dates),'2026-11-20');
assert.equal(nextObservationDate('2026-11-18','2026-11-19',dates),'2026-11-18');
assert.equal(nextObservationDate('2026-11-18',null,dates),'2026-11-18');
assert.equal(nextObservationDate('2099-01-01',null,dates),'2026-11-20');
assert.equal(nextObservationDate(null,null,[]),null);
'''
        result = subprocess.run(['node','-'],input=stub+checks,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('selDate=nextObservationDate(selDate,previousLatest,sortedAll)',source)
        self.assertIn("get('signal'),sel.dataset.latest,dates)",source)
        self.assertIn('sel.dataset.latest=latest.date',source)

    def test_dashboard_preserves_explicit_release_version(self):
        source = (ROOT / 'dashboard.html').read_text(encoding='utf-8')
        code = source.split('const requestedVersion=',1)[1].split('function mount(',1)[0]
        for search, expected in [('?v=ic20-release','ic20-release'),('','tooltip-side-20261123'),('?v=bad%2Fpath','tooltip-side-20261123')]:
            stub = "const assert=require('assert');const location={search:" + repr(search) + "};const requestedVersion=" + code + "assert.equal(version," + repr(expected) + ");"
            result = subprocess.run(['node','-'],input=stub,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
        parsed = Collector()
        parsed.feed(source)
        for script in parsed.scripts:
            result = subprocess.run(['node','--check','-'],input=script,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_futures_timer_visibility_and_busy_gates(self):
        source=self.sources['futures-preview.html']
        code=source.split('/* ===== Futures automatic refresh: active view only, no reload ===== */',1)[1].split('/* ===== End futures automatic refresh ===== */',1)[0]
        stub=r'''
const assert=require('assert');const events={},docEvents={};let tick=null,interval=0,now=2000,calls=0;
const top={},parent={};const window={top,parent,futuresBusy:false,reloadIC(){},refresh(){calls++;return Promise.resolve()}};
const location={origin:'https://example.test'};const document={hidden:false,addEventListener(k,f){docEvents[k]=f}};
const Date={now(){return now}};const addEventListener=(k,f)=>{events[k]=f};
const setInterval=(f,ms)=>{tick=f;interval=ms};const pageNotice=()=>{};
'''
        checks=r'''
assert.equal(interval,60000);tick();assert.equal(calls,0);
events.message({source:{},origin:location.origin,data:{kind:'view-visibility',page:'futures-preview.html',visible:true}});assert.equal(calls,0);
events.message({source:parent,origin:location.origin,data:{kind:'view-visibility',page:'futures-preview.html',visible:true}});assert.equal(calls,1);
now+=60000;tick();assert.equal(calls,2);assert.equal(window.futuresRefreshStats.lastReason,'timer');
window.futuresBusy=true;now+=60000;tick();assert.equal(calls,2);window.futuresBusy=false;
document.hidden=true;now+=60000;tick();assert.equal(calls,2);
document.hidden=false;docEvents.visibilitychange();assert.equal(calls,3);
events.message({source:parent,origin:location.origin,data:{kind:'view-visibility',page:'futures-preview.html',visible:false}});now+=60000;tick();assert.equal(calls,3);
events.message({source:parent,origin:location.origin,data:{kind:'view-visibility',page:'futures-preview.html',visible:true}});assert.equal(calls,4);
'''
        result=subprocess.run(['node','-'],input=stub+code+checks,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        dashboard=(ROOT/'dashboard.html').read_text()
        self.assertIn("kind:'view-visibility'",dashboard)
        self.assertIn('signalVisibility(previous,false)',dashboard)
        self.assertIn('signalVisibility(next,true)',dashboard)
        self.assertIn("addEventListener('visibilitychange'",dashboard)
        self.assertIn('active===name',dashboard)

    def test_inline_javascript_parses(self):
        for name, p in self.parsed.items():
            for i, script in enumerate(p.scripts):
                with self.subTest(page=name, script=i):
                    result = subprocess.run(["node", "--check", "-"], input=script, text=True, capture_output=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
