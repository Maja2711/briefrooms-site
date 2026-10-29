"""CAS-publish only EURUSD wire + its immutable receipt, in one Git commit.

No trading state, workflow dispatch, notification or position write authority.
The Git Data API parent check prevents races with other canonical writers.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from eurusd_central_bank_wire import validate_snapshot, dt
from eurusd_cb_shadow_observe import private_state
from eurusd_central_bank_event_risk import canonical_hash

SNAPSHOT = 'data/investments/eurusd_central_bank_wire.json'
JOURNAL = 'data/investments/eurusd_cb_wire_journal/'


def api(repo, token, method, path, body=None):
    req=Request(f'https://api.github.com/repos/{repo}/{path}', method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json',
                 'Content-Type':'application/json','User-Agent':'BriefRooms-EURUSD-CB-Wire'})
    with urlopen(req,timeout=20) as response:return json.load(response)


def publish(snapshot, repo, token, request=api, daily_assessment=None):
    if not validate_snapshot(snapshot):raise ValueError('invalid snapshot')
    digest=snapshot['integrity']['snapshot_sha256']
    receipt=JOURNAL+digest+'.json'
    raw=json.dumps(snapshot,sort_keys=True,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    for attempt in range(6):
        head=request(repo,token,'GET','git/ref/heads/main')['object']['sha']
        commit=request(repo,token,'GET',f'git/commits/{head}')
        try:
            remote=request(repo,token,'GET',f'contents/{SNAPSHOT}?ref={head}')
            prior=json.loads(base64.b64decode(remote['content']))
            if not validate_snapshot(prior):raise ValueError('canonical wire corrupt; manual recovery required')
            if dt(prior['generated_at']) >= dt(snapshot['generated_at']):return 'skipped_newer_or_equal'
            # Do not lose or retime any previously observed publication version.
            current={e['event_id']:e for e in snapshot['events']}
            for old in prior['events']:
                dropped_old = old['event_id'] not in current and len(current)==1000 and old['ingested_at'] < min(e['ingested_at'] for e in current.values())
                if not dropped_old and (old['event_id'] not in current or current[old['event_id']] != old):
                    raise ValueError('collector must retain immutable prior event versions')
        except HTTPError as exc:
            if exc.code!=404:raise
        try:
            existing=request(repo,token,'GET',f'contents/{receipt}?ref={head}')
            if base64.b64decode(existing['content']).decode()!=raw:raise ValueError('immutable receipt conflict')
        except HTTPError as exc:
            if exc.code!=404:raise
        blob=request(repo,token,'POST','git/blobs',{'content':base64.b64encode(raw.encode()).decode(),'encoding':'base64'})['sha']
        entries=[{'path':path,'mode':'100644','type':'blob','sha':blob} for path in (SNAPSHOT,receipt)]
        if daily_assessment is not None:
            item=daily_assessment
            identity=item.get('immutable_id','')
            at=dt(item.get('evaluated_at'))
            sealed=dict(item); sealed.pop('assessment_sha256',None)
            if (item.get('mode')!='SHADOW' or item.get('instrument_id')!='eurusd' or len(identity)!=64
                or any(c not in '0123456789abcdef' for c in identity)
                or not at or not dt(snapshot['generated_at']) <= at <= datetime.now(timezone.utc)
                or (item.get('audit') or {}).get('snapshot_sha256')!=digest
                or item.get('assessment_sha256')!=canonical_hash(sealed)
                or item.get('execution_authority') is not False or item.get('executed_at') is not None
                or (item.get('audit') or {}).get('execution_authorized') is not False
                or (item.get('audit') or {}).get('executed_at') is not None):
                raise ValueError('invalid read-only Daily audit')
            remote_state=request(repo,token,'GET',f'contents/data/investments/eurusd_daily_spot.json?ref={head}')
            # If the canonical position changed during collection, omit this
            # observation rather than attaching it to a different position.
            remote_raw=base64.b64decode(remote_state['content'])
            remote_payload=json.loads(remote_raw)
            if private_state(remote_payload):raise ValueError('private canonical data forbidden in public audit')
            if remote_payload.get('instrument') not in (None,'EUR/USD','EURUSD','eurusd'):
                raise ValueError('non-EURUSD canonical data forbidden')
            remote_position=(remote_payload.get('metadata') or {}).get('position') or {}
            actual_id=remote_position.get('trade_id') or remote_position.get('position_id') if remote_position.get('status')=='OPEN' else None
            if hashlib.sha256(remote_raw).hexdigest()==item.get('position_state_sha256') and actual_id==item.get('position_id'):
                path='data/investments/eurusd_daily_cb_event_risk.json'
                newer=False
                try:
                    prior_risk=request(repo,token,'GET',f'contents/{path}?ref={head}')
                    prior_risk=json.loads(base64.b64decode(prior_risk['content']))
                    previous_at=dt(prior_risk.get('evaluated_at'))
                    newer=bool(previous_at and previous_at >= at)
                except HTTPError as exc:
                    if exc.code!=404:raise
                if not newer:
                    audit_path=f'data/investments/eurusd_cb_audit/daily/{identity}.json'
                    audit_raw=json.dumps(item,sort_keys=True,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
                    try:
                        existing=request(repo,token,'GET',f'contents/{audit_path}?ref={head}')
                        if base64.b64decode(existing['content']).decode()!=audit_raw:raise ValueError('immutable Daily audit conflict')
                    except HTTPError as exc:
                        if exc.code!=404:raise
                    audit_blob=request(repo,token,'POST','git/blobs',{'content':base64.b64encode(audit_raw.encode()).decode(),'encoding':'base64'})['sha']
                    entries.extend({'path':p,'mode':'100644','type':'blob','sha':audit_blob} for p in (path,audit_path))
        tree=request(repo,token,'POST','git/trees',{'base_tree':commit['tree']['sha'],'tree':entries})['sha']
        new=request(repo,token,'POST','git/commits',{'message':'Refresh EURUSD Fed/ECB wire (SHADOW context only)',
            'tree':tree,'parents':[head]})['sha']
        try:
            request(repo,token,'PATCH','git/refs/heads/main',{'sha':new,'force':False})
            return new
        except HTTPError as exc:
            if exc.code not in (409,422) or attempt==5:raise
    raise RuntimeError('publication race retry exhausted')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,default=Path(SNAPSHOT))
    parser.add_argument('--daily-risk',type=Path)
    args=parser.parse_args()
    repo=os.environ.get('GITHUB_REPOSITORY','')
    token=os.environ.get('GH_TOKEN','')
    if repo!='Maja2711/briefrooms-site' or not token:raise ValueError('explicit repository and token required')
    daily=json.loads(args.daily_risk.read_text(encoding='utf-8')) if args.daily_risk else None
    print(publish(json.loads(args.snapshot.read_text(encoding='utf-8')),repo,token,daily_assessment=daily))


if __name__=='__main__':main()
