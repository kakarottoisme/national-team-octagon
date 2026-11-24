"""Calendar observation ranges; node runs only the page's pure date helper."""
from pathlib import Path
import json,re,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'margin.html').read_text()
HELPER=SOURCE[SOURCE.index('function observationWindow('):SOURCE.index('let marginData=null')]
class ObservationRanges(unittest.TestCase):
    def run_window(self,dates,span,end=None):
        args=[dates,span]+([end] if end else [])
        code=HELPER+'\nconsole.log(JSON.stringify(observationWindow(...'+json.dumps(args)+')));'
        return json.loads(subprocess.run(['node','-'],input=code,text=True,capture_output=True,check=True).stdout)
    def test_matching_five_choices(self):
        for attr in ['data-flow-window','data-range']:
            self.assertEqual(re.findall(attr+r'="([^"]+)"',SOURCE),['30','90','1','3Y','ALL'])
        for text in ['最近30天','最近90天','最近1天','最近3年','全部']:
            self.assertEqual(SOURCE.count('>'+text+'</button>'),2)
    def test_one_day_ends_at_latest_not_request_date(self):
        self.assertEqual(self.run_window(['2026-11-20','2026-11-23'],1),{'dates':['2026-11-23'],'offset':1})
    def test_calendar_days_not_rows(self):
        dates=['2026-10-24','2026-10-25','2026-10-26','2026-11-20','2026-11-23']
        self.assertEqual(self.run_window(dates,30)['dates'],dates[1:])
    def test_90_day_boundary(self):
        dates=['2026-08-25','2026-08-26','2026-11-23']
        self.assertEqual(self.run_window(dates,90)['dates'],dates[1:])
    def test_three_year_boundary(self):
        dates=['2023-11-22','2023-11-23','2024-11-23','2026-11-23']
        self.assertEqual(self.run_window(dates,'3Y')['dates'],dates[1:])
    def test_leap_year_clamps_february(self):
        dates=['2021-02-27','2021-02-28','2021-03-01','2024-02-29']
        self.assertEqual(self.run_window(dates,'3Y')['dates'],dates[1:])
    def test_empty_and_all(self):
        self.assertEqual(self.run_window([],30),{'dates':[],'offset':0})
        self.assertEqual(self.run_window(['2020-01-01','2026-11-23'],'ALL')['offset'],0)
    def test_section_dates_remain_independent(self):
        dates=['2026-11-23','2026-11-24']
        self.assertEqual(self.run_window(dates,1,'2026-11-23')['dates'],dates[:1])
        self.assertIn('observationWindow(pack.dates||[],span)',SOURCE)
    def test_defaults_and_restore(self):
        self.assertIn('marginRange=90',SOURCE)
        self.assertIn('span=90',SOURCE)
        self.assertIn("['ALL','3Y','1','30','90'].includes(q)",SOURCE)
        self.assertIn("['1','30','90'].includes(w)",SOURCE)
        self.assertIn("['ALL','3Y'].includes(w)",SOURCE)
    def test_single_day_points_and_all_scripts_parse(self):
        self.assertIn('showSymbol:slice.dates.length===1',SOURCE)
        self.assertIn('showSymbol:marginDates.length===1',SOURCE)
        for script in re.findall(r'<script>(.*?)</script>',SOURCE,re.S):
            result=subprocess.run(['node','--check','-'],input=script,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main()
