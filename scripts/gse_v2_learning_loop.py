from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import geopolitical_scenario_engine as gse
UTC = timezone.utc
MODE = 'shadow'
SCHEMA_VERSION = 'gse-v2-learning-loop-v1'
ENRICHED_LIBRARY_VERSION = 'gse-v2-enriched-library-v1'
CANDIDATE_VERSION = 'gse-v2-regime-candidate-v1'
CALIBRATION_VERSION = 'gse-v2-regime-calibration-v1'
HISTORICAL_REPORT_VERSION = 'gse-v2-walkforward-v1'
RESEARCH_POLICY_VERSION = 'gse-v2-fixed-research-policy-v1'
POLICY_PROPOSAL_VERSION = 'gse-v2-policy-proposal-v1'
LEARNING_LEDGER_VERSION = 'gse-v2-learning-ledger-v1'
HORIZONS_H = (24, 168, 720)
REGIME_WINDOWS = (5, 20, 60)
CORE_REGIME_ASSETS = ('SPX', 'USD', 'US10Y', 'BRENT', 'GOLD')
SOURCE_RELIABILITY_DEFAULT = 0.75
REGIME_WEIGHT = 0.7
EVENT_WEIGHT = 0.3
RECENCY_HALF_LIFE_YEARS = 12.0
MIN_PRIOR_EFFECTIVE_N = 2.0
MIN_HISTORICAL_WALKFORWARD_N = 12
MIN_HOLDOUT_N = 5
MIN_PROSPECTIVE_PROMOTION_N = 30
ACTIVE_RESEARCH_POLICY: Mapping[str, float] = {'similarity_temperature': 0.85, 'prior_strength': 8.0, 'max_overlay_weight': 0.2, 'weight_per_effective_cluster': 0.025}
POLICY_GRID: Tuple[Tuple[float, float], ...] = tuple(((temperature, prior) for temperature in (0.5, 0.7, 0.85, 1.05, 1.35) for prior in (4.0, 8.0, 12.0, 18.0)))

def parse_time(value):
    return gse.parse_time(value)

def iso_z(value):
    return gse.iso_z(value)

def stable_id(prefix, *parts):
    return gse.stable_id(prefix, *parts)

def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))

def canonical_sha256(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return default

def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)

def read_jsonl(path):
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out

def append_unique(path, rows, id_key):
    existing = {str(row.get(id_key)) for row in read_jsonl(path)}
    pending = [dict(row) for row in rows if str(row.get(id_key)) not in existing]
    if not pending:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        for row in pending:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    return len(pending)

@dataclass(frozen=True)
class DailyClose:
    timestamp: datetime
    close: float

class HistoricalMarketClient:
    def __init__(self, client=None):
        self.client = client or gse.HttpClient()
    def daily_closes(self, symbol, start, end):
        period1 = int(start.astimezone(UTC).timestamp())
        period2 = int(end.astimezone(UTC).timestamp())
        url = gse.YAHOO_CHART.format(symbol=urllib.parse.quote(symbol, safe='')) + f'?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true'
        try:
            payload = self.client.get_json(url)
            result = ((payload.get('chart') or {}).get('result') or [None])[0] or {}
            stamps = result.get('timestamp') or []
            quote = ((result.get('indicators') or {}).get('quote') or [{}])[0]
            closes = quote.get('close') or []
        except Exception:
            return []
        out: List[DailyClose] = []
        for stamp, close in zip(stamps, closes):
            try:
                value = float(close)
                ts = datetime.fromtimestamp(int(stamp), tz=UTC)
            except (TypeError, ValueError, OSError):
                continue
            if math.isfinite(value) and value > 0:
                out.append(DailyClose(ts, value))
        return sorted(out, key=lambda row: row.timestamp)

def _event_map(catalog):
    return {str(row.get('event_id')): dict(row) for row in catalog.get('events') or []}

def _catalog_projection(catalog):
    return {'schema_version': catalog.get('schema_version'), 'catalog_version': catalog.get('catalog_version'), 'events': catalog.get('events')}

def _prior_points(points, at):
    return [row for row in points if row.timestamp.date() < at.date()]

def _return_sessions(points, at, sessions):
    rows = _prior_points(points, at)
    if len(rows) <= sessions:
        return None
    return rows[-1].close / rows[-1 - sessions].close - 1.0

