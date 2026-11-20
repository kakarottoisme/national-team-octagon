"""离线核对：国家队官方持仓文件的双口径结构、原文数字一致性与汇总可复算。"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLDINGS = ROOT / "data" / "etf_official_holdings.json"
WATCH = [
    "510300", "510310", "510330", "510050", "510180", "510500", "512500",
    "512100", "560010", "588000", "588080",
    "159919", "159845", "159629", "159915", "159977", "159352",
]
DIRECT_KINDS = {"中央汇金", "证金", "国新", "诚通", "其他国家队"}


class HoldingsStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(HOLDINGS.read_text(encoding="utf-8"))

    def test_schema_and_two_periods(self):
        self.assertEqual(self.doc["schema"], "etf-official-holdings-2")
        self.assertEqual(self.doc["annual_2025"]["as_of"], "2025-12-31")
        self.assertEqual(self.doc["interim_2026"]["as_of"], "2026-06-30")

    def test_annual_covers_all_17_watched_funds_once(self):
        funds = self.doc["annual_2025"]["funds"]
        codes = [f["code"] for f in funds]
        self.assertEqual(sorted(codes), sorted(WATCH))
        self.assertEqual(len(codes), 17)

    def test_every_annual_row_is_primary_sourced_and_well_formed(self):
        for f in self.doc["annual_2025"]["funds"]:
            self.assertTrue(f["top10_section"])
            self.assertTrue(f["source_url"].startswith(("https://www.sse.com.cn/", "https://static.cninfo.com.cn/")))
            self.assertEqual(f["as_of"], "2025-12-31")
            named = bool(f["holders"])
            self.assertEqual(f["national_team_named"], named)
            for h in f["holders"]:
                self.assertIn(h["holder_kind"], DIRECT_KINDS | {"汇金资管计划"})
                self.assertGreaterEqual(h["rank"], 1)
                self.assertLessEqual(h["rank"], 10)
                self.assertGreater(h["shares"], 0)
                self.assertGreater(h["pct_of_listed_shares"], 0)
                self.assertLessEqual(h["pct_of_listed_shares"], 100)

    def test_two_funds_have_no_named_national_team(self):
        summary = self.doc["annual_2025"]["summary"]
        self.assertEqual(summary["funds_with_named_national_team"], 15)
        self.assertEqual(sorted(summary["funds_without_named_national_team"]),
                         sorted(["159352", "588000"]))

    def test_summary_totals_recompute_from_fund_rows(self):
        rows = [h for f in self.doc["annual_2025"]["funds"] for h in f["holders"]]
        direct = sum(h["shares"] for h in rows if h["holder_kind"] != "汇金资管计划") / 1e8
        plans = sum(h["shares"] for h in rows if h["holder_kind"] == "汇金资管计划") / 1e8
        summary = self.doc["annual_2025"]["summary"]
        self.assertEqual(summary["direct_entity_shares_yi"], round(direct, 4))
        self.assertEqual(summary["asset_management_plan_shares_yi"], round(plans, 4))
        self.assertEqual(summary["named_holder_total_shares_yi"],
                         round(direct + plans, 4))
        self.assertEqual(len(rows), 32)

    def test_known_510300_huijin_snapshot(self):
        f = next(x for x in self.doc["annual_2025"]["funds"] if x["code"] == "510300")
        by_name = {h["holder_name"]: h for h in f["holders"]}
        self.assertEqual(by_name["中央汇金资产管理有限责任公司"]["shares"], 37858474974.0)
        self.assertEqual(by_name["中央汇金资产管理有限责任公司"]["pct_of_listed_shares"], 42.62)
        self.assertEqual(by_name["中央汇金投资有限责任公司"]["shares"], 35654598859.0)
        self.assertEqual(by_name["中央汇金投资有限责任公司"]["pct_of_listed_shares"], 40.14)

    def test_159977_manual_verification_carries_note(self):
        f = next(x for x in self.doc["annual_2025"]["funds"] if x["code"] == "159977")
        self.assertIn("verification_note", f)
        self.assertEqual(len(f["holders"]), 1)
        self.assertEqual(f["holders"][0]["holder_name"], "中央汇金投资有限责任公司")
        self.assertEqual(f["holders"][0]["shares"], 462012436.0)

    def test_interim_2026_rows_empty_but_all_17_checked_under_new_rule(self):
        interim = self.doc["interim_2026"]
        self.assertEqual(interim["rows"], [])
        self.assertEqual(len(interim["checked"]), 17)
        self.assertTrue(all(not c["top10_section"] for c in interim["checked"]))
        self.assertIn("〔2026〕5号", interim["gap"])

    def test_regulation_note_cites_primary_sources_and_article_31(self):
        notes = self.doc["regulation_notes"]
        self.assertEqual(len(notes), 1)
        body = notes[0]["rule"]
        self.assertIn("第三十一条", body)
        self.assertIn("2026-05-01", body)
        urls = notes[0]["urls"]
        for key in ("announcement", "rule_pdf", "press_release", "old_rule_archive"):
            self.assertTrue(urls[key].startswith("http"))


if __name__ == "__main__":
    unittest.main()
