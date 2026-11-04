#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""public_macro 隔离测试: 用假fetcher,不触网,不写repo数据。"""
import os
import sys
import unittest
import datetime as dt

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "preview-tools"))
import public_macro as pm  # noqa


class FakeFetcher:
    """返回固定字节;按URL关键字分发。"""
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def __call__(self, url, *a, **k):
        for key, val in self.mapping.items():
            if key in url:
                return 200, val.encode()
        return 404, b"not found"


class TestPublicMacro(unittest.TestCase):
    def _run(self, mapping):
        now = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        return pm.collect_public_macro(fetcher=FakeFetcher(mapping), now=now, years=(2026,))

    def test_nominal_real_bei_same_date(self):
        nom = 'Date,"10 Yr"\n11/03/2026,4.95\n11/02/2026,4.90\n'
        real = 'Date,"10 YR"\n11/03/2026,2.65\n11/02/2026,2.60\n'
        out = self._run({"daily_treasury_yield_curve": nom, "daily_treasury_real_yield_curve": real})
        by = {m["id"]: m for m in out["metrics"]}
        self.assertEqual(by["ust_10y_nominal"]["value"], 4.95)
        self.assertEqual(by["ust_10y_real"]["value"], 2.65)
        bei = by["bei_10y"]
        self.assertEqual(bei["value"], 2.30)  # 同日交集 4.95-2.65
        self.assertEqual(len(bei["history"]), 2)

    def test_future_date_filtered(self):
        nom = 'Date,"10 Yr"\n11/03/2026,4.95\n12/30/2099,9.99\n'
        out = self._run({"daily_treasury_yield_curve": nom,
                         "daily_treasury_real_yield_curve": 'Date,"10 YR"\n11/03/2026,2.65\n'})
        by = {m["id"]: m for m in out["metrics"]}
        self.assertNotIn("2099-12-30", [h["date"] for h in by["ust_10y_nominal"]["history"]])

    def test_pending_sources_not_faked(self):
        out = self._run({"daily_treasury_yield_curve": 'Date,"10 Yr"\n11/03/2026,4.95\n',
                         "daily_treasury_real_yield_curve": 'Date,"10 YR"\n11/03/2026,2.65\n'})
        by = {m["id"]: m for m in out["metrics"]}
        for pid in ("iorb", "sofr_iorb_spread", "nfci", "sahm"):
            self.assertIsNone(by[pid]["value"])
            self.assertEqual(by[pid]["history"], [])

    def test_schema_fields(self):
        out = self._run({"daily_treasury_yield_curve": 'Date,"10 Yr"\n11/03/2026,4.95\n',
                         "daily_treasury_real_yield_curve": 'Date,"10 YR"\n11/03/2026,2.65\n'})
        m = out["metrics"][0]
        for k in ("id", "name", "unit", "value", "as_of", "history",
                  "source_url", "license_url", "frequency", "collected_at"):
            self.assertIn(k, m)

    def test_sofr_json_parse(self):
        import json
        payload = json.dumps({"refRates": [
            {"effectiveDate": "2026-11-02", "percentRate": 3.95},
            {"effectiveDate": "2026-11-01", "percentRate": 3.90}]})
        out = self._run({"daily_treasury_yield_curve": 'Date,"10 Yr"\n11/03/2026,4.95\n',
                         "daily_treasury_real_yield_curve": 'Date,"10 YR"\n11/03/2026,2.65\n',
                         "sofr/search": payload})
        by = {m["id"]: m for m in out["metrics"]}
        self.assertEqual(by["sofr"]["value"], 3.95)

    def test_wrong_series_rejected_html_not_csv(self):
        # FRED 拦截/错误页返回 200 但内容是 HTML,绝不当 CSV
        html = "<!DOCTYPE html><html><head><title>Error</title></head><body>bad</body></html>"
        out = self._run({"daily_treasury_yield_curve": 'Date,"10 Yr"\n11/03/2026,4.95\n',
                         "daily_treasury_real_yield_curve": 'Date,"10 YR"\n11/03/2026,2.65\n',
                         "fredgraph.csv?id=SAHMREALTIME": html,
                         "fredgraph.csv?id=IORB": "observation_date,IORB\n2026-11-02,3.90\n",
                         "fredgraph.csv?id=NFCI": "observation_date,NFCI\n2026-10-23,-0.487\n"})
        by = {m["id"]: m for m in out["metrics"]}
        self.assertIsNone(by["sahm"]["value"])
        self.assertEqual(by["sahm"]["unit"], "pp")
        self.assertEqual(out["fetch_status"]["sahm"]["state"], "pending")

    def test_acm_authors_and_notice_correct(self):
        # 防错: ACM作者=Adrian,Crump,Moench; 不得出现错误作者名
        self.assertIn("Adrian, Crump, Moench", pm.ACM_COPYRIGHT)
        self.assertNotIn("Filardo", pm.ACM_COPYRIGHT)
        self.assertIn("Federal Reserve Bank of New York", pm.ACM_COPYRIGHT)
        # NYFed英文notice必须literal前缀(源给定,非自改写)
        self.assertTrue(pm.NYFED_NOTICE_EN.startswith("The SOFR is subject to the Terms of Use"))
        self.assertIn("has no liability for your use", pm.NYFED_NOTICE_EN)
        # ACM不套用SOFR专属literal notice;仅copyright+terms
        self.assertNotIn("SOFR is subject", pm.ACM_COPYRIGHT)

    def test_full_curve_metrics(self):
        nom = ('Date,"10 Yr","2 Yr","30 Yr"\n'
               '11/03/2026,4.95,4.61,5.32\n11/02/2026,4.90,4.59,5.30\n')
        real = 'Date,"10 YR","5 YR","30 YR"\n11/03/2026,2.65,2.35,3.09\n11/02/2026,2.62,2.31,3.06\n'
        out = self._run({"daily_treasury_yield_curve": nom,
                         "daily_treasury_real_yield_curve": real})
        by = {m["id"]: m for m in out["metrics"]}
        for cid in ("ust_curve_nominal", "ust_curve_real"):
            self.assertIn(cid, by)
            self.assertIsInstance(by[cid]["value"], dict)
            self.assertEqual(len(by[cid]["history"]), 2)
            self.assertIsInstance(by[cid]["history"][0]["value"], dict)
        # 名义曲线含多期限
        self.assertIn("10 Yr", by["ust_curve_nominal"]["value"])
        self.assertIn("30 Yr", by["ust_curve_nominal"]["value"])
        self.assertEqual(by["ust_curve_nominal"]["value"]["10 Yr"], 4.95)
        # 实际曲线含 TIPS 期限
        self.assertIn("10 YR", by["ust_curve_real"]["value"])
        self.assertEqual(by["ust_curve_real"]["value"]["10 YR"], 2.65)

    def test_curve_no_extra_network(self):
        # 曲线与10Y标量来自同一CSV解析,不额外请求
        nom = 'Date,"10 Yr","2 Yr"\n11/03/2026,4.95,4.61\n'
        real = 'Date,"10 YR"\n11/03/2026,2.65\n'
        calls = []
        class Rec(FakeFetcher):
            def __call__(self, url, *a, **k):
                calls.append(url)
                return super().__call__(url, *a, **k)
        pm.collect_public_macro(fetcher=Rec({"daily_treasury_yield_curve": nom,
                                             "daily_treasury_real_yield_curve": real}),
                                now=dt.datetime(2026, 11, 4, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        # 同一URL不被反复请求(曲线不引发额外下载)
        self.assertLessEqual(calls.count(calls[0]), 1)

    def test_sahm_realtime_parses(self):
        csv = "observation_date,SAHMREALTIME\n2026-09-01,0.30\n2026-10-01,0.45\n"
        out = self._run({"daily_treasury_yield_curve": 'Date,"10 Yr"\n11/03/2026,4.95\n',
                         "daily_treasury_real_yield_curve": 'Date,"10 YR"\n11/03/2026,2.65\n',
                         "fredgraph.csv?id=SAHMREALTIME": csv})
        by = {m["id"]: m for m in out["metrics"]}
        self.assertEqual(by["sahm"]["value"], 0.45)
        self.assertEqual(by["sahm"]["unit"], "pp")
        self.assertIn("Claudia Sahm", by["sahm"]["attribution"])
        self.assertIn("SAHMREALTIME", by["sahm"]["source_url"])


if __name__ == "__main__":
    unittest.main()