def _realized_vol(points, at, sessions=20):
    rows = _prior_points(points, at)
    if len(rows) <= sessions:
        return None
    sample = rows[-(sessions + 1):]
    rets: List[float] = []
    for left, right in zip(sample, sample[1:]):
        if left.close <= 0 or right.close <= 0:
            continue
        rets.append(math.log(right.close / left.close))
    if len(rets) < max(5, sessions // 2):
        return None
    return pstdev(rets) * math.sqrt(252.0)

def regime_features(histories, at):
    features: Dict[str, float] = {}
    for asset in CORE_REGIME_ASSETS:
        points = histories.get(asset) or ()
        if not points:
            continue
        for window in REGIME_WINDOWS:
            value = _return_sessions(points, at, window)
            if value is not None and math.isfinite(value):
                features[f'{asset}.ret_{window}'] = round(value, 8)
        vol = _realized_vol(points, at, 20)
        if vol is not None and math.isfinite(vol):
            features[f'{asset}.rv_20'] = round(vol, 8)
    return features
_EVENT_FEATURE_KEYS = ('severity', 'surprise', 'global_scope', 'military_relevance', 'energy_relevance', 'shipping_relevance', 'sanctions_relevance', 'food_relevance', 'china_relevance')

def _event_features(event):
    raw = event.get('features') if isinstance(event.get('features'), Mapping) else {}
    return {key: round(clamp(float(raw.get(key, 0.0))), 6) for key in _EVENT_FEATURE_KEYS}

def _scenario_features(scenario_type, scenario):
    probability = clamp(float(scenario.get('probability') or 0.0))
    confidence = clamp(float(scenario.get('confidence') or 0.0))
    acceleration = max(0.0, float(scenario.get('acceleration') or 0.0))
    base = {key: 0.0 for key in _EVENT_FEATURE_KEYS}
    base['severity'] = clamp(math.sqrt(probability * confidence))
    base['surprise'] = clamp(acceleration / 2.5)
    base['global_scope'] = 0.65
    if scenario_type == 'middle_east_energy_escalation':
        base.update(military_relevance=0.85, energy_relevance=1.0, shipping_relevance=0.45)
    elif scenario_type == 'russia_ukraine_black_sea_escalation':
        base.update(military_relevance=1.0, energy_relevance=0.55, food_relevance=0.65, global_scope=0.85)
    elif scenario_type == 'red_sea_shipping_disruption':
        base.update(military_relevance=0.65, energy_relevance=0.55, shipping_relevance=1.0)
    elif scenario_type == 'china_taiwan_trade_escalation':
        base.update(military_relevance=0.85, china_relevance=1.0, global_scope=0.9)
    elif scenario_type == 'sanctions_escalation':
        base.update(sanctions_relevance=1.0, global_scope=0.7)
    elif scenario_type == 'grain_export_disruption':
        base.update(food_relevance=1.0, shipping_relevance=0.45, global_scope=0.65)
    return {key: round(clamp(value), 6) for key, value in base.items()}

def _fetch_histories(market, start, end, assets=CORE_REGIME_ASSETS):
    out: Dict[str, List[DailyClose]] = {}
    for asset in assets:
        meta = gse.ASSETS.get(asset) or {}
        symbol = str(meta.get('symbol') or '')
        out[asset] = market.daily_closes(symbol, start, end) if symbol else []
    return out

def build_enriched_library(legacy_library, catalog, market, *, built_at):
    events = list(catalog.get('events') or [])
    if not events:
        raise ValueError('historical event catalog is empty')
    earliest = min((parse_time(row['event_at']) for row in events)) - timedelta(days=130)
    latest = max((parse_time(row['event_at']) for row in events)) + timedelta(days=45)
    histories = _fetch_histories(market, earliest, latest)
    event_by_id = _event_map(catalog)
    enriched: List[Dict[str, Any]] = []
    for source in legacy_library.get('responses') or []:
        event = event_by_id.get(str(source.get('event_id'))) or {}
        at = parse_time(source['event_at'])
        row = dict(source)
        row['event_cluster_id'] = str(event.get('event_cluster_id') or event.get('event_id') or source.get('event_id'))
        row['source_reliability'] = round(clamp(float(event.get('source_reliability', SOURCE_RELIABILITY_DEFAULT))), 6)
        row['event_features'] = _event_features(event)
        row['regime_features'] = regime_features(histories, at)
        enriched.append(row)
    projection = _catalog_projection(catalog)
    missing = sorted((asset for asset, points in histories.items() if not points))
    return {'schema_version': ENRICHED_LIBRARY_VERSION, 'mode': MODE, 'built_at': iso_z(built_at), 'catalog_version': catalog.get('catalog_version'), 'catalog_sha256': canonical_sha256(projection), 'legacy_library_sha256': canonical_sha256(legacy_library), 'point_in_time_regime_rule': 'daily_closes_strictly_before_event_calendar_date', 'regime_assets': list(CORE_REGIME_ASSETS), 'regime_windows_sessions': list(REGIME_WINDOWS), 'cluster_cap_enabled': True, 'market_outcomes_used_for_event_selection': False, 'automatic_tuning_enabled': False, 'decision_influence': False, 'responses': enriched, 'coverage': {'events': len(events), 'response_rows': len(enriched), 'regime_assets_available': len(CORE_REGIME_ASSETS) - len(missing), 'regime_assets_missing': missing, 'rows_with_regime': sum((1 for row in enriched if row.get('regime_features')))}}

def enriched_library_needs_refresh(existing, catalog, legacy_library, *, now, refresh_days):
    if existing.get('schema_version') != ENRICHED_LIBRARY_VERSION:
        return True
    if existing.get('catalog_sha256') != canonical_sha256(_catalog_projection(catalog)):
        return True
    if existing.get('legacy_library_sha256') != canonical_sha256(legacy_library):
        return True
    try:
        built = parse_time(existing['built_at'])
    except Exception:
        return True
    return built < now - timedelta(days=max(1, int(refresh_days)))

def _dedup_cluster(rows):
    best: Dict[str, Dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        cluster = str(row.get('event_cluster_id') or row.get('market_anchor_key') or row.get('event_id'))
        current = best.get(cluster)
        if current is None:
            best[cluster] = row
            continue
        left = (float(row.get('source_reliability') or 0.0), abs(float(row.get('transmission_weight') or 0.0)))
        right = (float(current.get('source_reliability') or 0.0), abs(float(current.get('transmission_weight') or 0.0)))
        if left > right:
            best[cluster] = row
    return sorted(best.values(), key=lambda row: (row.get('event_at'), row.get('event_id')))

def _eligible(rows, *, forecast_at, scenario_type=None, asset=None, horizon_hours=None, exclude_cluster=None):
    out: List[Dict[str, Any]] = []
    for source in rows:
        try:
            complete = parse_time(source['response_complete_at'])
        except Exception:
            continue
        if complete > forecast_at:
            continue
        if scenario_type is not None and str(source.get('scenario_type')) != scenario_type:
            continue
        if asset is not None and str(source.get('asset')) != asset:
            continue
        if horizon_hours is not None and int(source.get('horizon_hours') or 0) != int(horizon_hours):
            continue
        cluster = str(source.get('event_cluster_id') or source.get('market_anchor_key') or source.get('event_id'))
        if exclude_cluster and cluster == exclude_cluster:
            continue
        out.append(dict(source))
    return _dedup_cluster(out)

def _shrunk_rate(rows, alpha=2.0, beta=2.0):
    if not rows:
        return alpha / (alpha + beta)
    hits = sum((1.0 for row in rows if bool(row.get('directional_success'))))
    return (hits + alpha) / (len(rows) + alpha + beta)

def _hierarchical_prior(all_rows, *, forecast_at, scenario_type, asset, horizon_hours, exclude_cluster=None):
    asset_h = _eligible(all_rows, forecast_at=forecast_at, asset=asset, horizon_hours=horizon_hours, exclude_cluster=exclude_cluster)
    scenario_h = _eligible(all_rows, forecast_at=forecast_at, scenario_type=scenario_type, horizon_hours=horizon_hours, exclude_cluster=exclude_cluster)
    global_h = _eligible(all_rows, forecast_at=forecast_at, horizon_hours=horizon_hours, exclude_cluster=exclude_cluster)
    rates = [(0.5, _shrunk_rate(asset_h), len(asset_h)), (0.35, _shrunk_rate(scenario_h), len(scenario_h)), (0.15, _shrunk_rate(global_h), len(global_h))]
    available = [(weight, rate, n) for weight, rate, n in rates if n > 0]
    if not available:
        prior = 0.5
    else:
        total = sum((weight for weight, _, _ in available))
        prior = sum((weight * rate for weight, rate, _ in available)) / total
    return {'prior_mean': round(prior, 6), 'asset_horizon_n': len(asset_h), 'scenario_horizon_n': len(scenario_h), 'global_horizon_n': len(global_h)}

def _robust_scales(rows):
    values: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        for key, value in (row.get('regime_features') or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                values[str(key)].append(number)
    scales: Dict[str, float] = {}
    for key, series in values.items():
        if len(series) < 2:
            continue
        center = median(series)
        mad = median((abs(value - center) for value in series))
        scale = 1.4826 * mad
        if scale <= 1e-12:
            scale = pstdev(series)
        if scale > 1e-12:
            scales[key] = scale
    return scales

def _feature_distance(left, right, *, scales=None, natural_scale=0.35):
    deltas: List[float] = []
    keys = set(left) & set(right)
    for key in keys:
        try:
            lval = float(left[key])
            rval = float(right[key])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(lval) or not math.isfinite(rval):
            continue
        scale = float((scales or {}).get(key) or natural_scale)
        if scale <= 1e-12:
            continue
        delta = (lval - rval) / scale
        deltas.append(delta * delta)
    if not deltas:
        return None
    return math.sqrt(sum(deltas) / len(deltas))

def _similarity_weight(row, *, current_regime, current_event, forecast_at, scales, temperature):
    regime_distance = _feature_distance(row.get('regime_features') or {}, current_regime, scales=scales, natural_scale=1.0)
    event_distance = _feature_distance(row.get('event_features') or {}, current_event, scales=None, natural_scale=0.35)
    components: List[Tuple[float, float]] = []
    if regime_distance is not None:
        components.append((REGIME_WEIGHT, regime_distance))
    if event_distance is not None:
        components.append((EVENT_WEIGHT, event_distance))
    if components:
        total_weight = sum((weight for weight, _ in components))
        distance = sum((weight * value for weight, value in components)) / total_weight
    else:
        distance = 1.0
    kernel = math.exp(-0.5 * (distance / max(0.05, temperature)) ** 2)
    reliability = clamp(float(row.get('source_reliability') or SOURCE_RELIABILITY_DEFAULT), 0.25, 1.0)
    age_years = max(0.0, (forecast_at - parse_time(row['event_at'])).days / 365.25)
    recency = 0.5 ** (age_years / RECENCY_HALF_LIFE_YEARS)
    weight = kernel * reliability * recency
    return (weight, {'event_id': row.get('event_id'), 'event_cluster_id': row.get('event_cluster_id'), 'similarity': round(kernel, 6), 'regime_distance': None if regime_distance is None else round(regime_distance, 6), 'event_distance': None if event_distance is None else round(event_distance, 6), 'source_reliability': round(reliability, 6), 'recency_weight': round(recency, 6), 'final_weight': round(weight, 8), 'directional_success': bool(row.get('directional_success')), 'aligned_return': row.get('aligned_return')})

def _effective_n(weights):
    total = sum(weights)
    squared = sum((weight * weight for weight in weights))
    return 0.0 if squared <= 0 else total * total / squared

def posterior_for_scenario(all_rows, *, scenario_type, asset, horizon_hours, forecast_at, current_regime, current_event, temperature, prior_strength, exclude_cluster=None):
    exact = _eligible(all_rows, forecast_at=forecast_at, scenario_type=scenario_type, asset=asset, horizon_hours=horizon_hours, exclude_cluster=exclude_cluster)
    prior = _hierarchical_prior(all_rows, forecast_at=forecast_at, scenario_type=scenario_type, asset=asset, horizon_hours=horizon_hours, exclude_cluster=exclude_cluster)
    scales = _robust_scales(exact or _eligible(all_rows, forecast_at=forecast_at))
    weighted_hits = 0.0
    weight_sum = 0.0
    weights: List[float] = []
    neighbours: List[Dict[str, Any]] = []
    for row in exact:
        weight, diagnostic = _similarity_weight(row, current_regime=current_regime, current_event=current_event, forecast_at=forecast_at, scales=scales, temperature=temperature)
        if weight <= 1e-09:
            continue
        weight_sum += weight
        weights.append(weight)
        if bool(row.get('directional_success')):
            weighted_hits += weight
        neighbours.append(diagnostic)
    n_eff = _effective_n(weights)
    strength = max(1.0, float(prior_strength))
    posterior = (weighted_hits + strength * float(prior['prior_mean'])) / (weight_sum + strength)
    posterior = clamp(posterior, 0.05, 0.95)
    variance_n = max(1.0, n_eff + strength)
    se = math.sqrt(max(1e-09, posterior * (1.0 - posterior) / variance_n))
    lower = clamp(posterior - 1.645 * se, 0.0, 1.0)
    upper = clamp(posterior + 1.645 * se, 0.0, 1.0)
    uncertainty_width = upper - lower
    confidence = clamp(min(1.0, n_eff / 12.0) * (1.0 - 0.5 * uncertainty_width))
    neighbours.sort(key=lambda row: float(row['final_weight']), reverse=True)
    return {'probability_transmission_direction': round(posterior, 6), 'effective_n': round(n_eff, 4), 'raw_cluster_n': len(exact), 'weight_sum': round(weight_sum, 6), 'interval_90': [round(lower, 6), round(upper, 6)], 'epistemic_confidence': round(confidence, 6), 'prior': prior, 'neighbours': neighbours[:12]}

def _current_histories_for_forecasts(forecasts, market, *, now):
    if not forecasts:
        return {}
    earliest = min((parse_time(row['forecast_at']) for row in forecasts)) - timedelta(days=130)
    return _fetch_histories(market, earliest, now + timedelta(days=1))

def candidate_from_baseline(baseline, enriched_library, histories, *, policy=ACTIVE_RESEARCH_POLICY):
    forecast_at = parse_time(baseline['forecast_at'])
    asset = str(baseline['asset'])
    horizon = int(baseline['horizon_hours'])
    direction = int(baseline['direction'])
    p0 = clamp(float(baseline['predicted_probability']), 0.5, 0.95)
    scenarios = list(baseline.get('scenario_snapshot') or [])
    all_rows = list(enriched_library.get('responses') or [])
    current_regime = regime_features(histories, forecast_at)
    weighted: List[Tuple[float, float, Dict[str, Any]]] = []
    cluster_ids: set[str] = set()
    for scenario in scenarios:
        scenario_type = str(scenario.get('scenario_type') or '')
        impact = float(gse.TRANSMISSION_GRAPH.get(scenario_type, {}).get(asset, 0.0))
        if not impact:
            continue
        current_event = _scenario_features(scenario_type, scenario)
        posterior = posterior_for_scenario(all_rows, scenario_type=scenario_type, asset=asset, horizon_hours=horizon, forecast_at=forecast_at, current_regime=current_regime, current_event=current_event, temperature=float(policy['similarity_temperature']), prior_strength=float(policy['prior_strength']))
        p_transmission = float(posterior['probability_transmission_direction'])
        expected_direction = 1 if impact > 0 else -1
        p_for_baseline = p_transmission if expected_direction == direction else 1.0 - p_transmission
        scenario_weight = abs(impact) * clamp(float(scenario.get('probability') or 0.0)) * max(0.1, clamp(float(scenario.get('confidence') or 0.0)))
        if scenario_weight <= 0:
            continue
        for neighbour in posterior.get('neighbours') or []:
            if neighbour.get('event_cluster_id'):
                cluster_ids.add(str(neighbour['event_cluster_id']))
        diagnostic = {'scenario_type': scenario_type, 'transmission_weight': impact, 'expected_direction': expected_direction, 'probability_for_baseline_direction': round(p_for_baseline, 6), 'scenario_weight': round(scenario_weight, 8), 'current_event_features': current_event, **posterior}
        weighted.append((scenario_weight, p_for_baseline, diagnostic))
    if not weighted:
        return None
    total = sum((weight for weight, _, _ in weighted))
    analogue_probability = sum((weight * probability for weight, probability, _ in weighted)) / total if total > 0 else 0.5
    avg_confidence = sum((weight * float(diag['epistemic_confidence']) for weight, _, diag in weighted)) / total if total > 0 else 0.0
    raw_overlay = min(float(policy['max_overlay_weight']), float(policy['weight_per_effective_cluster']) * len(cluster_ids))
    overlay_weight = raw_overlay * avg_confidence
    p2 = clamp(p0 + overlay_weight * (analogue_probability - p0), 0.5, 0.85)
    policy_snapshot = {'version': RESEARCH_POLICY_VERSION, **{key: float(value) for key, value in policy.items()}}
    payload = {'schema_version': CANDIDATE_VERSION, 'candidate_id': stable_id('gse-v2-regime', baseline['forecast_id']), 'baseline_forecast_id': baseline['forecast_id'], 'baseline_batch_id': baseline.get('batch_id'), 'asset': asset, 'symbol': baseline.get('symbol'), 'forecast_at': baseline.get('forecast_at'), 'target_at': baseline.get('target_at'), 'horizon_hours': horizon, 'direction': direction, 'baseline_v1_probability': round(p0, 6), 'historical_analogue_probability': round(analogue_probability, 6), 'v2_regime_candidate_probability': round(p2, 6), 'overlay_weight': round(overlay_weight, 6), 'raw_overlay_before_uncertainty': round(raw_overlay, 6), 'epistemic_confidence': round(avg_confidence, 6), 'effective_cluster_n': len(cluster_ids), 'current_regime_features': current_regime, 'scenario_diagnostics': [diag for _, _, diag in weighted], 'research_policy': policy_snapshot, 'research_policy_sha256': canonical_sha256(policy_snapshot), 'enriched_library_sha256': canonical_sha256(enriched_library), 'mode': MODE, 'v1_forecast_modified': False, 'automatic_tuning_applied': False, 'decision_influence': False, 'belief_core_connected': False, 'trade_execution_enabled': False, 'policy_output_enabled': False}
    payload['candidate_sha256'] = canonical_sha256(payload)
    return payload

def _log_loss(probability, outcome):
    p = clamp(probability, 1e-09, 1.0 - 1e-09)
    y = 1.0 if outcome else 0.0
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))

def _metrics(predictions, key):
    usable = [row for row in predictions if row.get(key) is not None]
    if not usable:
        return {'n': 0, 'brier': None, 'log_loss': None, 'calibration_bias': None, 'hit_rate_50': None}
    probs = [clamp(float(row[key]), 0.0, 1.0) for row in usable]
    ys = [1.0 if bool(row['outcome']) else 0.0 for row in usable]
    brier = mean(((p - y) ** 2 for p, y in zip(probs, ys)))
    loss = mean((_log_loss(p, bool(y)) for p, y in zip(probs, ys)))
    bias = mean((p - y for p, y in zip(probs, ys)))
    hit = mean(((p >= 0.5) == bool(y) for p, y in zip(probs, ys)))
    return {'n': len(usable), 'brier': round(brier, 6), 'log_loss': round(loss, 6), 'calibration_bias': round(bias, 6), 'hit_rate_50': round(hit, 6)}

def historical_walkforward_predictions(enriched_library, *, temperature, prior_strength):
    rows = list(enriched_library.get('responses') or [])
    predictions: List[Dict[str, Any]] = []
    ordered = sorted(rows, key=lambda row: (row.get('event_at'), row.get('event_id'), row.get('asset')))
    for test in ordered:
        test_at = parse_time(test['event_at'])
        cluster = str(test.get('event_cluster_id') or test.get('market_anchor_key') or test.get('event_id'))
        scenario_type = str(test.get('scenario_type'))
        asset = str(test.get('asset'))
        horizon = int(test.get('horizon_hours') or 0)
        prior_exact = _eligible(rows, forecast_at=test_at, scenario_type=scenario_type, asset=asset, horizon_hours=horizon, exclude_cluster=cluster)
        if len(prior_exact) < 2:
            continue
        current_regime = test.get('regime_features') or {}
        current_event = test.get('event_features') or {}
        posterior = posterior_for_scenario(rows, scenario_type=scenario_type, asset=asset, horizon_hours=horizon, forecast_at=test_at, current_regime=current_regime, current_event=current_event, temperature=temperature, prior_strength=prior_strength, exclude_cluster=cluster)
        unweighted = _shrunk_rate(prior_exact)
        predictions.append({'event_id': test.get('event_id'), 'event_cluster_id': cluster, 'event_at': test.get('event_at'), 'scenario_type': scenario_type, 'asset': asset, 'horizon_hours': horizon, 'outcome': bool(test.get('directional_success')), 'regime_probability': posterior['probability_transmission_direction'], 'unweighted_probability': round(unweighted, 6), 'effective_n': posterior['effective_n']})
    return predictions

def _slice_metrics(predictions):
    regime = _metrics(predictions, 'regime_probability')
    plain = _metrics(predictions, 'unweighted_probability')
    return {'regime_aware': regime, 'unweighted_analogue': plain, 'delta_brier_regime_minus_unweighted': None if regime['brier'] is None or plain['brier'] is None else round(float(regime['brier']) - float(plain['brier']), 6), 'delta_log_loss_regime_minus_unweighted': None if regime['log_loss'] is None or plain['log_loss'] is None else round(float(regime['log_loss']) - float(plain['log_loss']), 6)}

def build_historical_report(enriched_library, *, policy=ACTIVE_RESEARCH_POLICY):
    predictions = historical_walkforward_predictions(enriched_library, temperature=float(policy['similarity_temperature']), prior_strength=float(policy['prior_strength']))
    by_horizon: Dict[str, Any] = {}
    by_scenario: Dict[str, Any] = {}
    by_asset: Dict[str, Any] = {}
    for horizon in HORIZONS_H:
        by_horizon[str(horizon)] = _slice_metrics([row for row in predictions if int(row['horizon_hours']) == horizon])
    scenarios = sorted({str(row['scenario_type']) for row in predictions})
    for scenario in scenarios:
        by_scenario[scenario] = _slice_metrics([row for row in predictions if row['scenario_type'] == scenario])
    assets = sorted({str(row['asset']) for row in predictions})
    for asset in assets:
        by_asset[asset] = _slice_metrics([row for row in predictions if row['asset'] == asset])
    return {'schema_version': HISTORICAL_REPORT_VERSION, 'mode': MODE, 'method': 'strict_point_in_time_walkforward_leave_event_cluster_out', 'active_research_policy': {'version': RESEARCH_POLICY_VERSION, **{key: float(value) for key, value in policy.items()}}, 'overall': _slice_metrics(predictions), 'by_horizon': by_horizon, 'by_scenario': by_scenario, 'by_asset': by_asset, 'evaluable_predictions': len(predictions), 'market_outcomes_used_for_event_selection': False, 'automatic_tuning_applied': False, 'predictions': predictions}

def _cluster_split(predictions):
    clusters: Dict[str, datetime] = {}
    for row in predictions:
        cluster = str(row['event_cluster_id'])
        at = parse_time(row['event_at'])
        clusters[cluster] = min(clusters.get(cluster, at), at)
    ordered = [key for key, _ in sorted(clusters.items(), key=lambda item: item[1])]
    if len(ordered) < 4:
        return (set(ordered), set())
    cut = max(2, min(len(ordered) - 1, math.ceil(len(ordered) * 0.7)))
    return (set(ordered[:cut]), set(ordered[cut:]))

def propose_policy(enriched_library):
    baseline_predictions = historical_walkforward_predictions(enriched_library, temperature=float(ACTIVE_RESEARCH_POLICY['similarity_temperature']), prior_strength=float(ACTIVE_RESEARCH_POLICY['prior_strength']))
    train_clusters, holdout_clusters = _cluster_split(baseline_predictions)
    if not holdout_clusters:
        return {'schema_version': POLICY_PROPOSAL_VERSION, 'status': 'insufficient_clusters', 'automatically_applied': False, 'active_policy_unchanged': True, 'candidate': None}
    scored: List[Tuple[float, float, float, Dict[str, Any]]] = []
    for temperature, prior_strength in POLICY_GRID:
        preds = historical_walkforward_predictions(enriched_library, temperature=temperature, prior_strength=prior_strength)
        train = [row for row in preds if row['event_cluster_id'] in train_clusters]
        metrics = _metrics(train, 'regime_probability')
        if int(metrics['n']) < MIN_HISTORICAL_WALKFORWARD_N or metrics['brier'] is None:
            continue
        objective = float(metrics['brier']) + 0.1 * float(metrics['log_loss'])
        scored.append((objective, temperature, prior_strength, metrics))
    if not scored:
        return {'schema_version': POLICY_PROPOSAL_VERSION, 'status': 'insufficient_training_sample', 'automatically_applied': False, 'active_policy_unchanged': True, 'candidate': None}
    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    _, temperature, prior_strength, train_metrics = scored[0]
    candidate_predictions = historical_walkforward_predictions(enriched_library, temperature=temperature, prior_strength=prior_strength)
    holdout = [row for row in candidate_predictions if row['event_cluster_id'] in holdout_clusters]
    holdout_metrics = _metrics(holdout, 'regime_probability')
    active_holdout = [row for row in baseline_predictions if row['event_cluster_id'] in holdout_clusters]
    active_metrics = _metrics(active_holdout, 'regime_probability')
    candidate = {'similarity_temperature': temperature, 'prior_strength': prior_strength, 'max_overlay_weight': float(ACTIVE_RESEARCH_POLICY['max_overlay_weight']), 'weight_per_effective_cluster': float(ACTIVE_RESEARCH_POLICY['weight_per_effective_cluster'])}
    holdout_delta = None
    if holdout_metrics['brier'] is not None and active_metrics['brier'] is not None:
        holdout_delta = round(float(holdout_metrics['brier']) - float(active_metrics['brier']), 6)
    status = 'eligible_for_human_shadow_review' if int(holdout_metrics['n']) >= MIN_HOLDOUT_N and holdout_delta is not None and (holdout_delta < 0) else 'measuring'
    proposal = {'schema_version': POLICY_PROPOSAL_VERSION, 'status': status, 'automatically_applied': False, 'active_policy_unchanged': True, 'candidate': candidate, 'train_metrics': train_metrics, 'holdout_metrics': holdout_metrics, 'active_policy_holdout_metrics': active_metrics, 'holdout_delta_brier_candidate_minus_active': holdout_delta, 'train_cluster_n': len(train_clusters), 'holdout_cluster_n': len(holdout_clusters)}
    proposal['proposal_sha256'] = canonical_sha256(proposal)
    return proposal

def generate_candidates(state_dir, enriched_library, market, *, now):
    forecasts = read_jsonl(state_dir / 'gse_forecasts.jsonl')
    if not forecasts:
        return 0
    candidate_path = state_dir / 'gse_v2_regime_forecasts.jsonl'
    existing = {str(row.get('baseline_forecast_id')) for row in read_jsonl(candidate_path)}
    pending_baselines = [row for row in forecasts if str(row.get('forecast_id')) not in existing and int(row.get('horizon_hours') or 0) in HORIZONS_H]
    if not pending_baselines:
        return 0
    histories = _current_histories_for_forecasts(pending_baselines, market, now=now)
    candidates: List[Dict[str, Any]] = []
    for baseline in pending_baselines:
        candidate = candidate_from_baseline(baseline, enriched_library, histories)
        if candidate is not None:
            candidates.append(candidate)
    return append_unique(candidate_path, candidates, 'candidate_id')

def verify_candidates(state_dir, *, now):
    candidates = read_jsonl(state_dir / 'gse_v2_regime_forecasts.jsonl')
    baseline_verifications = {str(row.get('forecast_id')): row for row in read_jsonl(state_dir / 'gse_verifications.jsonl') if row.get('forecast_id') is not None}
    out_path = state_dir / 'gse_v2_regime_verifications.jsonl'
    existing = {str(row.get('candidate_id')) for row in read_jsonl(out_path)}
    pending: List[Dict[str, Any]] = []
    for candidate in candidates:
        cid = str(candidate.get('candidate_id'))
        if cid in existing:
            continue
        verification = baseline_verifications.get(str(candidate.get('baseline_forecast_id')))
        if not verification or verification.get('outcome') not in {True, False}:
            continue
        outcome = bool(verification['outcome'])
        p1 = clamp(float(candidate['baseline_v1_probability']))
        p2 = clamp(float(candidate['v2_regime_candidate_probability']))
        row = {'schema_version': CALIBRATION_VERSION, 'verification_id': stable_id('gse-v2-regime-verification', cid), 'candidate_id': cid, 'baseline_forecast_id': candidate.get('baseline_forecast_id'), 'asset': candidate.get('asset'), 'horizon_hours': candidate.get('horizon_hours'), 'outcome': outcome, 'baseline_v1_probability': p1, 'v2_regime_candidate_probability': p2, 'brier_v1': round((p1 - (1.0 if outcome else 0.0)) ** 2, 8), 'brier_v2_regime': round((p2 - (1.0 if outcome else 0.0)) ** 2, 8), 'log_loss_v1': round(_log_loss(p1, outcome), 8), 'log_loss_v2_regime': round(_log_loss(p2, outcome), 8), 'verified_at': verification.get('verified_at') or iso_z(now), 'candidate_sha256': candidate.get('candidate_sha256'), 'research_policy_sha256': candidate.get('research_policy_sha256'), 'decision_influence': False}
        row['delta_brier_v2_minus_v1'] = round(row['brier_v2_regime'] - row['brier_v1'], 8)
        row['delta_log_loss_v2_minus_v1'] = round(row['log_loss_v2_regime'] - row['log_loss_v1'], 8)
        pending.append(row)
    return append_unique(out_path, pending, 'verification_id')

def _paired_summary(rows):
    if not rows:
        return {'paired_n': 0, 'mean_brier_v1': None, 'mean_brier_v2_regime': None, 'delta_brier_v2_minus_v1': None, 'mean_log_loss_v1': None, 'mean_log_loss_v2_regime': None, 'delta_log_loss_v2_minus_v1': None, 'calibration_bias_v2_regime': None}
    ys = [1.0 if bool(row['outcome']) else 0.0 for row in rows]
    p2s = [float(row['v2_regime_candidate_probability']) for row in rows]
    return {'paired_n': len(rows), 'mean_brier_v1': round(mean((float(row['brier_v1']) for row in rows)), 6), 'mean_brier_v2_regime': round(mean((float(row['brier_v2_regime']) for row in rows)), 6), 'delta_brier_v2_minus_v1': round(mean((float(row['delta_brier_v2_minus_v1']) for row in rows)), 6), 'mean_log_loss_v1': round(mean((float(row['log_loss_v1']) for row in rows)), 6), 'mean_log_loss_v2_regime': round(mean((float(row['log_loss_v2_regime']) for row in rows)), 6), 'delta_log_loss_v2_minus_v1': round(mean((float(row['delta_log_loss_v2_minus_v1']) for row in rows)), 6), 'calibration_bias_v2_regime': round(mean((p - y for p, y in zip(p2s, ys))), 6)}

def build_calibration(state_dir):
    rows = read_jsonl(state_dir / 'gse_v2_regime_verifications.jsonl')
    by_asset: Dict[str, Any] = {}
    by_horizon: Dict[str, Any] = {}
    by_asset_horizon: Dict[str, Any] = {}
    for asset in sorted({str(row.get('asset')) for row in rows}):
        by_asset[asset] = _paired_summary([row for row in rows if str(row.get('asset')) == asset])
    for horizon in HORIZONS_H:
        by_horizon[str(horizon)] = _paired_summary([row for row in rows if int(row.get('horizon_hours') or 0) == horizon])
    for asset in sorted({str(row.get('asset')) for row in rows}):
        for horizon in HORIZONS_H:
            slice_rows = [row for row in rows if str(row.get('asset')) == asset and int(row.get('horizon_hours') or 0) == horizon]
            if slice_rows:
                by_asset_horizon[f'{asset}|{horizon}'] = _paired_summary(slice_rows)
    overall = _paired_summary(rows)
    return {'schema_version': CALIBRATION_VERSION, 'mode': MODE, 'overall': overall, 'by_asset': by_asset, 'by_horizon': by_horizon, 'by_asset_horizon': by_asset_horizon, 'controls': {'automatic_tuning_enabled': False, 'policy_proposal_auto_apply_enabled': False, 'decision_influence': False, 'belief_core_connected': False, 'trade_execution_enabled': False, 'v1_forecast_modified': False}}

def readiness(historical_report, calibration):
    hist = (historical_report.get('overall') or {}).get('regime_aware') or {}
    hist_delta = (historical_report.get('overall') or {}).get('delta_brier_regime_minus_unweighted')
    live = calibration.get('overall') or {}
    reasons: List[str] = []
    if int(hist.get('n') or 0) < 30:
        reasons.append('historical_walkforward_n_below_30')
    if hist_delta is None or float(hist_delta) >= 0:
        reasons.append('regime_similarity_not_better_than_unweighted_history')
    if int(live.get('paired_n') or 0) < MIN_PROSPECTIVE_PROMOTION_N:
        reasons.append('prospective_paired_n_below_30')
    if live.get('delta_brier_v2_minus_v1') is None or float(live.get('delta_brier_v2_minus_v1') or 0.0) > -0.005:
        reasons.append('prospective_brier_improvement_below_gate')
    if live.get('delta_log_loss_v2_minus_v1') is None or float(live.get('delta_log_loss_v2_minus_v1') or 0.0) > 0:
        reasons.append('prospective_log_loss_not_improved')
    if live.get('calibration_bias_v2_regime') is None or abs(float(live.get('calibration_bias_v2_regime') or 0.0)) > 0.1:
        reasons.append('prospective_calibration_bias_above_0_10')
    return {'status': 'eligible_for_human_promotion_review' if not reasons else 'shadow_learning', 'automatic_promotion': False, 'reasons': reasons}

def _validate_ledger(rows):
    previous = None
    for row in rows:
        if row.get('previous_hash') != previous:
            raise ValueError('GSE v2 learning ledger chain is broken')
        expected = canonical_sha256({key: value for key, value in row.items() if key != 'record_hash'})
        if str(row.get('record_hash')) != expected:
            raise ValueError('GSE v2 learning ledger record hash mismatch')
        previous = row.get('record_hash')

def append_learning_ledger(state_dir, *, now, enriched_library, historical_report, policy_proposal, calibration, candidates_added, verifications_added):
    path = state_dir / 'gse_v2_learning_ledger.jsonl'
    rows = read_jsonl(path)
    _validate_ledger(rows)
    previous = rows[-1]['record_hash'] if rows else None
    record = {'schema_version': LEARNING_LEDGER_VERSION, 'ledger_id': stable_id('gse-v2-learning-ledger', iso_z(now)), 'recorded_at': iso_z(now), 'previous_hash': previous, 'enriched_library_sha256': canonical_sha256(enriched_library), 'historical_report_sha256': canonical_sha256(historical_report), 'policy_proposal_sha256': canonical_sha256(policy_proposal), 'prospective_calibration_sha256': canonical_sha256(calibration), 'candidates_added': int(candidates_added), 'verifications_added': int(verifications_added), 'automatic_tuning_applied': False, 'decision_influence': False}
    record['record_hash'] = canonical_sha256(record)
    append_unique(path, [record], 'ledger_id')

def run(state_dir, catalog_path, *, now=None, refresh_days=7, market=None):
    now = (now or datetime.now(UTC)).astimezone(UTC)
    market = market or HistoricalMarketClient()
    catalog = read_json(catalog_path, {})
    if not catalog.get('events'):
        raise ValueError('GSE v2 historical catalogue is unavailable')
    legacy_path = state_dir / 'gse_historical_analogue_library.json'
    legacy = read_json(legacy_path, {})
    if not legacy.get('responses'):
        raise ValueError('legacy GSE v2 analogue library is unavailable')
    enriched_path = state_dir / 'gse_v2_enriched_library.json'
    enriched = read_json(enriched_path, {})
    if enriched_library_needs_refresh(enriched, catalog, legacy, now=now, refresh_days=refresh_days):
        enriched = build_enriched_library(legacy, catalog, market, built_at=now)
        write_json(enriched_path, enriched)
    historical_report = build_historical_report(enriched)
    policy_proposal = propose_policy(enriched)
    write_json(state_dir / 'gse_v2_historical_walkforward.json', historical_report)
    write_json(state_dir / 'gse_v2_policy_proposal.json', policy_proposal)
    candidates_added = generate_candidates(state_dir, enriched, market, now=now)
    verifications_added = verify_candidates(state_dir, now=now)
    calibration = build_calibration(state_dir)
    write_json(state_dir / 'gse_v2_regime_calibration.json', calibration)
    gate = readiness(historical_report, calibration)
    state = {'schema_version': SCHEMA_VERSION, 'mode': MODE, 'updated_at': iso_z(now), 'active_research_policy': {'version': RESEARCH_POLICY_VERSION, **{key: float(value) for key, value in ACTIVE_RESEARCH_POLICY.items()}}, 'policy_proposal': policy_proposal, 'historical': {'evaluable_walkforward_n': historical_report.get('evaluable_predictions'), 'overall': historical_report.get('overall')}, 'prospective': calibration.get('overall'), 'readiness': gate, 'controls': {'automatic_tuning_enabled': False, 'policy_proposal_auto_apply_enabled': False, 'automatic_promotion_enabled': False, 'decision_engine_connected': False, 'belief_core_connected': False, 'trade_execution_enabled': False, 'policy_output_enabled': False, 'v1_forecast_modified': False}, 'last_cycle': {'candidates_added': candidates_added, 'verifications_added': verifications_added, 'enriched_library_refreshed_at': enriched.get('built_at')}}
    write_json(state_dir / 'gse_v2_learning_state.json', state)
    append_learning_ledger(state_dir, now=now, enriched_library=enriched, historical_report=historical_report, policy_proposal=policy_proposal, calibration=calibration, candidates_added=candidates_added, verifications_added=verifications_added)
    return state

def main():
    parser = argparse.ArgumentParser(description='Run advanced GSE v2 historical learning loop')
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--refresh-days', type=int, default=7)
    args = parser.parse_args()
    state = run(Path(args.state_dir), Path(args.catalog), refresh_days=args.refresh_days)
    print(json.dumps({'mode': state['mode'], 'readiness': state['readiness'], 'last_cycle': state['last_cycle'], 'prospective': state['prospective']}, ensure_ascii=False, sort_keys=True))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
