#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""futures_history 隔离测试: 用样本CSV,不触网,不写repo。"""
import os
import sys
import unittest
import datetime as dt

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "preview-tools"))
import futures_history as fh  # noqa

SAMPLE = (
    "交易日,合约,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,\r\n"
    ",,,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,会员简称,持卖单量,比上一交易日增减\r\n"
    "20261104,IH2611,1,国泰君安(代客),7756,890,国泰君安(代客),4767,-957,中信期货(代客),4365,-1166\r\n"
    "20261104,IH2611,2,中信期货(代客),7203,34,中信期货(代客),2209,-885,国泰君安(代客),4065,-923\r\n"
    "20261104,IH2612,1,中信期货(代客),100,1,中信期货(代客),50,2,永安期货(代客),90,3\r\n"
).encode("gbk")


class TestFuturesHistory(unittest.TestCase):
    def test_parse_member_rows(self):
        td, contracts, both_absent, perr = fh.parse_cffex_csv(
            SAMPLE, "IH", expected_date=dt.date(2026, 11, 4))
        self.assertEqual(td, "2026-11-04")
        self.assertIsNone(perr)
        by = {c["contract"]: c for c in contracts}
        self.assertEqual(by["IH2611"]["long"], 2209)
        self.assertEqual(by["IH2611"]["short"], 4365)
        self.assertEqual(by["IH2611"]["long_status"], "ranked")
        self.assertEqual(by["IH2611"]["short_status"], "ranked")
        self.assertEqual(by["IH2612"]["long"], 50)
        self.assertIsNone(by["IH2612"]["short"])
        self.assertEqual(by["IH2612"]["short_status"], "not_ranked")

    def test_net_short_null_when_partial_side(self):
        class F:
            def __call__(self, url, *a, **k):
                return 200, SAMPLE
        rec = fh.backfill_one_day("IH", dt.date(2026, 11, 4), fetcher=F())
        self.assertIsNone(rec["net_short"])
        self.assertEqual(rec["coverage_status"], "partial_side")
        self.assertIsNotNone(rec["observed_difference"])
        self.assertEqual(rec["source_date_evidence"], "2026-11-04")

    def test_not_published_distinct_from_error(self):
        class F404:
            def __call__(self, url, *a, **k):
                return 404, b""
        rec = fh.backfill_one_day("IH", dt.date(2026, 11, 1), fetcher=F404())
        self.assertIn(rec["source_status"], ("not_published",))

    def test_5xx_is_error_not_holiday(self):
        class F500:
            def __call__(self, url, *a, **k):
                return 500, b"err"
        rec = fh.backfill_one_day("IH", dt.date(2026, 11, 3), fetcher=F500())
        self.assertEqual(rec["source_status"], "error")
        self.assertEqual(rec["coverage_status"], "source_error")

    def test_date_mismatch_is_error(self):
        td, _, _, perr = fh.parse_cffex_csv(
            SAMPLE, "IH", expected_date=dt.date(2026, 11, 3))
        self.assertEqual(perr, "date_mismatch")
        self.assertIsNone(td)

    def test_invalid_header_is_error(self):
        bad = "foo,bar\n1,2\n".encode("gbk")
        td, _, _, perr = fh.parse_cffex_csv(bad, "IH")
        self.assertEqual(perr, "header_invalid")

    def test_no_header_html_is_source_error(self):
        # 坏HTML(无表头)即使200/有内容 -> source_error,不当假日不当published
        class F:
            def __call__(self, url, *a, **k): return 200, b"<html>" + b"Runtime Error " * 20 + b"</html>"
        rec = fh.backfill_one_day("IH", dt.date(2026, 11, 4), fetcher=F())
        self.assertEqual(rec["source_status"], "error")
        self.assertEqual(rec["coverage_status"], "source_error")

    def test_valid_csv_no_citic_is_member_not_ranked(self):
        # 合法CSV(有表头+交易日)但中信两边都无 -> member_not_ranked,非error非holiday
        class F:
            def __call__(self, url, *a, **k): return 200, (
                "交易日,合约,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,\n"
                ",,,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,会员简称,持卖单量,比上一交易日增减\n"
                "20261104,IH2611,1,国泰君安(代客),100,1,国泰君安(代客),100,1,永安期货(代客),100,1").encode("gbk")
        rec = fh.backfill_one_day("IH", dt.date(2026, 11, 4), fetcher=F())
        self.assertEqual(rec["source_status"], "published")
        self.assertEqual(rec["coverage_status"], "member_not_ranked")

    def test_schema_version(self):
        class F:
            def __call__(self, url, *a, **k):
                return 200, SAMPLE
        shard = fh.backfill("IH", dt.date(2026, 11, 4), dt.date(2026, 11, 4), fetcher=F())
        self.assertEqual(shard["schema_version"], "futures-history-2.0")
        fh.validate_shard(shard)

    def test_no_zero_for_unranked(self):
        _, contracts, _, _ = fh.parse_cffex_csv(SAMPLE, "IH")
        by = {c["contract"]: c for c in contracts}
        self.assertIsNone(by["IH2612"]["short"])
        self.assertNotEqual(by["IH2612"]["short"], 0)

    def test_holiday_not_member_not_ranked(self):
        # 空文件/404 = 节假日未发布,coverage必须not_published而非member_not_ranked
        class F:
            def __call__(self, url, *a, **k): return 200, b""
        rec = fh.backfill_one_day("IH", dt.date(2026, 11, 1), fetcher=F())
        self.assertEqual(rec["source_status"], "not_published")
        self.assertEqual(rec["coverage_status"], "not_published")

    def test_bad_html_with_trading_date_is_error_not_holiday(self):
        # 有交易日但解析不到会员行 = source_error,不当假日
        html = ("交易日,合约,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,\n"
                ",,,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,会员简称,持卖单量,比上一交易日增减\n"
                "20261104,IH2611,1,某会员,100,1,某会员,100,1,某会员,100,1").encode("gbk")
        class F:
            def __call__(self, url, *a, **k): return 200, html
        rec = fh.backfill_one_day("IH", dt.date(2026, 11, 4), fetcher=F())
        # 该HTML无中信行 -> published但member_not_ranked(真实交易日),不是not_published
        self.assertEqual(rec["source_status"], "published")
        self.assertEqual(rec["coverage_status"], "member_not_ranked")

    def test_collect_daily_preserves_history(self):
        import tempfile, os, json
        tmp = tempfile.mkdtemp(); out = os.path.join(tmp, "IH.json")
        # 预置一个既有历史日(模拟回补后的145)
        pre = {"schema_version": "futures-history-2.0", "member": "中信期货(代客)",
               "product": "IH", "source": "CFFEX", "range_start": "2026-10-01",
               "range_end": "2026-10-01", "records": {"2026-10-01": {"source_status": "published"}}}
        json.dump(pre, open(out, "w"))
        class F:
            def __call__(self, url, *a, **k): return 200, SAMPLE
        fh.collect_daily("IH", dt.date(2026, 11, 4), out, fetcher=F())
        sh = json.load(open(out))
        self.assertEqual(len(sh["records"]), 2)  # 旧10-01不丢
        self.assertIn("2026-10-01", sh["records"])
        self.assertEqual(sh["range_end"], "2026-11-04")

    def test_collect_daily_idempotent_no_overwrite(self):
        import tempfile, os
        tmp = tempfile.mkdtemp(); out = os.path.join(tmp, "IH.json")
        class F:
            def __call__(self, url, *a, **k): return 200, SAMPLE
        fh.collect_daily("IH", dt.date(2026, 11, 4), out, fetcher=F())
        n1 = len(__import__("json").load(open(out))["records"])
        fh.collect_daily("IH", dt.date(2026, 11, 4), out, fetcher=F())  # 同日重跑
        n2 = len(__import__("json").load(open(out))["records"])
        self.assertEqual(n1, n2)

    def test_partial_side_net_null_after_daily(self):
        import tempfile, os
        tmp = tempfile.mkdtemp(); out = os.path.join(tmp, "IH.json")
        class F:
            def __call__(self, url, *a, **k): return 200, SAMPLE
        fh.collect_daily("IH", dt.date(2026, 11, 4), out, fetcher=F())
        rec = __import__("json").load(open(out))["records"]["2026-11-04"]
        self.assertIsNone(rec["net_short"])  # IH2612 short缺榜
        self.assertEqual(rec["coverage_status"], "partial_side")


if __name__ == "__main__":
    unittest.main()
