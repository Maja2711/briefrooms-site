"""Read-only WES adapter for the shared EURUSD central-bank risk contract.

This module never writes a weekly position, risk plan, event-exit request or
notification. New CLOSE/PROTECT recommendations are prospective SHADOW records.
The existing WES hard-risk authority runs before this adapter in the executor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_NAME = 'data/investments/eurusd_weekly_cb_event_risk.json'
JOURNAL_NAME = 'data/investments/eurusd_cb_audit/weekly'


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _timestamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def normalize_weekly_position(item: Mapping, week_id: str) -> dict | None:
    """Use the current frozen WES geometry, never another engine's profile."""
    if item.get('instrument_id') != 'eurusd':
        return None
    if (item.get('trade_status') != 'open' or item.get('direction') not in ('long', 'short') or not item.get('entry_price')
            or item.get('exit_price') is not None or item.get('trade_status') == 'closed'):
        return None
    plan = item.get('risk_plan') if isinstance(item.get('risk_plan'), Mapping) else {}
    identity = [week_id, 'eurusd', item.get('entry_captured_at'), item.get('entry_price'),
                item.get('entry_decision_id')]
    derived_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        'instrument_id': 'eurusd',
        'position_id': item.get('position_id') or item.get('entry_decision_id') or f'weekly:{derived_id}',
        'direction': item.get('direction'), 'entry_price': item.get('entry_price'),
        'stop_loss': plan.get('stop_loss_price'), 'take_profit': plan.get('take_profit_price'),
        'initial_stop_loss': (item.get('entry_risk_plan') or {}).get('stop_loss_price') or plan.get('stop_loss_price'),
        'opened_at': item.get('entry_captured_at'), 'mfe_R': item.get('mfe_R'),
    }


def normalize_weekly_quote(live_prices: Mapping | None) -> dict:
    values = live_prices or {}
    prices = values.get('prices') if isinstance(values.get('prices'), Mapping) else values
    quote = prices.get('eurusd') if isinstance(prices.get('eurusd'), Mapping) else {}
    # A chart close/MID is useful for SHADOW economics but is not an executable
    # BID/ASK. Never infer either side, spread or a fresh timestamp from a MID.
    return {
        'bid': quote.get('bid'), 'ask': quote.get('ask'),
        'mid': quote.get('mid') if quote.get('mid') is not None else quote.get('price'),
        'observed_at': quote.get('observed_at') or quote.get('current_price_updated_at') or quote.get('timestamp'),
        'source': quote.get('source'),
        'verified': quote.get('verified') is True and quote.get('bid') is not None and quote.get('ask') is not None,
        'baseline_spread_pips': quote.get('baseline_spread_pips'),
        'liquidity_ok': quote.get('liquidity_ok', True), 'cost_pips': quote.get('cost_pips'),
    }


def price_watchdog(live_prices: Mapping | None, now: datetime) -> dict:
    quote = normalize_weekly_quote(live_prices)
    observed = _timestamp(quote.get('observed_at'))
    age = (now.astimezone(timezone.utc) - observed).total_seconds() if observed else None
    def positive(value):
        try:
            return not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0
        except (TypeError, ValueError):
            return False
    price_fresh = age is not None and 0 <= age <= 300 and positive(quote.get('mid'))
    valid_sides = (positive(quote.get('bid')) and positive(quote.get('ask'))
        and float(quote['bid']) <= float(quote['ask']))
    execution_fresh = (age is not None and 0 <= age <= 60 and quote['verified'] is True and valid_sides)
    return {
        'instrument_id': 'eurusd', 'price_observed_at': quote.get('observed_at'),
        'price_age_seconds': age, 'fresh_for_display_mark': price_fresh,
        'fresh_for_execution': execution_fresh, 'last_known_price': quote.get('mid'),
        'status': 'FRESH' if price_fresh else 'STALE',
        'alerts': [] if price_fresh else ['EURUSD_LIVE_PRICE_STALE', 'RECOVERY_REQUIRED'],
        'recovery_required': not price_fresh,
    }


