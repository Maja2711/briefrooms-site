"""Read persisted EURUSD facts and produce a prospective SHADOW receipt only.

Does not run trading engines, fetch quotes, change positions or notify a fill.
Used by the shared wire's five-minute collector for durable Daily observation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from eurusd_central_bank_wire import load_wire
from eurusd_central_bank_event_risk import load_policy
from eurusd_daily_cb_event_adapter import safe_evaluate_daily
from eurusd_weekly_cb_event_adapter import normalize_weekly_quote
from eurusd_central_bank_event_risk import canonical_hash

ROOT=Path(__file__).resolve().parents[1]


def private_state(state):
    md=state.get('metadata') or {}
    pos=md.get('position') or {}
    return any(isinstance(row,dict) and ((row.get('execution_price_engine') or {}).get('private_saxo_primary') is True
               or row.get('redistributable') is False) for row in (state,md,pos))


def observe_daily(*,root=ROOT,now=None,wire=None):
    now=now or datetime.now(timezone.utc)
    state_path=root/'data/investments/eurusd_daily_spot.json'
    raw=state_path.read_bytes()
    state=json.loads(raw)
    if private_state(state):raise ValueError('private data cannot enter public EURUSD audit')
    if state.get('instrument') not in (None,'EUR/USD','EURUSD','eurusd'):
        raise ValueError('EURUSD-only public observer')
    position=(state.get('metadata') or {}).get('position')
    if not isinstance(position,dict) or position.get('status')!='OPEN':position=None
    try:prices=json.loads((root/'data/investments/live_prices.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):prices={}
    snapshot=wire if wire is not None else load_wire(root/'data/investments/eurusd_central_bank_wire.json',now=now)
    assessment=safe_evaluate_daily(position,[],now,wire=snapshot,
        policy=load_policy(root/'data/investments/eurusd_cb_risk_policy.json'),quote=normalize_weekly_quote(prices))
    assessment['position_state_sha256']=hashlib.sha256(raw).hexdigest()
    assessment['instrument_id']='eurusd'
    assessment['assessment_sha256']=canonical_hash(assessment)
    return assessment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=observe_daily()
    if not result.get('immutable_id'):raise ValueError('unsealed assessment')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,sort_keys=True,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:result.get(key) for key in ('action','mode','position_id','evaluated_at','risk_protection_status')}))


if __name__=='__main__':main()
