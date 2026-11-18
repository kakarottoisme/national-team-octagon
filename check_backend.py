#!/usr/bin/env python3
"""Run deterministic regression without network or writes to production assets.

No collector entrypoints are executed with real sources. Use the same command
locally and in a clean checkout. Tests use temporary output directories.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "preview-tools")]


def protected_files():
    paths = list(ROOT.glob("*.html")) + list(ROOT.glob("*.js"))
    for directory in ("data", "preview-data", ".github/workflows"):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file())
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    before = protected_files()
    suite = unittest.TestSuite()
    # Avoid the local single-file packaging suite, which is not a repository dependency.
    for pattern in ("test_backend_compatibility.py", "test_ic_strategy.py", "test_dashboard_ui.py", "test_reviewed_ux.py", "test_chart_readout.py", "test_market_status.py"):
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern))
    # Separate loaders keep unittest's discovery root state from crossing directories.
    suite.addTests(unittest.TestLoader().discover(str(ROOT / "preview-tools/tests")))
    import test_global_sources
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(test_global_sources))
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Offline regression forbids network")), \
         patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Offline regression forbids network")), \
         patch("socket.create_connection", side_effect=AssertionError("Offline regression forbids network")), \
         patch("urllib.request.urlopen", side_effect=AssertionError("Offline regression forbids HTTP")):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = protected_files()
    if before != after:
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        print("ERROR: tests changed protected files:", ", ".join(changed), file=sys.stderr)
        return 1
    print("Protected frontend, production data and workflow files unchanged.")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
