import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_runner as runner
from backend_common import ETF_UNIVERSE
from market_calendar import TZ


def at(hour, minute, day=19):
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def payload():
    return {'date': '20261119', 'updated': '2026-11-19 10:00:00',
            'etfs': {code: {'date': '20261119', 't': ['0930', '1000'],
                            'p': [1.0, 1.1], 'a': [0.1, 1.1]}
                     for _, code in ETF_UNIVERSE}, 'estimates': {'合计': 7.7}}


class LiveRunnerTests(unittest.TestCase):
    def test_verified_windows_and_warmup(self):
        self.assertIsNone(runner.session_end(at(8, 52)))
        self.assertEqual(runner.session_end(at(8, 53)).strftime('%H:%M'), '11:35')
        self.assertEqual(runner.session_end(at(11, 35)).strftime('%H:%M'), '11:35')
        self.assertIsNone(runner.session_end(at(11, 36)))
        self.assertIsNone(runner.session_end(at(12, 42)))
        self.assertEqual(runner.session_end(at(12, 43)).strftime('%H:%M'), '15:05')
        self.assertIsNone(runner.session_end(at(15, 6)))
        self.assertIsNone(runner.session_end(at(10, 0, day=21)))
        self.assertIsNone(runner.session_end(dt.datetime(2026, 10, 1, 10, tzinfo=TZ)))

    def test_valid_payload_has_no_transform(self):
        data = payload()
        before = copy.deepcopy(data)
        self.assertIs(runner.validate_payload(data, at(10, 0)), data)
        self.assertEqual(data, before)

    def test_yesterday_and_mixed_dates_rejected(self):
        for key in ('date', 'etf_date'):
            data = payload()
            if key == 'date': data['date'] = '20261118'
            else: data['etfs']['sh510050']['date'] = '20261118'
            with self.assertRaises(ValueError): runner.validate_payload(data, at(10, 0))

    def test_partial_empty_invalid_rejected(self):
        variants = []
        data = payload(); del data['etfs']['sh510050']; variants.append(data)
        for change in ({'t': []}, {'p': [1]}, {'p': [1, float('nan')]}, {'a': [0.1, float('inf')]}):
            data = payload(); data['etfs']['sh510050'].update(change); variants.append(data)
        for data in variants:
            with self.assertRaises(ValueError): runner.validate_payload(data, at(10, 0))

    def test_timestamp_only_is_not_change(self):
        old = payload(); new = copy.deepcopy(old); new['updated'] = '2026-11-19 10:05:00'
        self.assertEqual(runner.meaningful(old), runner.meaningful(new))
        self.assertIn('updated', old)

    def test_outside_session_no_fetch_or_git(self):
        with patch.object(runner, 'sync_main') as sync, patch.object(runner, 'collect_candidate') as collect:
            self.assertEqual(runner.publish_once(clock=lambda: at(12, 50)), 'outside session')
            sync.assert_not_called(); collect.assert_not_called()

    def test_failure_does_not_write(self):
        with patch.object(runner, 'sync_main'), patch.object(runner, 'git') as git, patch.object(runner, 'write_json_atomic') as write:
            data = payload(); data['date'] = '20261118'
            with self.assertRaises(ValueError): runner.publish_once(clock=lambda: at(10, 0), collect=lambda: data)
            write.assert_not_called(); git.assert_not_called()

    def test_stale_same_day_and_future_minute_rejected(self):
        for tick in ('0949', '1003', '2561'):
            data = payload(); data['etfs']['sh510050']['t'][-1] = tick
            with self.assertRaises(ValueError): runner.validate_payload(data, at(10, 0))
        data = payload(); data['etfs']['sh510050']['t'][-1] = '1001'
        runner.validate_payload(data, at(10, 0))

    def test_fetch_crossing_close_discards_candidate(self):
        clock = iter([at(11, 35), at(11, 36), at(11, 36)])
        data = payload()
        for row in data['etfs'].values(): row['t'][-1] = '1135'
        with patch.object(runner, 'sync_main'), patch.object(runner, 'write_json_atomic') as write:
            result = runner.publish_once(clock=lambda: next(clock), collect=lambda: data)
            self.assertIn('discarded', result); write.assert_not_called()

    def test_no_meaningful_change_no_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'data').mkdir()
            (root / 'data/live.json').write_text(json.dumps(payload()))
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'sync_main'), patch.object(runner, 'git') as git:
                self.assertEqual(runner.publish_once(clock=lambda: at(10, 0), collect=payload), 'no meaningful change')
                git.assert_not_called()

    def test_publish_scope_is_live_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'data').mkdir()
            old = payload(); old['etfs']['sh510050']['p'][-1] = 1.0
            (root / 'data/live.json').write_text(json.dumps(old))
            (root / 'data/holdings.json').write_text('protected')
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'sync_main'), patch.object(runner, 'git') as git:
                self.assertIn('published', runner.publish_once(clock=lambda: at(10, 0), collect=payload))
                self.assertEqual(git.call_args_list[0].args, ('add', '--', 'data/live.json'))
                self.assertEqual(git.call_args_list[-1].args, ('push', 'origin', 'HEAD:main'))
                self.assertEqual((root / 'data/holdings.json').read_text(), 'protected')

    def test_unknown_local_write_not_replayed(self):
        with patch.object(runner, 'git', return_value='unconfirmed') as git:
            with self.assertRaises(runner.PublishError): runner.sync_main()
            self.assertEqual(git.call_count, 1)

    def test_workflow_keeps_one_writer_and_restart_schedule(self):
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/live.yml').read_text()
        self.assertIn('group: refresh-live-json', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn('*/5 1-7 * * 1-5', text)
        self.assertIn('python3 -B live_runner.py', text)
        self.assertNotIn('git reset --hard', text)
        self.assertNotIn('git checkout -f', text)


if __name__ == '__main__':
    unittest.main()
