#!/usr/bin/env python3
"""Stage a replacement ACM site; activate only after verified HTTPS health."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src/acm'))
from acm_connection import canonical_origin, ROOT


def manager():
    spec=importlib.util.spec_from_file_location('qq_manage',REPO/'scripts/manage.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def probe(origin,transport=None):
    import httpx
    # No cookies, credentials, paid requests, redirects, or TLS exceptions.
    with httpx.Client(base_url=origin,trust_env=False,follow_redirects=False,timeout=12,transport=transport) as client:
        response=client.get('/api/health')
        response.raise_for_status()
        if response.json().get('status')!='ok':raise ValueError('ACM health is not ready')
        response=client.get('/api/auth/status')
        response.raise_for_status()
        if type(response.json().get('authenticated')) is not bool:raise ValueError('Not an ACM authentication endpoint')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['status','prepare','activate'])
    parser.add_argument('--origin')
    args=parser.parse_args()
    path=ROOT/'server.json'
    current=json.loads(path.read_text()) if path.exists() else {'schema':1,'state':'pending','target':None}
    if args.command=='status':
        print(json.dumps(current,ensure_ascii=False,indent=2));return
    origin=canonical_origin(args.origin or current.get('target'))
    m=manager()
    m.idle(retired_server=current.get('state')=='pending')
    if args.command=='prepare':
        state={'schema':1,'state':'pending','target':origin}
    else:
        if current.get('target')!=origin:raise ValueError('Stage this exact origin with prepare first')
        probe(origin)
        state={'schema':1,'state':'ready','origin':origin,'target':origin}
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    previous=path.read_bytes() if path.exists() else None
    m.atomic(path,(json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode())
    try:
        m.run(['systemctl','--user','restart',m.SERVICE])
    except Exception:
        if previous is not None:m.atomic(path,previous)
        else:path.unlink(missing_ok=True)
        m.run(['systemctl','--user','restart',m.SERVICE],check=False)
        raise
    print('ACM connection state: '+state['state'])
    if args.command=='activate':print('请在本机运行 hermes acm login 建立新站会话；旧 Cookie 不会发送。')


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('服务器尚未切换（'+type(exc).__name__+'）；请检查部署、HTTPS 和运行中任务。',file=sys.stderr)
        raise SystemExit(1)
