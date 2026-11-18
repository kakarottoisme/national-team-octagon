"""Offline compatibility: frozen legacy outputs, code invariants and atomic failure."""
from __future__ import annotations
import ast
import contextlib
import datetime
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import archive
import fetch_live
from backend_common import parse_minutes
from backend_io import write_json_atomic, write_text_atomic

FIXTURE = json.loads((ROOT / "tests/fixtures/backend_compatibility.json").read_text(encoding="utf-8"))


def fixture_fetch(case):
    response = case["responses"]
    def fetch(url):
        if "minute/query" in url:
            return response["minutes"][url.split("code=")[1]]
        if "eastmoney" in url:
            return response["fallback"]
        if case.get("fallback"):
            raise RuntimeError("fixture fallback")
        return response["kline"]
    return fetch


class TestLegacyOutputs(unittest.TestCase):
    def test_live_and_archive_exact_json_bytes(self):
        now = datetime.datetime.fromisoformat(FIXTURE["clock"])
        for name, stored in FIXTURE["scenarios"].items():
            case = dict(stored, fallback=name == "fallback")
            for script, expected in case["outputs"].items():
                with self.subTest(scenario=name, script=script), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    data = root / "data"
                    data.mkdir()
                    (data / "history.json").write_text(json.dumps({"2026-11-14": {"合计": 7.0}}))
                    with contextlib.redirect_stdout(io.StringIO()):
                        if script == "fetch_live.py":
                            fetch_live.main(data / "live.json", fetch=fixture_fetch(case), now=now, include_indices=False)
                        else:
                            archive.main(data, fetch=fixture_fetch(case))
                    for path, legacy in expected.items():
                        self.assertEqual((root / path).read_bytes(), legacy.encode("utf-8"), path)

    def test_malformed_row_leaves_existing_live_file(self):
        case = json.loads(json.dumps(FIXTURE["scenarios"]["normal"]))
        case["responses"]["minutes"]["sh510050"]["data"]["sh510050"]["data"]["data"] = ["0930 6.1 10 broken"]
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "live.json"
            out.write_bytes(b'{"existing":true}')
            with self.assertRaises(ValueError):
                fetch_live.main(out, fetch=fixture_fetch(case))
            self.assertEqual(out.read_bytes(), b'{"existing":true}')

    def test_source_failure_does_not_write_archive(self):
        def fail(url):
            raise RuntimeError("fixture offline")
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            out = data / "history.json"
            out.write_bytes(b'{"existing":true}')
            with self.assertRaises(RuntimeError):
                archive.main(data, fetch=fail)
            self.assertEqual(out.read_bytes(), b'{"existing":true}')
            self.assertFalse((data / "minutes").exists())

    def test_strict_threshold_precision_is_legacy(self):
        raw = {"data": {"date": "20261117", "data": ["0930 6 0 100000000", "0931 6 0 200010000"]}}
        parsed = parse_minutes(raw, "sh510050")
        self.assertEqual(parsed["a"], [1.0, 1.0001])
        self.assertEqual(fetch_live.estimated_large_minutes(parsed["a"]), 1.0)


class TestNoBehaviorChanges(unittest.TestCase):
    def test_all_ic_non_io_functions_unchanged(self):
        tree = ast.parse((ROOT / "ic_strategy.py").read_text(encoding="utf-8"))
        current = {node.name: hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
                   for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "main"}
        self.assertEqual(current, FIXTURE["ic_functions"])

    def test_frontend_exact_hashes_unchanged(self):
        for name, expected in FIXTURE["frontend_sha256"].items():
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)

    def test_import_does_not_fetch_or_write(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("import fetched")), \
             patch("backend_io.write_json_atomic", side_effect=AssertionError("import wrote")):
            importlib.reload(fetch_live)
            importlib.reload(archive)

    def test_live_request_retry_policy_unchanged(self):
        with patch("urllib.request.urlopen", side_effect=OSError("fixture timeout")) as open_url, \
             patch("time.sleep") as sleep:
            with self.assertRaises(OSError):
                fetch_live.g("https://fixture.invalid")
            self.assertEqual(open_url.call_count, 3)
            self.assertEqual([call.kwargs["timeout"] for call in open_url.call_args_list], [20, 20, 20])
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.5, 3.0])

    def test_archive_timeout_no_retries(self):
        with patch("urllib.request.urlopen", side_effect=OSError("fixture timeout")) as open_url:
            with self.assertRaises(OSError):
                archive.g("https://fixture.invalid")
            self.assertEqual(open_url.call_count, 1)
            self.assertEqual(open_url.call_args.kwargs["timeout"], 15)


class TestAtomicIO(unittest.TestCase):
    def test_serialization_failure_retains_old_file(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "live.json"
            out.write_bytes(b'{"old":true}')
            with self.assertRaises(TypeError):
                write_json_atomic(out, {"unserializable": object()})
            self.assertEqual(out.read_bytes(), b'{"old":true}')
            self.assertEqual(len(list(Path(temp).iterdir())), 1)

    def test_replace_failure_cleans_temp_preserves_old(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "live.json"
            out.write_text("old")
            with patch("backend_io.os.replace", side_effect=OSError("fixture failed replace")):
                with self.assertRaises(OSError):
                    write_text_atomic(out, "new")
            self.assertEqual(out.read_text(), "old")
            self.assertEqual(len(list(Path(temp).iterdir())), 1)

    def test_encoding_format_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "live.json"
            out.write_text("old")
            out.chmod(0o640)
            write_json_atomic(out, {"中文": [None, 0, -1]}, ensure_ascii=False, indent=1)
            self.assertEqual(out.read_text(), json.dumps({"中文": [None, 0, -1]}, ensure_ascii=False, indent=1))
            self.assertEqual(out.stat().st_mode & 0o777, 0o640)


if __name__ == "__main__":
    unittest.main()
