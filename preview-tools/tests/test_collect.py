#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性离线测试: 不触网,用伪造 Fetcher 注入固定响应。
覆盖: 单位换算、空数据/未发布、单源失败保留旧值(stale)、窗口缺洞、
      拆合告警、幂等(同输入同输出)、日期错配(SZSE无日期禁算)、期货缺席≠0。
运行: python3 -m unittest preview-tools/tests/test_collect.py -v
"""
import json
import os
import sys
import unittest
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import collect_preview as cp  # noqa: E402


def sse_payload(rows):
    """rows: list of (code, TOT_VOL万份)."""
    return {"pageHelp": {"data": [{"SEC_CODE": c, "TOT_VOL": str(v)} for c, v in rows]}}


class FakeFetcher:
    """按 URL 模式返回 canned。"""
    def __init__(self, sse=None, cffex=None, szse=b"", sse_status=200, cffex_status=200,
                 raise_sse=False):
        self.sse = sse or {}          # date_str -> payload dict
        self.cffex = cffex or {}      # date() -> {p: csv_text}
        self.szse = szse
        self.sse_status = sse_status
        self.cffex_status = cffex_status
        self.raise_sse = raise_sse

    def get(self, url, headers=None, binary=False):
        if "query.sse.com.cn" in url:
            m = re_search(url, r"STAT_DATE=(\d{4}-\d{2}-\d{2})")
            if self.raise_sse:
                raise RuntimeError("network down")
            if m in self.sse:
                return self.sse_status, json.dumps(self.sse[m]).encode("utf-8")
            return 200, json.dumps(sse_payload([])).encode("utf-8")  # 未发布=空
        if "cffex.com.cn" in url:
            return self.cffex_status, b""
        if "fund.szse.cn" in url:
            return 200, self.szse
        return 404, b""


def re_search(url, pat):
    import re
    mm = re.search(pat, url)
    return mm.group(1) if mm else None


class TempWorkdir(unittest.TestCase):
    def setUp(self):
        # 关键: 重定向到独立临时目录,绝不读写主 repo 的 preview-data/official
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="pvtest_")
        cp.OFFICIAL_DIR = self._tmp
        cp.HIST_PATH = os.path.join(self._tmp, "history.json")
        cp.SNAP_PATH = os.path.join(self._tmp, "snapshot-latest.json")
        cp.STATUS_PATH = os.path.join(self._tmp, "status.json")
        # stub 全球模块,测试不触网
        self._orig_global = cp.maybe_collect_global
        cp.maybe_collect_global = lambda: ("ok", {"state": "ok", "connected": 1, "test": True})

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        cp.maybe_collect_global = self._orig_global
        cp.OFFICIAL_DIR = os.path.join(REPO, "preview-data", "official")
        cp.HIST_PATH = os.path.join(cp.OFFICIAL_DIR, "history.json")
        cp.SNAP_PATH = os.path.join(cp.OFFICIAL_DIR, "snapshot-latest.json")
        cp.STATUS_PATH = os.path.join(REPO, "preview-data", "status.json")


class TestUnitsAndFetch(TempWorkdir):
    def test_sse_unit_conversion(self):
        class F:
            def get(self, *a, **k):
                return 200, json.dumps(sse_payload([("510300", 4458738.77)])).encode()
        n, mp = cp.fetch_sse(F(), "2026-11-03")
        self.assertEqual(n, 1)
        self.assertAlmostEqual(mp["510300"], 445.8739, places=4)  # 万份->亿份

    def test_szse_no_date_is_pending_not_used(self):
        # 深市份额绝不能进官方份额历史 / growth
        ft = FakeFetcher(sse={"2026-11-03": sse_payload([("510300", 4480000.0)])})
        snap = cp.run(target_date=dt.date(2026, 11, 3), fetcher=ft,
                      now=dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        e159 = [e for e in snap["etfs"] if e["code"] == "159915"][0]
        self.assertIsNone(e159["shares_yi"])
        self.assertEqual(e159["shares_status"], "pending_unverifiable")
        self.assertIsNone(e159["growth_1d_pct"])

    def test_empty_sse_unpublished_then_later_day(self):
        # 11-04空(未发布),应回退到11-03
        ft = FakeFetcher(sse={"2026-11-03": sse_payload([("510300", 4480000.0)])})
        snap = cp.run(target_date=dt.date(2026, 11, 4), fetcher=ft,
                      now=dt.datetime(2026, 11, 4, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        self.assertEqual(snap["as_of"], "2026-11-03")


class TestWindowsAndAnomaly(TempWorkdir):
    def _seed_history(self, values):
        """values: {date_str: {code: shares}} 直接灌 history。"""
        hist = {}
        for d, kv in values.items():
            hist[d] = {"etfs": {c: {"shares_yi": v, "shares_status": "official_confirmed"}
                                for c, v in kv.items()}, "futures": {}}
        cp.save_json(cp.HIST_PATH, hist)

    def test_window_incomplete_is_null_not_padded(self):
        self._seed_history({"2026-11-03": {"510300": 448.0}, "2026-10-30": {"510300": 440.0}})
        ft = FakeFetcher(sse={"2026-11-03": sse_payload([("510300", 4480000.0)])})
        snap = cp.run(target_date=dt.date(2026, 11, 3), fetcher=ft,
                      now=dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        e = [x for x in snap["etfs"] if x["code"] == "510300"][0]
        # 历史仅2天 -> 1d 可算, 5d/20d 必须 null
        self.assertIsNotNone(e["growth_1d_pct"])
        self.assertIsNone(e["growth_5d_pct"])
        self.assertIn("不足", e["coverage_reason_5d"])
        self.assertIsNone(e["growth_20d_pct"])

    def test_split_alert_suppresses_growth(self):
        self._seed_history({"2026-11-02": {"510300": 100.0}, "2026-11-03": {"510300": 200.0}})
        ft = FakeFetcher(sse={"2026-11-03": sse_payload([("510300", 2000000.0)])})
        snap = cp.run(target_date=dt.date(2026, 11, 3), fetcher=ft,
                      now=dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        e = [x for x in snap["etfs"] if x["code"] == "510300"][0]
        self.assertEqual(e["anomaly"], "anomaly_pending_verification")
        self.assertIsNone(e["growth_1d_pct"])

    def test_idempotent_same_input_same_output(self):
        ft = FakeFetcher(sse={"2026-11-03": sse_payload([("510300", 4480000.0), ("510050", 1600000.0)])})
        ts = dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        cp.run(target_date=dt.date(2026, 11, 3), fetcher=ft, now=ts)
        with open(cp.SNAP_PATH, encoding="utf-8") as fh:
            a = json.load(fh)
        # 清空后用同样输入再跑
        for p in (cp.HIST_PATH, cp.SNAP_PATH):
            os.remove(p)
        cp.run(target_date=dt.date(2026, 11, 3), fetcher=ft, now=ts)
        with open(cp.SNAP_PATH, encoding="utf-8") as fh:
            b = json.load(fh)
        a.pop("generated_at"); b.pop("generated_at")
        a.pop("collected_at"); b.pop("collected_at")
        self.assertEqual(a, b)


class TestSingleSourceFailure(TempWorkdir):
    def test_sse_failure_keeps_old_stale(self):
        # 先有一天正常数据
        ft1 = FakeFetcher(sse={"2026-11-02": sse_payload([("510300", 4400000.0)])})
        cp.run(target_date=dt.date(2026, 11, 2), fetcher=ft1,
               now=dt.datetime(2026, 11, 2, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        # 次日 SSE 全挂
        ft2 = FakeFetcher(raise_sse=True)
        snap = cp.run(target_date=dt.date(2026, 11, 3), fetcher=ft2,
                      now=dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        self.assertEqual(snap["run_status"], "failed")
        self.assertIn("SSE", snap["errors"][0])


class TestFuturesAbsentNotZero(TempWorkdir):
    def test_absent_contract_not_treated_zero(self):
        ft = FakeFetcher(sse={"2026-11-03": sse_payload([("510300", 4480000.0)])})
        # CFFEX 返回空(未发布/该产品当日无中信上榜) -> 产品各字段应为0但 note 声明缺席≠0
        snap = cp.run(target_date=dt.date(2026, 11, 3), fetcher=ft,
                      now=dt.datetime(2026, 11, 3, 17, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
        ih = snap["futures"]["products"]["IH"]
        self.assertEqual(ih["observed_contracts"], [])
        self.assertIn("缺席", ih["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
