"""Snapshot recovery uses immutable observations, never fills partial sessions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from backend_common import ETF_UNIVERSE, estimated_large_minutes
from backfill_index_minutes import expected_times
from index_quotes import INDEX_UNIVERSE
import restore_archive_snapshot as r

class RestoreSnapshot(unittest.TestCase):
    @staticmethod
    def sample():
        times=expected_times(1)
        rows={code:{'date':'20261120','t':times[:],'p':[2.5]*len(times),'a':[1.0,1.0001]+[0.0]*(len(times)-2),'pre':2.4} for _,code in ETF_UNIVERSE}
        values={name:estimated_large_minutes(rows[code]['a']) for name,code in ETF_UNIVERSE}
        values.update({'合计':round(sum(values.values()),2),'上证收盘':99.0,'上证涨跌幅':-1.0})
        return {'date':'20261120','updated':'2026-11-20 15:01:29','etfs':rows,'estimates':values,'sse':[['2026-11-19',0,100],['2026-11-20',0,99]],'index_minutes':{code:{'date':'20261120','t':times[:],'p':[6000.0]*len(times)} for _,code in INDEX_UNIVERSE}}

    def test_preserves_original_arrays_and_strict_threshold(self):
        source=self.sample();before=copy.deepcopy(source)
        summary,minutes=r.build(source,'2026-11-20',{'source_url':'https://example.com/preserved'})
        self.assertEqual(source,before)
        self.assertEqual(minutes['etfs'],before['etfs'])
        self.assertTrue(all(summary[name]==1.0 for name,_ in ETF_UNIVERSE))
        self.assertEqual(summary,source['estimates'])
        self.assertEqual(len(minutes['index_minutes']),7)
        self.assertTrue(all(not x['interpolated'] for x in minutes['index_minutes'].values()))

    def test_wrong_date_and_not_closed_rejected(self):
        for mutate in [lambda x:x.update(date='20261123'),lambda x:x.update(updated='2026-11-20 14:56:00'),lambda x:x['etfs']['sh510050'].update(date='20261119')]:
            source=self.sample();mutate(source)
            with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})

    def test_partial_or_invalid_amount_rejected(self):
        for value in [-1,None,float('nan'),True]:
            source=self.sample();source['etfs']['sh510050']['a'][1]=value
            with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})
        source=self.sample();source['etfs']['sh510050']['t']=source['etfs']['sh510050']['t'][:-1]
        with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})

    def test_missing_close_or_changed_summary_rejected(self):
        for mutate in [lambda x:x.update(sse=[['2026-11-19',0,100]]),lambda x:x['estimates'].update({'合计':123})]:
            source=self.sample();mutate(source)
            with self.assertRaises(ValueError):r.build(source,'2026-11-20',{})

    def test_index_partial_rejected(self):
        source=self.sample();source['index_minutes']['sh000905']['t']=source['index_minutes']['sh000905']['t'][:-1]
        with self.assertRaises(ValueError):r.build(source,'2026-11-20',{'source_url':'https://example.com/preserved'})

    def test_existing_history_preserved_idempotent_no_overwrite(self):
        summary,minutes=r.build(self.sample(),'2026-11-20',{'source_url':'https://example.com/preserved'})
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);prior={'2026-11-19':{'合计':42}};(root/'history.json').write_text(json.dumps(prior))
            self.assertTrue(r.apply_missing(root,'2026-11-20',summary,minutes))
            self.assertEqual(json.loads((root/'history.json').read_text())['2026-11-19'],prior['2026-11-19'])
            self.assertFalse(r.apply_missing(root,'2026-11-20',summary,minutes))
            with self.assertRaises(ValueError):r.apply_missing(root,'2026-11-20',{},minutes)

if __name__=='__main__':unittest.main()
