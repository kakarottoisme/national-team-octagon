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
        styles = [re.search(r"/\* ===== Unified dashboard UI v2:.*?/\* ===== End unified dashboard UI ===== \*/", text, re.S).group() for text in self.sources.values()]
        self.assertEqual(len(set(styles)), 1)
        self.assertIn("height:44px!important", styles[0])
        self.assertIn("repeat(4,minmax(0,1fr))", styles[0])
        self.assertNotIn("auto-fit", styles[0])

    def test_brand_and_favicons_are_shared(self):
        self.assertEqual(len({repr(p.icons) for p in self.parsed.values()}), 1)
        for text in self.sources.values():
            self.assertIn('src="favicon.svg?v=ui-2" width="38" height="38"', text)
            self.assertIn('data-ui-version="2"', text)
            self.assertNotIn('rel="icon" type="image/png" sizes="64x64" href="data:', text)

    def test_no_duplicate_ids_or_accordion(self):
        for name, p in self.parsed.items():
            with self.subTest(page=name):
                self.assertEqual(len(p.ids), len(set(p.ids)))
                self.assertEqual(p.details, 0)
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

    def test_funds_indicator_dom_order(self):
        t = self.sources["index.html"]
        kpis = re.search(r'<div class="kpis">(.*?)\n</div>', t, re.S).group(1)
        labels = re.findall(r'<div class="lb">(.*?)</div>', kpis)
        self.assertEqual(labels, ["大额分钟成交额", "主动净买入（推算）", "同时点倍数", "已归档天数"])

    def test_data_paths_and_financial_formulas_preserved(self):
        funds = self.sources["index.html"]
        for token in ['"./data/live.json?t="', '"./data/history.json"', '"./data/minutes/"', "preview-data/turnover-baseline.json", "tot/dens.reduce((a,b)=>a+b,0)", "amt/den", "v=>v>1", "setInterval(()=>{if(view==\"live\")refresh()},30000)"]:
            self.assertIn(token, funds)
        futures = self.sources["futures-preview.html"]
        for token in ["preview-data/official/futures/", "data/ic_strategy.json?t=", "rec.net_short", "rec.long_observed", "rec.short_observed", "connectNulls:false"]:
            self.assertIn(token, futures)
        self.assertIn("data/margin_balance.json?", self.sources["margin.html"])
        risk = self.sources["risk-preview.html"]
        self.assertIn("preview-data/global.json?t=", risk)
        self.assertIn("m.status==='connected'||m.status==='connected_stale'", risk)
        self.assertIn("m.status==='restricted'?'受限不转录':'—'", risk)

    def test_inline_javascript_parses(self):
        for name, p in self.parsed.items():
            for i, script in enumerate(p.scripts):
                with self.subTest(page=name, script=i):
                    result = subprocess.run(["node", "--check", "-"], input=script, text=True, capture_output=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
