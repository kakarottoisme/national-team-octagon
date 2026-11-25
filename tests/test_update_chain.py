"""Regression tests for delayed catch-up, full archive and business-date gates."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import futures_close_update as f
import ashare_tasks as a
import archive as producer
from tests import test_archive_snapshot as snapshot_tests
from tests.test_futures_close_update import prepare, member_csv

class MemberCatchup(unittest.TestCase):
    def test_late_run_catches_all_missing_sessions_and_keeps_old(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            def fetch(url):
                product=url.rsplit('/',1)[1].split('_')[0]
                date=''.join(url.split('/')[-3:-1])
                return 200,member_csv(product,date)
            old={p:f.load(root/f'preview-data/official/futures/{p}.json')['records'] for p in f.PRODUCTS}
            result=f.run_members_catchup(root,dt.date(2026,11,24),fetch)
            self.assertEqual(result['state'],'current')
            self.assertEqual([r['date'] for r in result['days']],['2026-11-20','2026-11-23','2026-11-24'])
            f.verify_members_date(root,dt.date(2026,11,24))
            for p in f.PRODUCTS:
                self.assertEqual(f.load(root/f'preview-data/official/futures/{p}.json')['records']['2026-11-19'],old[p]['2026-11-19'])
            before={p:p.read_bytes() for p in root.rglob('*.json')}
            again=f.run_members_catchup(root,dt.date(2026,11,24),lambda u:self.fail('idempotent'))
            self.assertEqual(again['changed'],[])
            self.assertTrue(all(p.read_bytes()==b for p,b in before.items()))

    def test_member_pending_is_nonzero_even_in_morning(self):
        now=dt.datetime.fromisoformat('2026-11-25T08:01:00+08:00')
        with patch.object(f,'run_members_catchup',return_value={'state':'pending','date':'2026-11-24','changed':[]}) as run,patch('builtins.print'):
            self.assertEqual(f.main(['--mode','members'],now=now),1)
        self.assertEqual(run.call_args.args[1],dt.date(2026,11,24))

    def test_empty_verify_date_must_not_start_collection(self):
        now=dt.datetime.fromisoformat('2026-11-25T18:00:00+08:00')
        with patch.object(f,'run_members_catchup') as collect,patch('builtins.print'):
            self.assertEqual(f.main(['--mode','members','--verify-date',''],now=now),1)
        collect.assert_not_called()

    def test_partial_products_do_not_hide_missing_day(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            def fetch(url):
                product=url.rsplit('/',1)[1].split('_')[0]
                date=''.join(url.split('/')[-3:-1])
                return (404,b'') if product=='IH' else (200,member_csv(product,date))
            result=f.run_members_catchup(root,dt.date(2026,11,20),fetch)
            self.assertEqual(result['state'],'pending')
            self.assertEqual(len(result['changed']),3)
            self.assertEqual(result['missing_dates'],['2026-11-20'])
            with self.assertRaises(ValueError):f.verify_members_date(root,dt.date(2026,11,20))

    def test_newer_published_day_does_not_hide_internal_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepare(root)
            for p in f.PRODUCTS:
                path=root/f'preview-data/official/futures/{p}.json'
                data=f.load(path);data['records']['2026-11-24']={'source_status':'published'}
                path.write_text(json.dumps(data))
            self.assertEqual(f.member_missing_days(root,dt.date(2026,11,24)),['2026-11-20','2026-11-23'])
            with self.assertRaises(ValueError):f.verify_members_date(root,dt.date(2026,11,24))

class CompleteArchive(unittest.TestCase):
    def sample(self):
        import restore_archive_snapshot as r
        source=snapshot_tests.RestoreSnapshot.sample()
        return r.build(source,'2026-11-20',{'source_url':'https://example.com/source'})

    def test_missing_index_is_not_success_and_existing_archive_not_overwritten(self):
        summary,minutes=self.sample();minutes.pop('index_minutes')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data/minutes').mkdir(parents=True)
            (root/'data/history.json').write_text(json.dumps({'2026-11-20':summary}))
            p=root/'data/minutes/2026-11-20.json';p.write_text(json.dumps(minutes));before=p.read_bytes()
            with patch.object(a,'ROOT',root),patch.object(a,'verify_archive_pages') as verify,patch.object(a,'commit_files') as commit:
                with self.assertRaisesRegex(ValueError,'指数存档缺失'):a.archive(dt.datetime(2026,11,20,15,31),{})
                self.assertEqual(p.read_bytes(),before);verify.assert_not_called();commit.assert_not_called()

    def test_shared_merge_uses_closed_cache_without_fetch_and_preserves_etf(self):
        summary,minutes=self.sample();original=copy.deepcopy(minutes)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);live=snapshot_tests.RestoreSnapshot.sample()
            (root/'live.json').write_text(json.dumps(live))
            minutes.pop('index_minutes')
            result=producer.add_index_minutes(minutes,root,fetch=lambda u:self.fail('closed cache sufficient'))
            self.assertEqual(result['etfs'],original['etfs'])
            self.assertEqual(len(result['index_minutes']),7)
            producer.validate_complete(summary,result,'2026-11-20')

    def test_audit_rejects_corrupt_etf_and_missing_index_without_write(self):
        summary,minute=self.sample()
        for corrupt in ('index','etf'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);(root/'data/minutes').mkdir(parents=True)
                candidate=copy.deepcopy(minute)
                if corrupt=='index':candidate['index_minutes'].pop('sh000905')
                else:candidate['etfs']['sh510050']['a'][1]=-1
                (root/'data/history.json').write_text(json.dumps({'2026-11-20':summary}))
                (root/'data/minutes/2026-11-20.json').write_text(json.dumps(candidate))
                (root/'data/margin_balance.json').write_text('{}')
                before={p:p.read_bytes() for p in root.rglob('*.json')}
                with patch.object(a,'ROOT',root),patch.object(a,'margin_missing',return_value=[]),patch.object(a,'verify_archive_pages') as verify:
                    with self.assertRaisesRegex(ValueError,'存档不完整'):a.data_audit(dt.datetime(2026,11,20,18,5),{})
                    verify.assert_not_called()
                self.assertTrue(all(p.read_bytes()==b for p,b in before.items()))

    def test_full_audit_requires_pages_equality(self):
        summary,minute=self.sample()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data/minutes').mkdir(parents=True)
            (root/'data/history.json').write_text(json.dumps({'2026-11-20':summary}))
            (root/'data/minutes/2026-11-20.json').write_text(json.dumps(minute))
            (root/'data/margin_balance.json').write_text('{}')
            with patch.object(a,'ROOT',root),patch.object(a,'margin_missing',return_value=[]),patch.object(a,'verify_archive_pages',side_effect=ValueError('Pages不一致')):
                with self.assertRaisesRegex(ValueError,'Pages不一致'):a.data_audit(dt.datetime(2026,11,20,18,5),{})

if __name__=='__main__':unittest.main()
