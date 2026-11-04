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


if __name__ == "__main__":
    unittest.main()
