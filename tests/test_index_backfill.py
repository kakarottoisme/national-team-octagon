"""Offline contracts for exact index backfill and future archive enrichment."""
from __future__ import annotations
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import archive
from backfill_index_minutes import canonical_date, expected_times, parse_sina_history, merge_indices

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'tests/fixtures/backend_compatibility.json').read_text())


def native_series(day='20261117', interval=1):
    times = expected_times(interval)
    return {'date': day, 't': times, 'p': [6300.0 + i for i in range(len(times))],
            'interval_minutes': interval, 'source': 'fixture', 'interpolated': False}


def fixture_fetch(url):
    response = FIXTURE['scenarios']['normal']['responses']
    if 'minute/query' in url:
        code = url.split('code=')[1]
        if code in response['minutes']:
            return response['minutes'][code]
        raise OSError('optional source unavailable')
    return response['kline']


class IndexBackfillTests(unittest.TestCase):
    def test_normalize_native_and_iso_dates(self):
        self.assertEqual(canonical_date('20261104'), '2026-11-04')
        self.assertEqual(canonical_date('2026-11-04'), '2026-11-04')
        for invalid in ['20261304', '', '2026-11-31', '2026-11-04 13:25']:
            with self.assertRaises(ValueError): canonical_date(invalid)

    def test_exact_five_minute_endpoints_and_metadata(self):
        rows = [{'day': '2026-11-04 '+t[:2]+':'+t[2:]+':00', 'close': str(6380+i/100)} for i,t in enumerate(expected_times(5))]
        # An incomplete previous day must not be relabelled as a complete day.
        rows.append({'day': '2026-11-03 15:00:00', 'close': '6000'})
        data = parse_sina_history('var x('+json.dumps(rows)+');', 5, 'https://fixture.invalid/kline', '2026-11-20')
        self.assertEqual(list(data), ['2026-11-04'])
        item = data['2026-11-04']
        self.assertEqual(len(item['t']), 48)
        self.assertNotIn('1326', item['t'])
        self.assertIn('1325', item['t'])
        self.assertEqual(item['interval_minutes'], 5)
        self.assertFalse(item['interpolated'])
        self.assertEqual(item['price_type'], 'bar_close')

    def test_bad_close_duplicate_or_missing_is_not_invented(self):
        rows = [{'day': '2026-11-04 '+t[:2]+':'+t[2:]+':00', 'close': '6384'} for t in expected_times(5)]
        missing = rows[:-1]
        self.assertEqual(parse_sina_history(json.dumps(missing), 5, 'fixture', 'now'), {})
        duplicate = rows + [{**rows[-1], 'close': '7000'}]
        with self.assertRaises(ValueError): parse_sina_history(json.dumps(duplicate), 5, 'fixture', 'now')
        rows[0]['close'] = 'NaN'
        with self.assertRaises(ValueError): parse_sina_history(json.dumps(rows), 5, 'fixture', 'now')

    def test_merge_preserves_etf_and_higher_resolution(self):
        old = {'date': '2026-11-17', 'etfs': {'sh510500': {'t':['1325'], 'p':[6.43], 'a':[.1211]}},
               'index_minutes': {'sh000905': native_series()}}
        original = copy.deepcopy(old)
        data = merge_indices(old, {'sh000905': native_series('2026-11-17', 5)})
        self.assertEqual(data, original)
        self.assertEqual(old, original)
        lower = {'date':'2026-11-17','etfs':original['etfs'],'index_minutes':{'sh000905':native_series('2026-11-17',5)}}
        upgraded = merge_indices(lower, {'sh000905':native_series()})
        self.assertEqual(upgraded['index_minutes']['sh000905']['interval_minutes'],1)
        self.assertEqual(upgraded['etfs'],lower['etfs'])
        with self.assertRaises(ValueError):merge_indices(old, {'sh000905':native_series('2026-11-16')})

    def test_archive_saves_indices_without_changing_etf_or_summary(self):
        date, summary, legacy = archive.build_archive(fetch=fixture_fetch)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('index_quotes.fetch_index_minutes', return_value={'sh000905':native_series(date)}) as probe, contextlib.redirect_stdout(io.StringIO()):
                archive.main(root, fetch=fixture_fetch)
            probe.assert_called_once()
            result=json.loads((root/'minutes'/f'{date}.json').read_text())
            self.assertEqual(result['etfs'],legacy['etfs'])
            self.assertEqual(json.loads((root/'history.json').read_text())[date],summary)
            self.assertEqual(result['index_minutes']['sh000905']['interval_minutes'],1)
            self.assertEqual(result['index_minutes']['sh000905']['date'],date)

    def test_optional_source_failure_retains_same_day_cached_index(self):
        date, summary, legacy = archive.build_archive(fetch=fixture_fetch)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'minutes').mkdir()
            old={**legacy,'index_minutes':{'sh000905':native_series(date,5)}}
            path=root/'minutes'/f'{date}.json';path.write_text(json.dumps(old))
            # Partial native data must not replace complete cached 5-minute data.
            partial={'date':date,'t':['1325'],'p':[9999]}
            with patch('index_quotes.fetch_index_minutes', return_value={'sh000905':partial}), contextlib.redirect_stdout(io.StringIO()):archive.main(root,fetch=fixture_fetch)
            result=json.loads(path.read_text())
            self.assertEqual(result['index_minutes'],old['index_minutes'])
            self.assertEqual(result['etfs'],old['etfs'])

    def test_other_day_live_cache_never_backfills_old_date(self):
        date, _, legacy=archive.build_archive(fetch=fixture_fetch)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'live.json').write_text(json.dumps({'date':'2026-01-01','index_minutes':{'sh000905':native_series('2026-01-01')}}))
            with patch('index_quotes.fetch_index_minutes', return_value={}), contextlib.redirect_stdout(io.StringIO()):archive.main(root,fetch=fixture_fetch)
            result=json.loads((root/'minutes'/f'{date}.json').read_text())
            self.assertEqual(result,legacy)


if __name__=='__main__':unittest.main()
