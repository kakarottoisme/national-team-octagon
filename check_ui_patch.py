#!/usr/bin/env python3
"""Small UI patch gate and read-only Pages verification. Never commits or collects data."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import urllib.request

ROOT=Path(__file__).resolve().parent
REPO='kakarottoisme/national-team-octagon'
SITE='https://kakarottoisme.github.io/national-team-octagon/'
UI_FILES={'index.html','margin.html','futures-preview.html','risk-preview.html','market-cycle.html','dashboard.html','dashboard-tabs.js','dashboard-shell.css','favicon.svg','check_ui_patch.py'}
COMMON=['tests.test_dashboard_ui']
PAGE_TESTS={
    'index.html':['tests.test_chart_readout','tests.test_market_status'],
    'futures-preview.html':['tests.test_ic_strategy'],
    'margin.html':['tests.test_etf_share_flows','tests.test_etf_official_holdings'],
    'market-cycle.html':['tests.test_market_cycle','tests.test_market_factors'],
}


def command(*args):
    return subprocess.check_output(args,cwd=ROOT,text=True,stderr=subprocess.STDOUT).strip()


def allowed(path):
    if path in UI_FILES:return True
    if path in {'AGENTS.md','BACKEND.md','AUTOMATION.md'}:return True
    if path.startswith('docs/') and path.endswith('.md'):return True
    return path.startswith('tests/test_') and path.endswith('.py')


def changed_paths(base):
    # Explicit base handles both uncommitted and already-committed patches.
    changed=set(command('git','diff','--name-only',base,'--').splitlines())
    unknown=set(command('git','ls-files','--others','--exclude-standard').splitlines())
    changed.update(p for p in unknown if not p.startswith(('_backup/','.tmp-','__pycache__/')))
    return sorted(changed)


def select_tests(paths):
    modules=list(COMMON)
    for p in paths:
        modules.extend(PAGE_TESTS.get(p,[]))
        if p.startswith('tests/test_') and p.endswith('.py'):modules.append(p[:-3].replace('/','.'))
    if {'dashboard.html','dashboard-tabs.js','dashboard-shell.css'}&set(paths):
        for group in PAGE_TESTS.values():modules.extend(group)
    return list(dict.fromkeys(modules))


def protected():
    paths=list((ROOT/'data').rglob('*'))+list((ROOT/'preview-data').rglob('*'))+list((ROOT/'.github/workflows').glob('*'))
    paths+=list(ROOT.glob('*.html'))+list(ROOT.glob('*.css'))+list(ROOT.glob('*.js'))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def check(base):
    paths=changed_paths(base);blocked=[p for p in paths if not allowed(p)]
    if blocked:raise ValueError('Not a UI-only patch; use the backend/data workflow: '+', '.join(blocked))
    if not paths:print(json.dumps({'state':'no_changes'}));return 0
    modules=select_tests(paths);before=protected();start=time.monotonic()
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    with patch('urllib.request.urlopen',side_effect=AssertionError('UI regression must be offline')),patch('socket.create_connection',side_effect=AssertionError('UI regression must be offline')),patch.object(socket.socket,'connect',side_effect=AssertionError('UI regression must be offline')),patch.object(socket.socket,'connect_ex',side_effect=AssertionError('UI regression must be offline')):
        result=unittest.TextTestRunner(verbosity=1).run(suite)
    if protected()!=before:raise ValueError('Tests changed protected files')
    print(json.dumps({'state':'passed' if result.wasSuccessful() else 'failed','changed':paths,'modules':modules,'tests':result.testsRun,'elapsed_seconds':round(time.monotonic()-start,2),'production_files_preserved':True},ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


def gh_json(endpoint):
    env={k:v for k,v in os.environ.items() if k not in ('GH_TOKEN','GITHUB_TOKEN')}
    return json.loads(subprocess.check_output(['gh','api','repos/'+REPO+'/'+endpoint],cwd=ROOT,env=env,text=True))


def descendant(commit,built,get=gh_json):
    if built==commit:return True
    compare=get('compare/'+commit+'...'+built)
    return compare.get('status') in ('ahead','identical')


def verify(commit,timeout):
    commit=command('git','rev-parse',commit+'^{commit}')
    paths=command('git','diff-tree','--no-commit-id','--name-only','-r',commit).splitlines()
    files=[p for p in paths if p.endswith(('.html','.css','.js','.svg')) and p in UI_FILES]
    # A doc/checker-only patch has no visible page to render or reload.
    if not files:print(json.dumps({'state':'no_user_interface_change','commit':commit}));return 0
    expected={p:subprocess.check_output(['git','show',commit+':'+p],cwd=ROOT) for p in files}
    start=time.monotonic();deadline=start+timeout;last={};accepted=None
    while time.monotonic()<deadline:
        build=gh_json('pages/builds/latest');last={'status':build.get('status'),'commit':build.get('commit')}
        if build.get('status')=='built' and build.get('commit'):
            built=build['commit']
            if built==accepted or descendant(commit,built):
                accepted=built
                def matches(path):
                    url=SITE+path+'?release='+commit+'&check='+str(time.time_ns())
                    try:
                        with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=12) as response:return response.read()==expected[path]
                    except Exception:return False
                with ThreadPoolExecutor(max_workers=4) as pool:ok=all(pool.map(matches,files))
                if ok:
                    print(json.dumps({'state':'served_verified','commit':commit,'serving_commit':built,'files':files,'elapsed_seconds':round(time.monotonic()-start,2)},ensure_ascii=False));return 0
        time.sleep(5)
    raise ValueError('Deployment not verified before timeout: '+json.dumps(last))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    c=sub.add_parser('check');c.add_argument('--base',default='HEAD')
    v=sub.add_parser('verify');v.add_argument('--commit',default='HEAD');v.add_argument('--timeout',type=int,default=180)
    args=parser.parse_args(argv)
    return check(args.base) if args.action=='check' else verify(args.commit,args.timeout)

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print(json.dumps({'state':'blocked','reason':str(exc)},ensure_ascii=False));sys.exit(1)
