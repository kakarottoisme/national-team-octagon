"""离线口径：份额缺口不补、深市无日期不进交易日、申赎用成交均价。"""
import datetime as dt
import json
import unittest
from decimal import Decimal

import etf_share_flows as f


def sse_row(day, code, wan):
    return {"STAT_DATE": day, "SEC_CODE": code, "SEC_NAME": "x", "TOT_VOL": wan, "ETF_TYPE": "单市", "NUM": "1"}


class ParseTests(unittest.TestCase):
    def test_sse_rejects_mismatched_stat_date_and_keeps_wan_text(self):
        payload = {"pageHelp": {"data": [
            sse_row("2026-11-19", "510300", "5154078.77"),
            sse_row("2026-11-18", "510050", "1"),
            sse_row("2026-11-19", "159919", "9"),
            sse_row("2026-11-19", "510050", "-"),
        ]}}
        self.assertEqual(f.parse_sse_payload(payload, "2026-11-19"), {"510300": "5154078.77"})
        self.assertEqual(f.shares_yi_text("5154078.77"), "515.4079")
        self.assertEqual(f.shares_yi_text("1666056.68"), "166.6057")

    def test_kline_vwap_uses_amount_over_shares(self):
        line = "2026-11-19,4.046,4.060,4.072,4.013,27909417,11293465472.000"
        rows = f.parse_kline_payload({"data": {"klines": [line, "bad"]}})
        quote = rows["2026-11-19"]
        price = f.vwap_of(quote)
        self.assertEqual(price, Decimal("11293465472.000") / (Decimal(27909417) * 100))
        self.assertIsNone(f.vwap_of({"close": "1", "volume_hand": "0", "amount_yuan": "1"}))

    def test_szse_inline_strings_have_no_stat_date(self):
        sheet = """<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c t="inlineStr"><is><t>基金代码</t></is></c><c t="inlineStr"><is><t>基金简称</t></is></c><c t="inlineStr"><is><t>当前规模(份)</t></is></c><c t="inlineStr"><is><t>净值</t></is></c></row>
<row r="2"><c t="inlineStr"><is><t>159919</t></is></c><c t="inlineStr"><is><t>沪深300ETF嘉实</t></is></c><c t="inlineStr"><is><t>1,000,000,000</t></is></c><c t="inlineStr"><is><t>4.010</t></is></c></row>
<row r="3"><c t="inlineStr"><is><t>510300</t></is></c><c t="inlineStr"><is><t>沪市</t></is></c><c t="inlineStr"><is><t>5</t></is></c><c t="inlineStr"><is><t>1</t></is></c></row>
</sheetData></worksheet>"""
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as book:
            book.writestr("xl/worksheets/sheet1.xml", sheet)
        parsed = f.parse_szse_xlsx(buf.getvalue())
        self.assertEqual(set(parsed), {"159919"})
        self.assertEqual(parsed["159919"]["scale_shares"], "1000000000")
        self.assertNotIn("stat_date", parsed["159919"])


