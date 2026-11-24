from pathlib import Path
import subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
class RiskRefreshStatus(unittest.TestCase):
    def test_global_time_and_partial_failure_are_not_futures_success(self):
        text=(ROOT/'risk-preview.html').read_text()
        code=text.split('async function refresh(){',1)[1].split('\nrefresh();',1)[0]
        code='async function refresh(){'+code
        stub="""
const assert=require('assert');let busy=false,DATA=null,cur='全部';const LAYERS=[];
const els={rb:{disabled:false,textContent:''},banner:{style:{},textContent:''},st:{textContent:''},riskFoot:{textContent:''}};
const $=id=>els[id],location={search:''};let payload={generated_at:'2026-11-24T03:48:54+00:00',metrics:[{status:'connected'}],errors:['iorb: timed out'],run_state:'partial'};
const readJSON=async path=>{assert(path.startsWith('preview-data/global.json?'));return payload};
const renderGroups=()=>{},render=()=>{},isLicensed=m=>m.status==='connected';
"""
        checks="""
(async()=>{await refresh();assert(els.st.textContent.includes('2026-11-24 11:48'));assert.equal(els.banner.style.display,'block');assert(els.banner.textContent.includes('iorb'));assert.equal(busy,false);assert.equal(els.rb.disabled,false);
payload={...payload,errors:[]};await refresh();assert.equal(els.banner.style.display,'none');
payload={...payload,generated_at:null};await refresh();assert(els.st.textContent.includes('最近采集 —'));})().catch(e=>{console.error(e);process.exit(1)});
"""
        p=subprocess.run(['node','-'],input=stub+code+checks,text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertNotIn("readJSON('preview-data/status.json",code)
if __name__=='__main__':unittest.main()
