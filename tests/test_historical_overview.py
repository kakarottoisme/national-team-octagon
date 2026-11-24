import copy
import json
import subprocess
import unittest
from pathlib import Path

import backfill_historical_overview as b
ROOT=Path(__file__).resolve().parents[1]


class HistoricalOverviewTests(unittest.TestCase):
    def test_all_recovered_rows_independently_match_original_git_data(self):
        rows=json.loads((ROOT/'data/historical_overview.json').read_text())['days']
        self.assertGreaterEqual(len(rows),10)
        for day,row in rows.items():
            s=row['source'];raw=b.git('show',s['snapshot_commit']+':data/live.json');doc=json.loads(raw)
            baseline=json.loads(b.git('show',s['baseline_commit']+':preview-data/turnover-baseline.json'))
            self.assertTrue(b.complete_close(doc,day));self.assertTrue(b.valid_base(baseline,day))
            amounts=[doc['etfs'][c]['amt'] for c in b.CODES]
            ratios=[]
            for c in b.CODES:
                q=doc['etfs'][c]
                ratios.append(round((q['ob']-q['os'])/(q['ob']+q['os'])*q['amt'],3))
            self.assertAlmostEqual(round(sum(ratios),3),row['net_buy_yi'],places=3)
            den=sum(baseline['codes'][c]['avg_amt_yi'] for c in b.CODES)
            self.assertAlmostEqual(sum(amounts)/den,row['same_time_ratio'],places=12)
            import hashlib
            self.assertEqual(hashlib.sha256(raw).hexdigest(),s['snapshot_sha256'])

    def test_future_baseline_and_partial_day_are_rejected(self):
        rows=json.loads((ROOT/'data/historical_overview.json').read_text())['days'];d=next(iter(rows));s=rows[d]['source']
        doc=json.loads(b.git('show',s['snapshot_commit']+':data/live.json'));base=json.loads(b.git('show',s['baseline_commit']+':preview-data/turnover-baseline.json'))
        base['codes'][b.CODES[0]]['last_day']=d;self.assertFalse(b.valid_base(base,d))
        doc['etfs'][b.CODES[0]]['t'][-1]='1459';self.assertFalse(b.complete_close(doc,d))

    def test_net_missing_is_not_zero(self):
        rows=json.loads((ROOT/'data/historical_overview.json').read_text())['days'];s=next(iter(rows.values()))['source']
        doc=json.loads(b.git('show',s['snapshot_commit']+':data/live.json'))
        doc['etfs'][b.CODES[0]]['net_buy']=None
        self.assertIsNone(b.build_close(doc)['net_buy_yi'])

    def test_history_ui_switches_without_leaking_live_or_old_values(self):
        src=(ROOT/'index.html').read_text();func=src[src.index('function historicalKpi('):src.index('async function loadBaseline')];kpi=src[src.index('function kpi('):src.index('let historyExpanded=')]
        stub=r'''
const assert=require('assert');const uiNumber=v=>v==null||v===''?null:(Number.isFinite(+v)?+v:null);
const E=Array.from({length:7},(_,i)=>['ETF'+i,'code'+i]);let HIST_MINUTES=null,HIST_OVERVIEW={'2026-11-23':{net_buy_yi:36.715,same_time_ratio:.92,total_amt_yi:496.75,same_time_den_yi:540}},view='2026-11-23',LIVE={etfs:Object.fromEntries(E.map(e=>[e[1],{net_buy:999,amt:100,t:['1500']}]))},BASE=null,TODAY='2026-11-24',H={'2026-11-23':{'合计':99.1},'2026-11-02':{'合计':296.56}},ARCHIVED=new Set();
const ids={},document={getElementById:id=>ids[id]||(ids[id]={textContent:'',innerHTML:''})};function sameTime(){throw Error('historical KPI must not use current baseline')}
'''
        checks=r'''
kpi('2026-11-23');assert(ids.kC.innerHTML.includes('+36.7'));assert(ids.kB.innerHTML.includes('0.92'));assert(ids.kCf.textContent.includes('非净流入'));
view='2026-11-02';kpi(view);assert.equal(ids.kC.innerHTML,'—');assert.equal(ids.kB.innerHTML,'—');assert.equal(ids.kCf.textContent,'');assert.equal(ids.kBf.textContent,'');
HIST_MINUTES={date:'2026-11-23',etfs:Object.fromEntries(E.map(e=>[e[1],{net_buy:1}]))};assert.equal(historicalKpi('2026-11-02').net,null);assert.equal(historicalKpi('2026-11-23').net,7);
'''
        run=subprocess.run(['node','-'],input=stub+func+kpi+checks,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr)


if __name__=='__main__':unittest.main()