class FlowTests(unittest.TestCase):
    def history(self):
        days = {}
        quotes = {}
        # 三个官方交易日。510300 中间缺一天；510050 连续。
        for day, wan300, wan050, amount in (
            ("2026-11-17", "1000000.00", "2000000.00", "400000000"),
            ("2026-11-18", None, "2000000.00", "400000000"),
            ("2026-11-19", "1100000.00", "2500000.00", "800000000"),
        ):
            bucket = {}
            if wan300:
                bucket["510300"] = {"tot_vol_wan": wan300, "stat_date": day, "source": "sse"}
            if wan050:
                bucket["510050"] = {"tot_vol_wan": wan050, "stat_date": day, "source": "sse"}
            days[day] = bucket
            quotes[day] = {
                "510300": {"close": "4.000", "volume_hand": "10000", "amount_yuan": amount},
                "510050": {"close": "2.000", "volume_hand": "10000", "amount_yuan": amount},
            }
        return {"schema": f.SCHEMA, "days": days, "quotes": quotes, "blocked_dates": [], "updated_at": "2026-11-20T23:40:00+08:00"}

    def test_hole_blocks_change_and_missing_is_not_zero(self):
        flows = f.build_flows(self.history(), {"latest": {"collected_at": "2026-11-20T23:50:00+08:00", "rows": {
            "159919": {"name": "沪深300ETF嘉实", "scale_shares": "20000000000", "nav": "4.2"}
        }}})
        by = {item["code"]: item for item in flows["etfs"]}
        self.assertEqual(flows["as_of"], "2026-11-19")
        self.assertIsNone(by["510300"]["delta_1d_yi"])
        self.assertIsNone(by["510300"]["inflow_1d_yi"])
        self.assertIn("缺官方份额", by["510300"]["coverage_1d"])
        # 510050: 250-200 = 50 亿份；均价 = 8e8 / (10000*100) = 800
        # 流入 = 50 亿份 × 800 元 = 40000 亿元。夹具只验公式。
        self.assertEqual(by["510050"]["delta_1d_yi"], "50.0000")
        self.assertEqual(by["510050"]["inflow_1d_yi"], "40000.0000")
        self.assertEqual(by["510050"]["shares_yi"], "250.0000")
        self.assertEqual(by["510050"]["aum_yi"], "500.00")
        self.assertIsNone(by["159919"]["shares_yi"])
        self.assertIsNone(by["159919"]["inflow_1d_yi"])
        self.assertEqual(by["159919"]["undated_snapshot_yi"], "200.0000")
        self.assertEqual(by["159919"]["shares_status"], "pending_unverifiable")
        self.assertEqual(flows["summary"]["inflow_1d_covered"], 1)
        self.assertNotEqual(flows["summary"]["inflow_1d_yi"], "0.0000")
        self.assertEqual(flows["dates"], ["2026-11-17", "2026-11-18", "2026-11-19"])

    def test_blocked_request_is_a_hole_not_a_holiday(self):
        history = self.history()
        history["blocked_dates"] = ["2026-11-18"]
        history["days"].pop("2026-11-18")
        flows = f.build_flows(history, {})
        by = {item["code"]: item for item in flows["etfs"]}
        self.assertIsNone(by["510050"]["delta_1d_yi"])
        self.assertIn("2026-11-18", flows["dates"])

    def test_zero_volume_does_not_invent_vwap(self):
        history = self.history()
        history["quotes"]["2026-11-19"]["510050"]["volume_hand"] = "0"
        flows = f.build_flows(history, {})
        by = {item["code"]: item for item in flows["etfs"]}
        self.assertEqual(by["510050"]["delta_1d_yi"], "50.0000")
        self.assertIsNone(by["510050"]["inflow_1d_yi"])
        self.assertIsNone(by["510050"]["vwap"])

    def test_import_and_rebuild_do_not_need_network(self):
        self.assertIn("全市场", f.METHOD)
        self.assertEqual(len(f.WATCH), 17)
        self.assertIn("159352", f.BY_CODE)


class RecordTests(unittest.TestCase):
    def test_snapshot_is_not_stamped_onto_a_trading_day(self):
        doc, changed = f.record_szse({"snapshots": []}, {"159915": {"scale_shares": "10", "name": "创业板", "nav": None}}, "2026-11-20T23:40:00+08:00")
        self.assertTrue(changed)
        self.assertEqual(doc["latest"]["date_status"], "undated")
        self.assertNotIn("stat_date", doc["latest"])
        again, changed = f.record_szse(doc, doc["latest"]["rows"], "2026-11-21T23:40:00+08:00")
        self.assertFalse(changed)
        self.assertEqual(len(again["snapshots"]), 1)

    def test_backfill_empty_sse_is_not_a_share_row(self):
        calls = []

        def fake_get(url, headers=None, timeout=25, binary=False):
            calls.append(url)
            if "kline" in url:
                body = {"data": {"klines": ["2026-11-19,1,1,1,1,100,10000"]}}
                return 200, json.dumps(body)
            if "ShowReport" in url:
                return 200, b""
            if "STAT_DATE=2026-11-19" in url:
                payload = {"pageHelp": {"data": [sse_row("2026-11-19", code, "10000.00") for code in f.SH_CODES]}}
                return 200, json.dumps(payload)
            return 200, json.dumps({"pageHelp": {"data": []}})

        original = (f.HISTORY_PATH, f.FLOWS_PATH, f.SZSE_PATH)
        import tempfile
        from pathlib import Path
        folder = Path(tempfile.mkdtemp())
        f.HISTORY_PATH = folder / "history.json"
        f.FLOWS_PATH = folder / "flows.json"
        f.SZSE_PATH = folder / "szse.json"
        try:
            result = f.backfill(dt.date(2026, 11, 19), dt.date(2026, 11, 20), get=fake_get, sleep=lambda s: None,
                                now=dt.datetime(2026, 11, 20, 23, 40, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        finally:
            f.HISTORY_PATH, f.FLOWS_PATH, f.SZSE_PATH = original
        self.assertEqual(result["added"], ["2026-11-19"])
        saved = json.loads((folder / "history.json").read_text())
        self.assertNotIn("2026-11-20", saved["days"])
        self.assertNotIn("2026-11-20", saved["blocked_dates"])


if __name__ == "__main__":
    unittest.main()
