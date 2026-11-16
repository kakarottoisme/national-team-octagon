#!/usr/bin/env python3
"""IC 收盘信号的纯函数测试，不访问网络。"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ic_strategy as ic


class TestLegs(unittest.TestCase):
    def test_grid_boundaries(self):
        self.assertEqual(ic.leg_a(6500.01), 0)
        self.assertEqual(ic.leg_a(6500), 2)
        self.assertEqual(ic.leg_a(6000.01), 2)
        self.assertEqual(ic.leg_a(6000), 4)
        self.assertEqual(ic.leg_a(5500), 6)
        self.assertEqual(ic.leg_a(5250), 8)
        self.assertEqual(ic.leg_a(5000.01), 8)
        self.assertEqual(ic.leg_a(5000), 10)
        self.assertEqual(ic.leg_a(1000), 10)

    def test_trend_hysteresis(self):
        ma = 1000
        self.assertEqual(ic.next_b(0, ma * 1.01 + 0.01, ma), 2)
        self.assertEqual(ic.next_b(0, ma * 1.01, ma), 0)
        self.assertEqual(ic.next_b(2, ma * 0.99, ma), 2)
        self.assertEqual(ic.next_b(2, ma * 0.99 - 0.01, ma), 0)
        self.assertEqual(ic.next_b(2, 1000, ma), 2)
        self.assertEqual(ic.next_b(0, 1000, ma), 0)

    def test_basis_thresholds(self):
        self.assertEqual(ic.c_from_basis(0.10), 2)
        self.assertEqual(ic.c_from_basis(0.0999), 0)
        self.assertEqual(ic.c_from_basis(0.03), 0)
        self.assertEqual(ic.c_from_basis(0.0299), -2)

    def test_clip(self):
        self.assertEqual(ic.clip_lots(12), 10)
        self.assertEqual(ic.clip_lots(-1), 0)
        self.assertEqual(ic.clip_lots(4), 4)


class TestCalendar(unittest.TestCase):
    def test_listed_on_2026_11_16(self):
        slots = ic.list_slots(dt.date(2026, 11, 16), set())
        got = [(s["code"], s["role"], s["days"]) for s in slots]
        self.assertEqual(got, [
            ("IC2611", "near", 4),
            ("IC2612", "next", 32),
            ("IC2703", "quarter", 123),
            ("IC2706", "far", 214),
        ])
        self.assertEqual(ic.third_friday(2026, 11), dt.date(2026, 11, 20))
        self.assertEqual(ic.prev_trading_day(dt.date(2026, 11, 20), set()), dt.date(2026, 11, 19))

    def test_holiday_postpones_third_friday(self):
        # 2026-06-19 是周五且为端午休市，最后交易日顺延到 6-22。
        holidays = {"2026-06-19"}
        self.assertEqual(ic.last_trade_day(2026, 6, holidays), dt.date(2026, 6, 22))
        self.assertEqual(ic.prev_trading_day(dt.date(2026, 6, 22), holidays), dt.date(2026, 6, 18))


class TestBasis(unittest.TestCase):
    def test_2026_11_16_quotes(self):
        index = 6297.183
        quotes = [
            ("IC2611", 6280.4, 4, 24.32),
            ("IC2612", 6218.4, 32, 14.27),
            ("IC2703", 6075.6, 123, 10.44),
            ("IC2706", 5917.6, 214, 10.28),
        ]
        for _code, px, days, pct in quotes:
            ann = ic.ann_discount(index, px, days)
            self.assertAlmostEqual(ann * 100, pct, places=2)

    def test_2026_10_15_new_near(self):
        days = (dt.date(2026, 11, 20) - dt.date(2026, 10, 15)).days
        ann = ic.ann_discount(6769.251, 6693.0, days)
        self.assertEqual(days, 36)
        self.assertAlmostEqual(ann * 100, 11.42, places=2)
        self.assertEqual(ic.c_from_basis(ann), 2)


class TestSelection(unittest.TestCase):
    def _slots(self):
        return ic.list_slots(dt.date(2026, 11, 16), set())

    def _prices(self):
        return {"IC2611": 6280.4, "IC2612": 6218.4, "IC2703": 6075.6, "IC2706": 5917.6}

    def _discounts(self):
        index = 6297.183
        return {
            s["code"]: ic.ann_discount(index, self._prices()[s["code"]], s["days"])
            for s in self._slots()
        }

    def test_far_and_short_dated_excluded(self):
        eligible = ic.eligible_codes(self._slots(), self._prices())
        self.assertEqual(eligible, ["IC2612", "IC2703"])

    def test_hold_ic2611_and_roll_hint(self):
        slots = self._slots()
        decision = ic.decide_position(
            dt.date(2026, 11, 16), slots, self._prices(), self._discounts(),
            "IC2611", set(), target=4,
        )
        self.assertEqual(decision["action"], "hold")
        self.assertEqual(decision["contract"], "IC2611")
        self.assertEqual(decision["position_roll"], "2026-11-19")
        self.assertEqual(decision["roll_hint"], "IC2612")
        self.assertIn("持有 IC2611", decision["action_text"])
        self.assertIn("2026-11-19", decision["action_text"])
        self.assertIn("IC2612", decision["note"])

    def test_open_uses_3pp_switch_uses_2pp(self):
        discounts = {"NEAR": 0.05, "MID": 0.07, "FAR": 0.09}
        self.assertEqual(ic.choose_open(["NEAR", "MID", "FAR"], "NEAR", discounts), "FAR")
        discounts["FAR"] = 0.07
        self.assertEqual(ic.choose_open(["NEAR", "MID", "FAR"], "NEAR", discounts), "NEAR")
        self.assertEqual(ic.choose_switch("NEAR", ["NEAR", "MID", "FAR"], {"NEAR": 0.05, "MID": 0.069, "FAR": 0.069}), "NEAR")
        self.assertEqual(ic.choose_switch("NEAR", ["NEAR", "MID", "FAR"], {"NEAR": 0.05, "MID": 0.07, "FAR": 0.08}), "FAR")

    def test_forced_roll_reopens(self):
        # 2026-10-15 是 IC2610 的移仓日（最后交易日 10-16 的前一交易日）。
        slots = ic.list_slots(dt.date(2026, 10, 15), set())
        self.assertEqual(slots[0]["code"], "IC2610")
        self.assertEqual(ic.prev_trading_day(slots[0]["ltd"], set()).isoformat(), "2026-10-15")
        prices = {s["code"]: 6000.0 for s in slots}
        # 新近月贴水故意更高，移仓应离开 IC2610。
        discounts = {s["code"]: 0.02 for s in slots}
        discounts["IC2611"] = 0.11
        decision = ic.decide_position(
            dt.date(2026, 10, 15), slots, prices, discounts, "IC2610", set(), target=4,
        )
        self.assertEqual(decision["action"], "roll")
        self.assertEqual(decision["contract"], "IC2611")


class TestTriggers(unittest.TestCase):
    def test_grid_phrases_for_nov_16(self):
        rows = ic.trigger_rows(6297.183, 2, 0, 2, 7900.0, 7700.0, "2026-11-19", "IC2612", 0.1427)
        text = {r["cond"]: r["effect"] for r in rows}
        self.assertEqual(text["收盘跌破 6000"], "网格腿升到 4 手，合计加到 6 手")
        self.assertEqual(text["收盘站上 6500"], "网格腿降到 0 手，合计减到 2 手")
        self.assertIn("趋势腿升到 2 手，合计加到 6 手", text["收盘站上 7,900.00"])
        self.assertIn("趋势腿维持 0 手，合计仍为 4 手", text["收盘跌破 7,700.00"])
        self.assertIn("14.27%", text["2026-11-19 收盘决定贴水腿"])
        self.assertIn("C = +2", text["2026-11-19 收盘决定贴水腿"])
        self.assertIn("C 维持 +2", text["2026-11-19 收盘决定贴水腿"])

    def test_tomorrow_ma_identity(self):
        sum250 = 1000 * 250
        drop = 1000
        on, off = ic.b_trigger_prices(sum250, drop)

        def ma(t):
            return (sum250 - drop + t) / 250

        self.assertAlmostEqual(on, 1.01 * ma(on), places=6)
        self.assertAlmostEqual(off, 0.99 * ma(off), places=6)
        self.assertFalse(on > 1.01 * ma(on))
        self.assertTrue(on + 0.01 > 1.01 * ma(on + 0.01))
        self.assertFalse(off < 0.99 * ma(off))
        self.assertTrue(off - 0.01 < 0.99 * ma(off - 0.01))

    def test_close_call_note(self):
        rows = ic.trigger_rows(6002, 2, 0, 0, None, None, None, None, None)
        down = next(r for r in rows if r["kind"] == "grid_down")
        self.assertIn("收盘后请再核对", down["effect"])


class TestMerge(unittest.TestCase):
    def test_keeps_days_before_new_window(self):
        old = [{"date": "2024-01-02", "target": 1}, {"date": "2025-06-01", "target": 9}]
        new = [{"date": "2025-06-01", "target": 2}, {"date": "2025-06-02", "target": 3}]
        got = ic.merge_history(old, new)
        self.assertEqual([d["date"] for d in got], ["2024-01-02", "2025-06-01", "2025-06-02"])
        self.assertEqual(got[1]["target"], 2)


if __name__ == "__main__":
    unittest.main()
