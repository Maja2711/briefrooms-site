"""EURUSD-only, deterministic central-bank risk assessment. No execution authority.

Numbers are position economics and stress scenarios, not calibrated forecasts.
All new stop/close recommendations remain SHADOW until a separately reviewed
implementation and prospective certification exist. No environment variable can
grant this module permission to execute.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import urlparse

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / 'data/investments/eurusd_cb_risk_policy.json'
DEFAULT_POLICY = {
    'schema_version': 'eurusd-cb-risk-policy-v1', 'policy_version': '1.0.0',
    'mode': 'SHADOW', 'monitoring_enabled': True, 'entry_guard_enabled': False,
    'automatic_close_enabled': False, 'stop_upgrade_enabled': False,
    'pre_event_minutes': 120, 'post_event_timeout_minutes': 240,
    'conference_cooldown_minutes': 30, 'wire_max_age_seconds': 900,
    'quote_max_age_seconds': 60, 'calendar_max_age_seconds': 86400,
    'max_spread_pips': 5.0, 'max_spread_multiple': 3.0,
    'minimum_independent_event_samples': 40,
    'weekly': {'meaningful_profit_R': 0.5, 'max_reward_to_downside': 0.75},
    'daily': {'meaningful_profit_R': 0.35, 'max_reward_to_downside': 0.75},
    'certification': {'status': 'NOT_VALIDATED', 'operator_approved': False},
}


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return None
    return result.astimezone(UTC) if result.tzinfo is not None else None


def iso(value: datetime) -> str:
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError('timezone-aware timestamp required')
    return parsed.isoformat().replace('+00:00', 'Z')


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def validate_policy(policy: Mapping[str, Any]) -> dict:
    result = json.loads(json.dumps(policy, allow_nan=False))
    if result.get('schema_version') != DEFAULT_POLICY['schema_version']:
        raise ValueError('unsupported policy')
    # Production activation is deliberately unsupported in this first PR.
    if result.get('mode') != 'SHADOW' or result.get('automatic_close_enabled') is not False or result.get('stop_upgrade_enabled') is not False:
        raise ValueError('uncertified close/protect activation forbidden')
    for key in ('monitoring_enabled', 'entry_guard_enabled'):
        if type(result.get(key)) is not bool:
            raise ValueError(f'invalid {key}')
    if result['entry_guard_enabled'] and not result['monitoring_enabled']:
        raise ValueError('entry guard requires monitoring')
    if type(result.get('minimum_independent_event_samples')) is not int:
        raise ValueError('independent sample minimum must be an integer')
    bounds = {'pre_event_minutes': (120, 240), 'post_event_timeout_minutes': (120, 720),
              'conference_cooldown_minutes': (15, 120), 'wire_max_age_seconds': (60, 1800),
              'quote_max_age_seconds': (1, 120), 'calendar_max_age_seconds': (300, 172800),
              'max_spread_pips': (0.1, 20), 'max_spread_multiple': (1, 10),
              'minimum_independent_event_samples': (40, 10000)}
    for key, (low, high) in bounds.items():
        val = number(result.get(key))
        if val is None or not low <= val <= high:
            raise ValueError(f'invalid {key}')
    for key in ('weekly', 'daily'):
        row = result.get(key, {})
        for field, low, high in [('meaningful_profit_R', .1, 3), ('max_reward_to_downside', .1, 3)]:
            val = number(row.get(field))
            if val is None or not low <= val <= high:
                raise ValueError(f'invalid {key}.{field}')
    return result


def load_policy(path: str | Path | None = None) -> dict:
    try:
        return validate_policy(json.loads(Path(path or POLICY_PATH).read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        result = json.loads(json.dumps(DEFAULT_POLICY))
        result.update(policy_status='INVALID_OR_MISSING', entry_guard_enabled=True)
        return result


def official_url(url: Any) -> bool:
    parsed = urlparse(str(url or ''))
    return parsed.scheme == 'https' and parsed.hostname in {
        'federalreserve.gov', 'www.federalreserve.gov', 'ecb.europa.eu', 'www.ecb.europa.eu'} and not parsed.username and not parsed.password


def _event_kind(row: Mapping) -> str | None:
    if row.get('bank') in ('FED', 'ECB'):
        return row['bank']
    text = ' '.join(str(row.get(k, '')) for k in ('event_type', 'title', 'headline', 'bank', 'event_id')).upper()
    if any(word in text for word in ('FOMC', 'FEDERAL OPEN MARKET', 'FED')):
        return 'FED'
    if any(word in text for word in ('ECB', 'EBC', 'EUROPEAN CENTRAL BANK')):
        return 'ECB'
    return None


def confirmed_conference_end(row: Mapping, now: datetime) -> datetime | None:
    """A dated official end remains a known fact after calendar freshness ages."""
    proof = row.get('conference_end_evidence') or {}
    ended = parse_time(row.get('conference_ended_at'))
    published, seen, received = [parse_time(proof.get(key)) for key in ('published_at','first_seen_at','ingested_at')]
    bank = _event_kind(row)
    host = urlparse(str(proof.get('source_url') or '')).hostname or ''
    if (row.get('conference_status') == 'ENDED' and proof.get('statement_verified') is True
        and official_url(proof.get('source_url')) and host.removeprefix('www.') == ('federalreserve.gov' if bank == 'FED' else 'ecb.europa.eu')
        and proof.get('event_id') == (row.get('event_id') or row.get('uid'))
        and ended and published and seen and received and ended <= published <= seen <= received <= now):
        return ended
    return None


def event_state(wire: Mapping, events: Sequence[Mapping], policy: Mapping, now: datetime) -> dict:
    """Calendar time never implies that a conference ended or a rate was released."""
    candidates = []
    window = wire.get('event_window') or {}
    # Fresh calendar polling cannot erase a previously verified end, and a
    # missing next-day calendar row cannot silently clear an unresolved event.
    identities = {}
    for row in [*events, window]:
        if not isinstance(row, Mapping):
            continue
        identity = row.get('event_id') or row.get('uid') or canonical_hash(dict(row))
        prior = identities.get(identity)
        rank = (confirmed_conference_end(row, now) is not None,
                parse_time(row.get('calendar_checked_at') or row.get('observed_at') or row.get('first_seen_at')) or datetime.min.replace(tzinfo=UTC))
        if prior is None or rank >= prior[0]:
            identities[identity] = (rank,row)
    for _, row in identities.values():
        if not isinstance(row, Mapping):
            continue
        at = parse_time(row.get('scheduled_at') or row.get('event_at'))
        bank = _event_kind(row)
        source = row.get('source_url') or row.get('source_ref') or row.get('calendar_source')
        if not source:
            source = row.get('calendar_source_url')
        # The general ECB HTML calendar can return a dated navigation menu.
        # A monetary-policy risk window needs a real Council Day-2 meeting or
        # its press conference, not any ECB-labelled page or Day-1 meeting.
        ecb_calendar = 'https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html'
        if bank == 'ECB' and (source == ecb_calendar or row.get('calendar_source_url') == ecb_calendar) and row.get('schedule_kind') != 'UNSCHEDULED_RELEASE':
            label = str(row.get('title') or row.get('event_id') or row.get('uid') or '').lower()
            if not ('governing council' in label and
                    ('(day 2)' in label or 'press conference following' in label)):
                continue
        first_seen = parse_time(row.get('first_seen_at') or row.get('observed_at') or row.get('calendar_checked_at'))
        observed = parse_time(row.get('calendar_checked_at') or row.get('observed_at') or row.get('first_seen_at'))
        calendar_fresh = bool(at and bank and official_url(source) and first_seen and first_seen <= now and observed and observed <= now
                              and (now-observed).total_seconds() <= policy['calendar_max_age_seconds'])
        event_id = row.get('event_id') or row.get('uid')
        release_verified = bool(event_id) and any(
            doc.get('statement_verified') is True and doc.get('bank') == bank
            and (doc.get('related_calendar_event_id') == event_id or
                 row.get('schedule_kind') == 'UNSCHEDULED_RELEASE' and doc.get('event_id') == row.get('release_event_id')
                 and parse_time(doc.get('published_at')) == at)
            and official_url(doc.get('source_url'))
            and (published := parse_time(doc.get('published_at'))) is not None
            and (seen := parse_time(doc.get('version_first_seen_at') or doc.get('first_seen_at'))) is not None
            and (received := parse_time(doc.get('ingested_at'))) is not None
            and published <= seen <= received <= now
            for doc in wire.get('events', ()) if isinstance(doc, Mapping))
        valid = bool(calendar_fresh and (row.get('time_precision', row.get('calendar_time_precision', 'unknown')) == 'exact' or release_verified))
        if not at or not bank:
            continue
        minutes = (at-now).total_seconds()/60
        # Keep an unconfirmed conference fail-closed after timeout until explicit
        # operator resolution, rather than silently ending a guard after 30m.
        if minutes > policy['pre_event_minutes']:
            continue
        ended_at = confirmed_conference_end(row, now)
        ended = ended_at is not None
        cooled = ended and now >= ended_at + timedelta(minutes=policy['conference_cooldown_minutes'])
        active = not cooled
        phase = ('PRE_EVENT' if minutes > 0 else
                 'ENDED' if cooled else 'POST_EVENT' if ended else
                 'PRESS_CONFERENCE' if row.get('conference_status') == 'IN_PROGRESS' else 'RELEASED' if release_verified else 'UNKNOWN')
        candidates.append(dict(event_id=row.get('event_id') or row.get('uid') or canonical_hash([bank,iso(at)]),
            bank=bank, scheduled_at=iso(at), minutes_until=minutes, event_verified=valid or ended,
            release_verified=release_verified,
            phase=phase, active=active, source_url=source,
            conference_status='UNKNOWN' if row.get('conference_status')=='ENDED' and not ended else row.get('conference_status','UNKNOWN'),
            manual_review_required=bool(active and minutes < -policy['post_event_timeout_minutes'])))
    if not candidates:
        return {'event_id': None, 'bank': None, 'phase': 'UNKNOWN', 'active': False,
                'event_verified': False, 'conference_status': 'UNKNOWN'}
    # An unresolved past event takes priority over a subsequent upcoming event.
    candidates.sort(key=lambda x: (not x['active'], x['minutes_until'] > 0, abs(x['minutes_until'])))
    selected = candidates[0]
    selected['active_event_ids'] = sorted({row['event_id'] for row in candidates if row['active']})
    return selected


@dataclass(frozen=True)
class EurusdPreEventDecision:
    event_id: str | None
    evaluated_at: datetime
    scope: Literal['EURUSD_WEEKLY', 'EURUSD_DAILY']
    position_id: str | None
    action: str
    mode: str
    quote_verified: bool
    event_verified: bool
    current_net_R: float | None
    giveback_risk_R: float | None
    hold_expected_net_R: float | None
    close_now_net_R: float | None
    hold_es95_R: float | None
    evidence_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    block_new_entry: bool
    entry_guard_enforced: bool
    risk_protection_status: str
    diagnostics: dict
    alternatives: dict
    audit: dict

    def to_dict(self) -> dict:
        value = asdict(self)
        value['evidence_ids'] = list(self.evidence_ids)
        value['reasons'] = list(self.reasons)
        value['evaluated_at'] = iso(self.evaluated_at)
        value['decision_mode'] = self.mode
        value['decision'] = self.action
        value['reason'] = list(self.reasons)
        value['immutable_id'] = self.audit['immutable_id']
        return value


def assess_pre_event(position: Mapping | None, quote: Mapping | None, wire: Mapping,
                     calendar_events: Sequence[Mapping] = (), policy: Mapping | None = None,
                     *, now: datetime, scope: str = 'EURUSD_WEEKLY',
                     historical_samples: Sequence[Mapping] = (), regime: Mapping | None = None) -> EurusdPreEventDecision:
    now = parse_time(now)
    if now is None or scope not in ('EURUSD_WEEKLY', 'EURUSD_DAILY'):
        raise ValueError('aware now and EURUSD scope required')
    p = validate_policy(policy or load_policy())
    position, quote = position or {}, quote or {}
    if position and position.get('instrument_id') != 'eurusd':
        raise ValueError('EURUSD-only assessor')
    from eurusd_central_bank_wire import validate_snapshot
    wire_valid = validate_snapshot(wire, now=now)
    wire_asof = parse_time(wire.get('as_of')) if wire_valid else None
    wire_age = (now-wire_asof).total_seconds() if wire_asof else None
    wire_fresh = bool(wire_age is not None and 0 <= wire_age <= p['wire_max_age_seconds']
                      and wire.get('overall_status') == 'FRESH')
    shared_events = [*wire.get('calendar_events',()), *wire.get('unresolved_event_windows',())] if wire_valid else []
    event = event_state(wire if wire_valid else {}, [*calendar_events,*shared_events], p, now)
    active = event['active']
    blocked = bool(active or not wire_fresh or p.get('policy_status') == 'INVALID_OR_MISSING')
    reasons = ['RISK_MODEL_UNCALIBRATED']
    if not wire_fresh:
        reasons.append('RSS_STALE' if wire_asof else 'WIRE_UNAVAILABLE_OR_INVALID')
    if event.get('manual_review_required'):
        reasons.append('RECOVERY_REQUIRED_CONFERENCE_UNCONFIRMED')
    if active:
        reasons.append('HIGH_IMPACT_EVENT_WINDOW')
    timestamp = parse_time(quote.get('observed_at'))
    age = (now-timestamp).total_seconds() if timestamp else None
    bid, ask, mid = [number(quote.get(k)) for k in ('bid', 'ask', 'mid')]
    spread = (ask-bid)*10000 if bid is not None and ask is not None and 0 < bid <= ask else None
    baseline = number(quote.get('baseline_spread_pips'))
    spike = spread/baseline if spread is not None and baseline and baseline>0 else None
    quality = bool(age is not None and 0 <= age <= p['quote_max_age_seconds'] and spread is not None
                   and .1 <= bid <= ask <= 5
                   and spread <= p['max_spread_pips'] and (spike is None or spike <= p['max_spread_multiple'])
                   and quote.get('verified') is True and quote.get('source') and quote.get('liquidity_ok', True))
    direction = str(position.get('direction','')).upper()
    sign = 1 if direction == 'LONG' else -1
    mark = bid if direction == 'LONG' else ask
    if mark is None:
        mark = mid if mid is not None and mid>0 else None
    entry, stop, target = [number(position.get(k)) for k in ('entry_price','stop_loss','take_profit')]
    initial_stop = number(position.get('initial_stop_loss')) or stop
    risk = abs(entry-initial_stop) if entry and initial_stop else None
    opened = parse_time(position.get('opened_at'))
    geometry = bool(direction in ('LONG','SHORT') and entry and stop and target and risk and risk>0
                    and all(.1 <= n <= 5 for n in (entry, stop, target, initial_stop))
                    and sign*(entry-initial_stop)>0 and sign*(stop-initial_stop)>=-1e-10
                    and sign*(target-entry)>0 and opened and opened<=now)
    cost = number(quote.get('cost_pips'))
    costs_known = cost is not None and cost >= 0
    economics = geometry and mark is not None and age is not None and 0 <= age <= p['quote_max_age_seconds']
    current_r = profit_pips = stop_distance = target_distance = giveback = None
    remaining = ratio = mfe = None
    if economics:
        profit_pips = sign*(mark-entry)*10000 - (cost if costs_known else 0)
        current_r = profit_pips/(risk*10000) if costs_known else None
        stop_distance = max(0, sign*(mark-stop)*10000)
        target_distance = max(0, sign*(target-mark)*10000)
        giveback = stop_distance/(risk*10000)
        remaining = target_distance/(risk*10000)
        ratio = remaining/giveback if giveback else None
        mfe = number(position.get('mfe_R'))
    if not quality:
        reasons.append('NO_EXECUTABLE_QUOTE')
    if not costs_known:
        reasons.append('EXECUTION_COSTS_UNKNOWN')
    if position and not geometry:
        reasons.append('POSITION_GEOMETRY_UNVERIFIED')
    action = 'NO_ACTION_UNVERIFIED' if position else ('BLOCK_NEW_ENTRY' if blocked else 'HOLD')
    profile = p['weekly' if scope.endswith('WEEKLY') else 'daily']
    if position and economics:
        if not active and quality and costs_known and wire_fresh:
            action = 'HOLD'
            reasons.append('OUTSIDE_KNOWN_EVENT_WINDOW_NO_NEW_ACTION')
        elif active and quality and costs_known and event['event_verified']:
            if current_r >= profile['meaningful_profit_R']:
                action = 'CLOSE_BEFORE_EVENT' if ratio is not None and ratio <= profile['max_reward_to_downside'] else 'PROTECT_PROFIT'
                reasons.append('PROFIT_AT_RISK_FULL_STOP_GIVEBACK')
            else:
                action = 'HOLD'
                reasons.append('LIMITED_POLICY_HOLD_UNCALIBRATED_MODEL_MANUAL_REVIEW')
    if action in ('CLOSE_BEFORE_EVENT','PROTECT_PROFIT'):
        status = 'PROTECT_PROFIT_SHADOW' if action == 'PROTECT_PROFIT' else 'CLOSE_SHADOW_NOT_EXECUTED'
    else:
        status = 'PROTECTION_PENDING' if active and (not quality or not event['event_verified']) else 'MONITORING_ONLY'
    events = [row for row in wire.get('events',[]) if isinstance(row,Mapping)] if wire_valid else []
    visible = [row for row in events if (t:=parse_time(row.get('version_first_seen_at') or row.get('first_seen_at'))) and t<=now
               and (t:=parse_time(row.get('ingested_at'))) and t<=now
               and (t:=parse_time(row.get('published_at'))) and t<=now]
    recent = [row for row in visible if (now-parse_time(row['published_at'])).total_seconds()<=1800]
    official = [row for row in visible if row.get('statement_verified')]
    latest = max(visible,key=lambda row:row.get('published_at',''),default={})
    latest_official = max(official,key=lambda row:row.get('published_at',''),default={})
    evidence = tuple(sorted({str(row.get('event_id')) for row in visible if row.get('event_id')}))
    model = {'status':'RISK_MODEL_UNCALIBRATED','effective_sample_size':0,
             'expected_net_R':None,'expected_shortfall_95_R':None,'windows':{}}
    regime = regime or {}
    regime_at = parse_time(regime.get('observed_at'))
    if quality and current_r is not None and event['bank'] and regime.get('label') and regime.get('source_ref') and regime_at and regime_at <= now:
        from eurusd_cb_event_distribution import estimate_distribution
        model = estimate_distribution(historical_samples,now=now,bank=event['bank'],
            event_type='FOMC_DECISION' if event['bank']=='FED' else 'ECB_DECISION',
            regime=str(regime['label']),direction=direction,current_net_R=current_r,
            risk_pips=risk*10000,stop_distance_pips=stop_distance,tp_distance_pips=target_distance,
            minimum_samples=int(p['minimum_independent_event_samples']))
    if model['status'] == 'EMPIRICAL_UNVALIDATED':
        reasons = [reason for reason in reasons if reason != 'RISK_MODEL_UNCALIBRATED']
        reasons.append('EMPIRICAL_UNVALIDATED_NO_EXECUTION_AUTHORITY')
    diagnostic = dict(current_event_id=event['event_id'],bank=event['bank'],phase=event['phase'],
        minutes_until=event.get('minutes_until'),conference_status=event['conference_status'],
        active_event_ids=event.get('active_event_ids',[]),
        headline_count_30m=len({row.get('dedup_key') or row.get('event_id') for row in recent}),latest_headline=latest.get('headline'),
        latest_official_document=latest_official.get('source_url'),speaker_verified=latest.get('speaker_verified',False),
        source_age_seconds=wire_age,
        wire_health=('STALE' if wire_age is not None and wire_age > p['wire_max_age_seconds'] else wire.get('overall_status')) if wire_valid else 'UNAVAILABLE',
        producer_wire_health=wire.get('overall_status') if wire_valid else 'UNAVAILABLE',
        latest_decision_headline_age_seconds=(now-parse_time(latest['published_at'])).total_seconds() if latest else None,
        position_profit_pips=profit_pips,current_net_R=current_r,profit_at_risk_pips=stop_distance,
        stop_distance_pips=stop_distance,tp_distance_pips=target_distance,unprotected_profit_pips=max(0,profit_pips) if profit_pips is not None else None,
        giveback_R=max(0,mfe-current_r) if mfe is not None and current_r is not None else None,mfe_R=mfe,
        remaining_upside_R=remaining,reward_to_downside=ratio,quote_age_seconds=age,
        current_executable_exit_price=mark if quality else None,paper_proxy_price=mark if not quality else None,
        paper_proxy_uncertain=not quality,spread_pips=spread,spread_multiple=spike,cost_pips=cost,
        risk_model_status=model['status'],effective_sample_size=model['effective_sample_size'],
        event_distribution=model,
        probability_of_profit_giveback=None,probability_SL=None,expected_shortfall_95_R=model['expected_shortfall_95_R'],
        loss_beyond_stop_scenario='GAP_AND_SLIPPAGE_CAN_EXCEED_FROZEN_STOP',input_quality='VERIFIED_QUOTE' if quality and costs_known else 'PARTIAL',
        alerts=list(dict.fromkeys([r for r in reasons if r in ('RSS_STALE','NO_EXECUTABLE_QUOTE')] +
                    (['PROFIT_AT_RISK'] if action in ('CLOSE_BEFORE_EVENT','PROTECT_PROFIT') else []) +
                    (['LIVE_SPEECH_UNVERIFIED'] if active and event['conference_status'] in ('IN_PROGRESS','UNKNOWN') else []) +
                    (['FED/ECB HIGH IMPACT IN 30M'] if active and 0<=event.get('minutes_until',999)<=30 else []))))
    alternatives = {
        'HOLD': {'expected_net_R': model['expected_net_R'],'es95_R':model['expected_shortfall_95_R'],'status':model['status'],
                 'net_R_at_current_quote':current_r,'full_stop_giveback_R':giveback,'remaining_upside_R':remaining},
        'CLOSE_NOW': {'net_R':current_r if quality else None,'executable':quality and costs_known,
                      'execution_authorized':False,'price_source':quote.get('source')},
        'PROTECT_PROFIT': {'status':'SHADOW_ONLY_LIFECYCLE_STOP_UPGRADE_NOT_CERTIFIED',
                           'proposed_stop':None,'execution_authorized':False},
    }
    audit = dict(policy_version=p['policy_version'],policy_hash=canonical_hash(p),
        data_as_of=wire.get('as_of') if wire_valid else None,first_seen_at=latest.get('first_seen_at'),
        producer_sha=wire.get('producer_sha'),consumer_sha=None,snapshot_sha256=wire.get('integrity',{}).get('snapshot_sha256'),
        executed_at=None,execution_price_source=None,execution_authorized=False,
        latency_breakdown={'source_to_seen_seconds':(parse_time(latest['first_seen_at'])-parse_time(latest['published_at'])).total_seconds() if latest else None,
            'snapshot_to_consumer_seconds':wire_age,'decision_to_execution_seconds':None})
    audit['risk_model_sample_sha256'] = model.get('sample_set_sha256')
    audit['consumer_code_sha256'] = canonical_hash({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('eurusd_central_bank_event_risk.py','eurusd_cb_event_distribution.py')})
    audit['immutable_id'] = canonical_hash([scope,position,event['event_id'],iso(now),audit['policy_hash'],audit['snapshot_sha256'],quote,model,regime,audit['consumer_code_sha256']])
    return EurusdPreEventDecision(event['event_id'],now,scope,position.get('position_id'),action,'SHADOW',quality,
        event['event_verified'],current_r,giveback,model['expected_net_R'],current_r if quality else None,model['expected_shortfall_95_R'],evidence,
        tuple(reasons),blocked,bool(blocked and p['entry_guard_enabled']),status,diagnostic,alternatives,audit)


def assess_during_event(*args, **kwargs) -> EurusdPreEventDecision:
    return assess_pre_event(*args, **kwargs)


def assess_post_event(*args, **kwargs) -> EurusdPreEventDecision:
    return assess_pre_event(*args, **kwargs)