def evaluate_weekly_cb_event(
    item: Mapping, *, week_id: str, now: datetime, wire: Mapping | None = None,
    policy: Mapping | None = None, live_prices: Mapping | None = None,
    calendar_events: Sequence[Mapping] = (), root: Path = ROOT,
    historical_samples: Sequence[Mapping] | None = None, regime: Mapping | None = None,
) -> dict | None:
    """Return metadata only; unrelated instruments are an exact no-op."""
    if item.get('instrument_id') != 'eurusd':
        return None
    from eurusd_central_bank_event_risk import assess_pre_event, load_policy, validate_policy
    from eurusd_central_bank_wire import load_wire, runtime_commit
    policy = dict(policy) if policy is not None else load_policy(root / 'data/investments/eurusd_cb_risk_policy.json')
    policy = validate_policy(policy)
    if not policy.get('monitoring_enabled', True):
        return None
    wire = dict(wire) if wire is not None else load_wire(root / 'data/investments/eurusd_central_bank_wire.json', now=now)
    live_prices = live_prices if live_prices is not None else _read(root / 'data/investments/live_prices.json', {})
    position = normalize_weekly_position(item, week_id)
    from eurusd_cb_event_distribution import load_model
    historical_samples = historical_samples if historical_samples is not None else load_model(root / 'data/investments/eurusd_cb_event_distribution.json')
    decision = assess_pre_event(position, normalize_weekly_quote(live_prices), wire,
                                calendar_events=calendar_events, policy=policy, now=now,
                                scope='EURUSD_WEEKLY',historical_samples=historical_samples,regime=regime).to_dict()
    # Defense in depth: neither a future config nor a caller can turn this
    # read-only adapter into production stop/close authority.
    decision.update(mode='SHADOW', decision_mode='SHADOW', executed_at=None,
                    execution_price_source=None, execution_authority=False,
                    event_exit_request_created=False)
    decision['week_id'] = week_id
    decision['instrument_id'] = 'eurusd'
    decision['source_position_ref'] = {key: item.get(key) for key in
        ('entry_price','entry_captured_at','direction','exit_price')}
    decision['entry_guard_enabled'] = policy.get('entry_guard_enabled') is True
    decision['consumer_sha'] = runtime_commit()
    decision['audit']['consumer_sha'] = decision['consumer_sha']
    decision['consumer_code_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    decision['price_watchdog'] = price_watchdog(live_prices, now)
    decision['price_watchdog']['fresh_for_execution'] = decision.get('quote_verified') is True
    decision['risk_protection_status'] = ('PROTECT_PROFIT_SHADOW'
        if decision.get('action') == 'PROTECT_PROFIT' else decision.get('risk_protection_status'))
    return decision


def entry_fill_allowed(point: Mapping, assessment: Mapping | None, *, now: datetime,
                       root: Path = ROOT, wire: Mapping | None = None,
                       policy: Mapping | None = None) -> tuple[bool, str]:
    """Verify CB admission at the price's actual time, including recovered bars.

    A freshly recovered feed must not retroactively clear a touch observed while
    the feed was unavailable. Without a snapshot already known at that price's
    time, the untouched pending plan waits for a later prospective opportunity.
    """
    if not assessment:
        from eurusd_central_bank_event_risk import load_policy
        effective = policy if policy is not None else load_policy(root / 'data/investments/eurusd_cb_risk_policy.json')
        return (False, 'CB_ASSESSMENT_MISSING') if effective.get('entry_guard_enabled') else (True, 'ENTRY_GUARD_SHADOW')
    if assessment.get('entry_guard_enabled') is not True:
        return True, 'ENTRY_GUARD_SHADOW'
    from eurusd_central_bank_event_risk import assess_pre_event, load_policy, canonical_hash
    from eurusd_central_bank_wire import load_wire
    at = _timestamp(point.get('timestamp'))
    if at is None or at > now.astimezone(timezone.utc):
        return False, 'CB_FILL_TIME_UNVERIFIED'
    policy = dict(policy) if policy is not None else load_policy(root / 'data/investments/eurusd_cb_risk_policy.json')
    if canonical_hash(policy) != (assessment.get('audit') or {}).get('policy_hash'):
        return False, 'CB_POLICY_CHANGED_BEFORE_FILL'
    wire = dict(wire) if wire is not None else load_wire(root / 'data/investments/eurusd_central_bank_wire.json', now=now)
    as_of = _timestamp(wire.get('as_of'))
    if as_of is None or as_of > at:
        return False, 'CB_NO_POINT_IN_TIME_WIRE_FOR_FILL'
    result = assess_pre_event(None, None, wire, policy=policy, now=at, scope='EURUSD_WEEKLY')
    if result.block_new_entry:
        return False, 'CB_ENTRY_BLOCKED_AT_PRICE_TIME'
    return True, 'CB_ENTRY_AS_OF_VERIFIED'


def persist_weekly_assessment(decision: Mapping, root: Path = ROOT) -> None:
    """Append an immutable assessment, then monotonically publish latest view."""
    immutable_id = str(decision.get('immutable_id') or '')
    if len(immutable_id) != 64 or any(char not in '0123456789abcdef' for char in immutable_id):
        raise ValueError('invalid immutable risk assessment identity')
    target = root / SNAPSHOT_NAME
    journal = root / JOURNAL_NAME / f'{immutable_id}.json'
    payload = json.dumps(dict(decision), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
    journal.parent.mkdir(parents=True, exist_ok=True)
    if journal.exists():
        if journal.read_text(encoding='utf-8') != payload:
            raise ValueError('immutable risk assessment conflict')
    else:
        # Exclusive creation prevents a replay from rewriting a closed record.
        with journal.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(payload)
    current = _read(target, {})
    previous_at = _timestamp(current.get('evaluated_at'))
    next_at = _timestamp(decision.get('evaluated_at'))
    if next_at is None:
        raise ValueError('missing risk assessment time')
    if previous_at and previous_at > next_at:
        return
    temporary = target.with_suffix('.tmp')
    temporary.write_text(payload, encoding='utf-8', newline='\n')
    temporary.replace(target)


def evaluate_current_week(*, root: Path = ROOT, now: datetime | None = None,
                          persist: bool = True, week: Mapping | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    if week is None:
        # ISO week is determined in the same Warsaw timezone as WES.
        from zoneinfo import ZoneInfo
        iso_week = now.astimezone(ZoneInfo('Europe/Warsaw')).isocalendar()
        week = _read(root / f'data/investments/weekly/{iso_week.year}-W{iso_week.week:02d}.json', {})
    for item in week.get('instruments', ()):
        if not isinstance(item, Mapping) or item.get('instrument_id') != 'eurusd':
            continue
        decision = evaluate_weekly_cb_event(item, week_id=str(week.get('week_id') or ''), now=now, root=root)
        if decision is not None and persist:
            persist_weekly_assessment(decision, root=root)
        return decision
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--now')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    now = _timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error('--now requires an aware ISO timestamp')
    print(json.dumps(evaluate_current_week(now=now, persist=not args.dry_run), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
