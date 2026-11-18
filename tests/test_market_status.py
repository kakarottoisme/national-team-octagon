"""Market-state and chart freshness wording; no real clock/source dependency."""
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]
class MarketStatusTests(unittest.TestCase):
    def test_calendar_boundaries_freshness_and_history(self):
        source=(ROOT/'index.html').read_text()
        code=source[source.index('const MARKET_HOLIDAYS_2026='):source.index('async function j(u)')]
        setup="""
const assert=require('assert');let box={innerHTML:''};const document={getElementById(){return box}},uiEsc=v=>String(v),bj=()=>new Date(2026,10,18,14,45);
const at=(m,d,h,n)=>new Date(2026,m-1,d,h,n);
"""
        checks="""
assert.equal(marketState(at(11,18,9,14)),'未开盘');assert.equal(marketState(at(11,18,9,15)),'集合竞价');assert.equal(marketState(at(11,18,9,30)),'交易中');assert.equal(marketState(at(11,18,11,30)),'休市（午休）');assert.equal(marketState(at(11,18,13,0)),'交易中');assert.equal(marketState(at(11,18,14,57)),'集合竞价');assert.equal(marketState(at(11,18,15,0)),'休市（已收盘）');
assert.equal(marketState(at(2,20,10,0)),'休市');assert.equal(marketState(at(2,24,10,0)),'交易中');assert.equal(marketState(at(10,7,10,0)),'休市');assert.equal(marketState(at(11,21,10,0)),'休市');assert.equal(marketState(new Date(2027,0,4,10)),'市场状态待确认');
assert.equal(chartUpdatedText('2026-11-18 14:43:55','2026-11-18'),'图表更新 14:43:55');assert.equal(chartUpdatedText('2026-11-17 15:05:00','2026-11-18'),'图表更新 2026-11-17 15:05:00');assert.equal(chartUpdatedText(null,'2026-11-18'),'图表更新时间未知');
chartStatusSnapshot={view:'live',updated:'2026-11-18 14:43:55'};renderMarketStatus();assert(box.innerHTML.includes('交易中'));assert(box.innerHTML.includes('图表更新 14:43:55'));assert(box.innerHTML.includes('盘中约5分钟更新'));assert(!box.innerHTML.includes('快照'));renderMarketStatus(at(11,18,15,1));assert(box.innerHTML.includes('已收盘'));assert(box.innerHTML.includes('14:43:55'));
chartStatusSnapshot={view:'2026-11-17'};renderMarketStatus();assert(box.innerHTML.includes('历史图表 2026-11-17'));assert(box.innerHTML.includes('不自动更新'));assert(!box.innerHTML.includes('约5分钟'));
"""
        result=subprocess.run(['node','-e',setup+code+checks],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_no_technical_status_or_refresh_time_relabel(self):
        source=(ROOT/'index.html').read_text()
        self.assertNotIn('最早截止',source)
        self.assertNotIn('非节假日校验',source)
        self.assertIn("chartStatusSnapshot={view:'live',updated:LIVE.updated}",source)
        self.assertIn('setInterval(()=>{if(!document.hidden)renderMarketStatus()},15000)',source)
if __name__=='__main__':unittest.main()
