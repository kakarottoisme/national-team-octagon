"""Tests for preview-tools.global_sources.

Run from repo root:
    python3 -m pytest preview-tools/test_global_sources.py -v
or:
    python3 preview-tools/test_global_sources.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

# Allow running as a script: add the preview-tools/ dir itself to sys.path
# (the dir name contains a hyphen so it cannot be imported as a package).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import global_sources as gs  # noqa: E402


class TestPayloadPolicy(unittest.TestCase):
    def setUp(self):
        # Build a payload WITHOUT network by passing a fake treasury response
        self.fake_treasury = {
            "ok": True,
            "as_of": "2026-11-03",           # ISO normalized
            "as_of_raw": "11/03/2026",       # original CSV format
            "rates": {"2 Yr": 4.61, "10 Yr": 4.95, "30 Yr": 5.32},
            "collected_at": "2026-11-04T04:00:00+00:00",
            "source_url": gs.TREASURY_NOMINAL_CSV_URL,
        }
        self.payload = gs.build_payload(self.fake_treasury)

    def test_metric_count_covers_28plus7(self):
        ids = [m["id"] for m in self.payload["metrics"]]
        # We expect at least the original 28 main + 7 slow, plus splits for VXTLT/real-curve
        self.assertGreaterEqual(len(ids), 35)
        self.assertEqual(len(ids), len(set(ids)), "duplicate metric ids")

    def test_no_ice_values_replicated(self):
        """B1_hy_oas / B2_ig_oas / A1_move must have value=None."""
        by_id = {m["id"]: m for m in self.payload["metrics"]}
        for restricted_id in ("B1_hy_oas", "B2_ig_oas", "A1_move"):
            self.assertIsNone(by_id[restricted_id]["value"],
                              f"{restricted_id} must not carry a replicated value")
            self.assertIn(by_id[restricted_id]["status"],
                          ("restricted", "link_only"))

    def test_connected_source_is_government_url(self):
        """The only connected metric must point to home.treasury.gov."""
        connected = [m for m in self.payload["metrics"] if m["status"] == "connected"]
        self.assertEqual(len(connected), 1)
        m = connected[0]
        self.assertEqual(m["id"], "A3_yield_curve_nominal")
        self.assertIn("home.treasury.gov", m["source_url"])
        self.assertIsNotNone(m["value"])
        self.assertIsNotNone(m["as_of"])

    def test_connected_as_of_is_iso_date(self):
        """as_of must be YYYY-MM-DD, not MM/DD/YYYY."""
        a3 = next(m for m in self.payload["metrics"] if m["id"] == "A3_yield_curve_nominal")
        self.assertEqual(a3["as_of"], "2026-11-03")
        self.assertEqual(a3["as_of_raw"], "11/03/2026")
        # ISO format length and dashes
        self.assertEqual(len(a3["as_of"]), 10)
        self.assertEqual(a3["as_of"][4], "-")
        self.assertEqual(a3["as_of"][7], "-")

    def test_no_l0_l4_or_trading_language(self):
        """Payload must not carry level/position/threshold fields from the old page."""
        blob = json.dumps(self.payload, ensure_ascii=False)
        for forbidden in ("L0", "L1", "L2", "L3", "L4", "Beta", "仓位", "止盈", "对冲比例"):
            self.assertNotIn(forbidden, blob,
                             f"forbidden trading language leaked: {forbidden}")

    def test_metric_has_required_fields(self):
        required = ("id", "name", "layer", "unit", "status", "value", "as_of",
                    "as_of_raw", "collected_at", "stale", "last_attempt_at",
                    "source_url", "link_url", "license", "restricted_reason")
        for m in self.payload["metrics"]:
            for k in required:
                self.assertIn(k, m, f"metric {m.get('id')} missing {k}")

    def test_missing_metrics_have_null_value(self):
        for m in self.payload["metrics"]:
            if m["status"] == "missing":
                self.assertIsNone(m["value"])


class TestISODateNormalization(unittest.TestCase):
    def test_mm_dd_yyyy_to_iso(self):
        self.assertEqual(gs._normalize_iso_date("11/03/2026"), "2026-11-03")
        self.assertEqual(gs._normalize_iso_date("01/02/2026"), "2026-01-02")

    def test_already_iso_passes_through(self):
        self.assertEqual(gs._normalize_iso_date("2026-11-03"), "2026-11-03")

    def test_blank_returns_blank(self):
        self.assertIsNone(gs._normalize_iso_date(""))
        self.assertIsNone(gs._normalize_iso_date(None))


class TestStaleCache(unittest.TestCase):
    """Success-then-failure: last licensed snapshot must be preserved with stale=true."""

    def _fake_good(self):
        return {
            "ok": True,
            "as_of": "2026-11-03",
            "as_of_raw": "11/03/2026",
            "rates": {"2 Yr": 4.61, "10 Yr": 4.95, "30 Yr": 5.32},
            "collected_at": "2026-11-04T04:00:00+00:00",
        }

    def test_stale_cache_preserves_value_and_as_of(self):
        # First: build a "good" payload (simulating a prior successful run)
        good_payload = gs.build_payload(self._fake_good())
        # Now simulate next run: network fails, but we pass the cached snapshot
        bad_fetch = {"ok": False, "error": "network: timeout", "as_of": None, "as_of_raw": None}
        cached = {
            "value": good_payload["metrics"][3]["value"],  # A3 row
            "as_of": good_payload["metrics"][3]["as_of"],
            "as_of_raw": good_payload["metrics"][3].get("as_of_raw"),
            "collected_at": good_payload["metrics"][3]["collected_at"],
        }
        stale_payload = gs.build_payload(bad_fetch, cached_treasury=cached)
        a3 = next(m for m in stale_payload["metrics"] if m["id"] == "A3_yield_curve_nominal")
        # Value preserved
        self.assertEqual(a3["value"], self._fake_good()["rates"])
        # Original as_of/collected_at preserved (NOT overwritten by today)
        self.assertEqual(a3["as_of"], "2026-11-03")
        self.assertEqual(a3["collected_at"], "2026-11-04T04:00:00+00:00")
        # Stale flag set
        self.assertTrue(a3["stale"])
        self.assertEqual(a3["status"], "connected_stale")
        self.assertIsNotNone(a3["last_attempt_at"])
        # fetch_status marks degraded
        self.assertTrue(stale_payload["fetch_status"]["treasury_nominal"]["degraded"])

    def test_failure_without_cache_clears_value(self):
        bad_fetch = {"ok": False, "error": "network: timeout"}
        payload = gs.build_payload(bad_fetch, cached_treasury=None)
        a3 = next(m for m in payload["metrics"] if m["id"] == "A3_yield_curve_nominal")
        self.assertIsNone(a3["value"])
        # Must NOT claim connected — must be honest failure
        self.assertEqual(a3["status"], "failed_no_cache")
        self.assertFalse(a3["stale"])


class TestUpdateGlobal(unittest.TestCase):
    def test_update_writes_file_and_returns_status(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "global.json")
            # Call real update_global (will hit Treasury; acceptable for this test)
            status = gs.update_global(out)
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["schema_version"], "2.0-safe")
            self.assertEqual(status["metrics_total"], len(payload["metrics"]))
            for k in ("connected", "connected_stale", "restricted", "link_only",
                      "pending", "missing", "state"):
                self.assertIn(k, status)

    def test_network_failure_no_cache_degrades_to_null(self):
        """If Treasury URL is unreachable AND no prior file exists, value=null, state=failed_no_cache."""
        original = gs.TREASURY_NOMINAL_CSV_URL
        try:
            gs.TREASURY_NOMINAL_CSV_URL = "https://nonexistent.invalid.invalid/x.csv"
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "global.json")
                status = gs.update_global(out)
                self.assertFalse(status["ok"])
                self.assertEqual(status["state"], "failed_no_cache")
                self.assertTrue(os.path.exists(out))
                with open(out, encoding="utf-8") as f:
                    payload = json.load(f)
                a3 = next(m for m in payload["metrics"] if m["id"] == "A3_yield_curve_nominal")
                self.assertIsNone(a3["value"])
                self.assertFalse(payload["fetch_status"]["treasury_nominal"]["ok"])
        finally:
            gs.TREASURY_NOMINAL_CSV_URL = original

    def test_success_then_failure_preserves_snapshot(self):
        """First run succeeds, second run fails -> stale cache preserved."""
        original = gs.TREASURY_NOMINAL_CSV_URL
        try:
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "global.json")
                # Run 1: real fetch (assume network OK in test env)
                status1 = gs.update_global(out)
                with open(out, encoding="utf-8") as f:
                    p1 = json.load(f)
                a3_1 = next(m for m in p1["metrics"] if m["id"] == "A3_yield_curve_nominal")
                # Run 2: break URL, should preserve p1 snapshot as stale
                gs.TREASURY_NOMINAL_CSV_URL = "https://nonexistent.invalid.invalid/x.csv"
                status2 = gs.update_global(out)
                self.assertFalse(status2["ok"])
                self.assertEqual(status2["state"], "degraded_stale_cached")
                with open(out, encoding="utf-8") as f:
                    p2 = json.load(f)
                a3_2 = next(m for m in p2["metrics"] if m["id"] == "A3_yield_curve_nominal")
                if status1["ok"]:
                    # If run 1 actually succeeded, value must be preserved
                    self.assertEqual(a3_2["value"], a3_1["value"])
                    self.assertEqual(a3_2["as_of"], a3_1["as_of"])
                    self.assertTrue(a3_2["stale"])
                    self.assertEqual(a3_2["status"], "connected_stale")
        finally:
            gs.TREASURY_NOMINAL_CSV_URL = original

    def test_two_consecutive_failures_keep_snapshot(self):
        """Success -> failure -> failure again: value/as_of/collected_at unchanged."""
        original = gs.TREASURY_NOMINAL_CSV_URL
        try:
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "global.json")
                # Run 1: success
                s1 = gs.update_global(out)
                if not s1["ok"]:
                    self.skipTest("live Treasury fetch unavailable in test env")
                with open(out, encoding="utf-8") as f:
                    a1 = next(m for m in json.load(f)["metrics"] if m["id"] == "A3_yield_curve_nominal")
                # Break URL
                gs.TREASURY_NOMINAL_CSV_URL = "https://nonexistent.invalid.invalid/x.csv"
                # Run 2: failure -> stale
                s2 = gs.update_global(out)
                self.assertEqual(s2["state"], "degraded_stale_cached")
                with open(out, encoding="utf-8") as f:
                    a2 = next(m for m in json.load(f)["metrics"] if m["id"] == "A3_yield_curve_nominal")
                self.assertEqual(a2["status"], "connected_stale")
                self.assertEqual(a2["value"], a1["value"])
                self.assertEqual(a2["as_of"], a1["as_of"])
                self.assertEqual(a2["collected_at"], a1["collected_at"])
                # Run 3: still failing — must NOT clear value
                s3 = gs.update_global(out)
                self.assertEqual(s3["state"], "degraded_stale_cached")
                with open(out, encoding="utf-8") as f:
                    a3 = next(m for m in json.load(f)["metrics"] if m["id"] == "A3_yield_curve_nominal")
                self.assertEqual(a3["status"], "connected_stale")
                self.assertEqual(a3["value"], a1["value"], "value must survive second consecutive failure")
                self.assertEqual(a3["as_of"], a1["as_of"])
                self.assertEqual(a3["collected_at"], a1["collected_at"])
        finally:
            gs.TREASURY_NOMINAL_CSV_URL = original

    def test_cached_snapshot_rejects_future_date(self):
        """A cached row with future as_of must be rejected (tampered/malformed)."""
        tampered = {
            "schema_version": "2.0-safe",
            "metrics": [{
                "id": "A3_yield_curve_nominal",
                "status": "connected",
                "value": {"2 Yr": 4.61},
                "as_of": "2099-01-01",  # future
                "as_of_raw": "01/01/2099",
                "collected_at": "2026-11-04T00:00:00+00:00",
                "source_url": gs.TREASURY_NOMINAL_CSV_URL,
                "license": "US Government work — public domain.",
            }],
        }
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "global.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(tampered, f)
            self.assertIsNone(gs._read_existing_snapshot(out))

    def test_cached_snapshot_rejects_non_numeric_value(self):
        tampered = {
            "schema_version": "2.0-safe",
            "metrics": [{
                "id": "A3_yield_curve_nominal",
                "status": "connected",
                "value": {"2 Yr": "N/A"},  # malformed
                "as_of": "2026-11-03",
                "collected_at": "2026-11-04T00:00:00+00:00",
                "source_url": gs.TREASURY_NOMINAL_CSV_URL,
                "license": "US Government work — public domain.",
            }],
        }
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "global.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(tampered, f)
            self.assertIsNone(gs._read_existing_snapshot(out))

    def test_cached_snapshot_rejects_legacy_fred_row(self):
        """Old v3 file might have an ICE/FRED row masquerading as A3 — reject."""
        legacy = {
            "schema_version": "1.0-unsafe",
            "metrics": [{
                "id": "A3_yield_curve_nominal",
                "status": "connected",
                "value": {"2 Yr": 4.61},
                "as_of": "2026-11-03",
                "collected_at": "2026-11-04T00:00:00+00:00",
                "source_url": "https://fred.stlouisfed.org/series/T10Y2Y",  # wrong source
                "license": None,
            }],
        }
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "global.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(legacy, f)
            self.assertIsNone(gs._read_existing_snapshot(out))


class TestImportSideEffects(unittest.TestCase):
    def test_import_does_not_fetch(self):
        """Re-importing the module must not create files or network calls."""
        import importlib
        importlib.reload(gs)
        self.assertFalse(hasattr(gs, "_last_written"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
