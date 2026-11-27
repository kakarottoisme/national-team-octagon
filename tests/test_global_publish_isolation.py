from pathlib import Path
import subprocess, tempfile, unittest, os, json, sys
import yaml
ROOT=Path(__file__).resolve().parents[1]
class IsolatedPublishing(unittest.TestCase):
    def test_turnover_failure_does_not_skip_global_publish(self):
        w=yaml.safe_load((ROOT/'.github/workflows/preview-data.yml').read_text())
        steps=w['jobs']['refresh']['steps'];by={s['id']:s for s in steps if 'id' in s}
        for key in ['global','full','baseline','turnover']:
            self.assertTrue(by[key]['continue-on-error'])
        self.assertIn('!cancelled()',by['publish_data']['if'])
        self.assertIn('publish_data.outcome',by['pages']['if'])
        self.assertIn('--skip-global',by['full']['run'])
        self.assertIn('"${paths[@]}"',by['pages']['run'])
        # Simulate the incident: valid global data with failing unrelated turnover.
        code=by['publish_data']['run'].split('          # impossible split')[0]
        code=code[:code.index('if git diff --cached --quiet')]
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);d=root/'bin';d.mkdir()
            git=d/'git';git.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n');git.chmod(0o755)
            out=root/'outputs';calls=root/'calls'
            env=os.environ|{'PATH':str(d)+':'+os.environ['PATH'],'CALLS':str(calls),'GITHUB_OUTPUT':str(out),'GLOBAL_OUTCOME':'success','FULL_OUTCOME':'failure','BASELINE_OUTCOME':'success','TURNOVER_OUTCOME':'failure'}
            p=subprocess.run(['bash','-c',code],env=env,cwd=t,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
            used=calls.read_text();self.assertIn('global.json',used);self.assertNotIn('turnover-history.json',used);self.assertNotIn('snapshot-latest.json',used)
            self.assertIn('preview-data/global.json',out.read_text())
        # After publishing valid modules, keep the run red if any collection failed.
        final=next(s for s in steps if s['name'].startswith('Final business result'))
        p=subprocess.run(['bash','-c',final['run']],env=os.environ|{'MEMBERS_OUTCOME':'success','FULL_OUTCOME':'success','GLOBAL_OUTCOME':'success','BASELINE_OUTCOME':'success','TURNOVER_OUTCOME':'failure','PUBLISH_OUTCOME':'success','PAGES_OUTCOME':'success'})
        self.assertNotEqual(p.returncode,0)
    def test_global_path_requests_existing_pages_build(self):
        sys.path.insert(0,str(ROOT));import futures_pages_sync as sync
        calls=[];checks=iter([False,True]);times=iter([0,1])
        self.assertEqual(sync.main(['preview-data/global.json'],check=lambda p:next(checks),request=lambda:calls.append(1) or 'queued',sleep=lambda s:None,clock=lambda:next(times)),0)
        self.assertEqual(calls,[1])
    def test_frontend_polling_preserves_source_date(self):
        text=(ROOT/'risk-preview.html').read_text()
        self.assertIn('5*60*1000',text)
        self.assertIn("document.addEventListener('visibilitychange'",text)
        self.assertIn('最近检查 ',text);self.assertIn('最新源日期 ',text)
if __name__=='__main__':unittest.main()
