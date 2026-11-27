#!/usr/bin/env python3
"""Ensure the existing branch-based Pages site serves the collected JSON.

GITHUB_TOKEN pushes do not trigger legacy Pages builds. Request a build only
when the permitted data files differ, then verify the public bytes. No commits,
settings changes, tokens in output, or unrelated files are involved.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
SITE='https://kakarottoisme.github.io/national-team-octagon/'
REPO='kakarottoisme/national-team-octagon'
ALLOWED={'data/ic_strategy.json','data/market_cycle.json','data/etf_share_flows.json',
         'data/etf_share_history.json','data/etf_szse_snapshot.json','data/etf_index_reference.json','data/historical_overview.json',
         'preview-data/global.json','preview-data/status.json',
         'preview-data/official/history.json','preview-data/official/snapshot-latest.json',
         'preview-data/turnover-baseline.json','preview-data/turnover-history.json',
         *[f'preview-data/official/futures/{p}.json' for p in ['IH','IF','IC','IM']]}


def served(paths,root=ROOT,read=None):
    if read is None:
        def read(url):
            with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','User-Agent':'Futures-Data-Check'}),timeout=15) as response:return response.read()
    expected={path:hashlib.sha256((Path(root)/path).read_bytes()).hexdigest() for path in paths}
    def matches(path):
        try:return hashlib.sha256(read(SITE+path+'?verify='+str(time.time_ns()))).hexdigest()==expected[path]
        except Exception:return False
    return all(matches(path) for path in paths)


def request_build():
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:raise ValueError('Pages build token missing')
    request=urllib.request.Request('https://api.github.com/repos/'+REPO+'/pages/builds',method='POST',data=b'',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','User-Agent':'Futures-Pages-Sync'})
    with urllib.request.urlopen(request,timeout=20) as response:
        if response.status!=201:raise ValueError('Pages build request not accepted')
        result=json.load(response)
    return result.get('status','queued')


def main(argv=None,check=served,request=request_build,sleep=time.sleep,clock=time.monotonic):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths',nargs='+')
    parser.add_argument('--request-build',action='store_true',help='Manual smoke test: request existing Pages build even if bytes are current')
    parser.add_argument('--timeout',type=int,default=300)
    args=parser.parse_args(argv)
    if not set(args.paths)<=ALLOWED:raise ValueError('Unapproved verification path')
    if check(args.paths) and not args.request_build:
        print(json.dumps({'state':'already_served','paths':args.paths}));return 0
    state=request();print(json.dumps({'state':'build_requested','request_status':state}))
    # A manual smoke check must give the accepted asynchronous build time to start.
    sleep(12);deadline=clock()+args.timeout
    while clock()<deadline:
        if check(args.paths):
            print(json.dumps({'state':'served_verified','paths':args.paths}));return 0
        sleep(10)
    raise ValueError('Pages not serving collected data before deadline; keep existing files and inspect build')

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print(json.dumps({'state':'publish_not_verified','reason':str(exc)}));sys.exit(1)
