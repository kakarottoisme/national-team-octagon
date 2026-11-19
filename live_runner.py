"""Keep one GitHub runner warm per market session; publish only changed live data.

The existing five-minute schedule remains a restart opportunity, not one queue per
successful refresh. This module never starts work on import and never changes
holdings, strategy, frontend, or archive data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from backend_common import ETF_UNIVERSE
from backend_io import write_json_atomic
from market_calendar import TZ, in_live_session, is_trading_day

ROOT = Path(__file__).resolve().parent
INTERVAL = 300


class PublishError(RuntimeError):
    """Stop on ambiguous git outcomes rather than replacing shared work."""


def session_end(now):
    now = now.astimezone(TZ)
    if not is_trading_day(now.date()):
        return None
    minute = now.hour * 60 + now.minute
    if 533 <= minute <= 695:  # 08:53 warm-up; collect only from 09:15.
        return now.replace(hour=11, minute=35, second=59, microsecond=0)
    if 763 <= minute <= 905:  # 12:43 warm-up; collect only from 12:55.
        return now.replace(hour=15, minute=5, second=59, microsecond=0)
    return None


def validate_payload(payload, now):
    """Reject yesterday, mixed dates, empty series and invalid prices."""
    day = now.astimezone(TZ).strftime('%Y%m%d')
    if payload.get('date') != day:
        raise ValueError('ETF source has not published the current trading day')
    expected = {code for _, code in ETF_UNIVERSE}
    if set(payload.get('etfs', {})) != expected:
        raise ValueError('incomplete ETF universe')
    for code in expected:
        row = payload['etfs'][code]
        if row.get('date') != day:
            raise ValueError('mixed ETF trading dates')
        ticks, prices, amounts = row.get('t', []), row.get('p', []), row.get('a', [])
        if not ticks or not (len(ticks) == len(prices) == len(amounts)):
            raise ValueError('empty or misaligned ETF minutes')
        tick = str(ticks[-1])
        if len(tick) != 4 or not tick.isdigit() or int(tick[:2]) > 23 or int(tick[2:]) > 59:
            raise ValueError('invalid latest ETF minute')
        local = now.astimezone(TZ)
        last = local.replace(hour=int(tick[:2]), minute=int(tick[2:]), second=0, microsecond=0)
        age = (local - last).total_seconds()
        # Some Tencent points label the minute ahead. Never relabel stale same-day
        # quotes as fresh merely because the collector ran again.
        if not -120 <= age <= 600:
            raise ValueError('stale or future ETF minute; previous data kept')
        if any(not math.isfinite(float(p)) or float(p) <= 0 for p in prices):
            raise ValueError('invalid ETF price')
        if any(not math.isfinite(float(a)) for a in amounts):
            raise ValueError('invalid minute amount')
    return payload


def meaningful(payload):
    return {key: value for key, value in payload.items() if key != 'updated'}


def git(*args, capture=False):
    result = subprocess.run(['git', *args], cwd=ROOT, timeout=60,
                            capture_output=True, text=True)
    if result.returncode:
        # Do not print HTTP credentials or raw git exception strings.
        raise PublishError('git operation failed: ' + args[0])
    return result.stdout.strip() if capture else None


def sync_main():
    if git('status', '--porcelain', capture=True):
        raise PublishError('checkout contains unpublished changes; stop without overwrite')
    git('fetch', '--depth=50', 'origin', 'main')
    if git('rev-list', 'origin/main..HEAD', capture=True):
        raise PublishError('unconfirmed local commit; stop without replay')
    git('merge', '--ff-only', 'origin/main')


def collect_candidate():
    # fetch_live.main keeps the original data formula, precision and optional
    # index-minute enrichment. Its output goes to a temporary file, not live.json.
    with tempfile.TemporaryDirectory(prefix='octagon-live-') as directory:
        target = Path(directory) / 'candidate.json'
        result = subprocess.run(
            [sys.executable, '-B', '-c',
             'import sys,fetch_live; fetch_live.main(output=sys.argv[1])', str(target)],
            cwd=ROOT, timeout=240, capture_output=True, text=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        if result.returncode:
            raise ValueError('live collector failed; previous data kept')
        return json.loads(target.read_text(encoding='utf-8'))


def publish_once(clock=None, collect=None):
    clock = clock or (lambda: dt.datetime.now(TZ))
    collect = collect or collect_candidate
    if not in_live_session(clock()):
        return 'outside session'
    sync_main()
    payload = validate_payload(collect(), clock())
    if not in_live_session(clock()):
        return 'collection completed outside session; candidate discarded'
    # Re-read main after collection: other tasks may have committed while HTTP ran.
    sync_main()
    target = ROOT / 'data' / 'live.json'
    old = json.loads(target.read_text(encoding='utf-8'))
    if meaningful(payload) == meaningful(old):
        return 'no meaningful change'
    write_json_atomic(target, payload, ensure_ascii=False, separators=(',', ':'))
    git('add', '--', 'data/live.json')
    git('commit', '-m', 'live ' + payload['updated'][11:16])
    for attempt in range(3):
        try:
            git('push', 'origin', 'HEAD:main')
            return 'published ' + payload['updated']
        except PublishError:
            if attempt == 2:
                raise
            git('fetch', '--depth=50', 'origin', 'main')
            try:
                git('rebase', 'origin/main')
            except PublishError:
                git('rebase', '--abort')
                raise PublishError('concurrent live conflict; local commit kept, no overwrite')
    raise PublishError('push not confirmed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    now = dt.datetime.now(TZ)
    end = session_end(now)
    if end is None:
        print('not a verified trading-session window; no collection')
        return 0
    failures = 0
    while dt.datetime.now(TZ) <= end:
        started = time.monotonic()
        try:
            result = publish_once()
            failures = 0
            print(result, flush=True)
        except PublishError as exc:
            print(str(exc), flush=True)
            return 1  # Do not replay unknown write outcomes.
        except Exception as exc:
            failures += 1
            print(f'collection failure {failures}: {type(exc).__name__}; previous data kept', flush=True)
        if args.once:
            return int(failures > 0)
        remaining = (end - dt.datetime.now(TZ)).total_seconds()
        if remaining <= 0:
            break
        # Five minutes start-to-start, no catch-up burst after a slow fetch.
        time.sleep(min(max(1, INTERVAL - (time.monotonic() - started)), remaining))
    print('session ended; no collection during lunch or after close', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
