#!/usr/bin/env python3
"""BriefRooms Market Relationship / Trigger Engine.

Central object:
    EVENT x ENTITY x PEERS x MARKET_REACTION x TIME

The engine is deliberately an attention allocator, not a trading engine. It
consumes the already-cheap Stock Trading v2 Opportunity Frontier and the shared
Event Intelligence snapshot, then identifies which relationships deserve scarce
deep-research capacity.

V1 is shadow-only:
- it cannot open/close positions,
- it cannot mutate production policy,
- its attention queue is counterfactual,
- it freezes trigger observations so lead/lag and follow-through can later be
  settled without hindsight.

Deep Entity BELIEF is therefore lazy by contract: the engine emits a small
deep_belief_queue only for unusually strong event/reaction/peer clusters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_discovery as discovery
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_discovery as discovery

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/market_relationship_trigger_config.json"
FRONTIER_ROOT = ROOT / "data/investments/stock_trading_v2_frontier"
DEFAULT_EVENT_PATH = ROOT / "data/investments/event_intelligence.json"
OUTPUT_ROOT = ROOT / "data/investments/market_relationship_trigger"
HISTORY_ROOT = ROOT / "data/investments/market_relationship_trigger_history"

SCHEMA_VERSION = "briefrooms-market-relationship-trigger-v1"
HISTORY_SCHEMA_VERSION = "briefrooms-market-relationship-observation-v1"


class TriggerEngineError(RuntimeError):
    pass


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())


def _sign(value: float | None, *, epsilon: float = 0.003) -> int:
    if value is None or abs(value) < epsilon:
        return 0
    return 1 if value > 0 else -1


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = _read_json(path)
    if not isinstance(config, dict) or config.get("schema_version") != "briefrooms-market-relationship-trigger-config-v1":
        raise contracts.ContractError("market relationship trigger config schema mismatch")
    weights = config.get("weights") or {}
    if abs(sum(float(value) for value in weights.values()) - 100.0) > 1e-9:
        raise contracts.ContractError("market relationship trigger weights must sum to 100")
    governance = config.get("governance") or {}
    forbidden = (
        "production_decision_influence",
        "automatic_portfolio_admission",
        "automatic_policy_writeback",
        "deep_belief_execution",
    )
    if any(governance.get(key) is not False for key in forbidden):
        raise contracts.ContractError("market relationship trigger must remain shadow-only")
    if governance.get("attention_queue_is_counterfactual_only") is not True:
        raise contracts.ContractError("attention queue must remain counterfactual")
    return config


THEME_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("semiconductor", ("semiconductor", "chip", "gpu", "wafer", "foundry")),
    ("ai_market", ("artificial intelligence", " ai ", "machine learning", "cloud", "software", "data center", "datacenter")),
    ("crypto_market", ("crypto", "bitcoin", "ethereum", "blockchain", "stablecoin", "digital asset")),
    ("energy", ("energy", "oil", "gas", "petroleum", "pipeline", "refining")),
    ("defense", ("defense", "defence", "aerospace", "missile", "military")),
    ("financial", ("bank", "financial", "payments", "capital markets", "insurance")),
    ("consumer_tech", ("consumer electronics", "smartphone", "devices", "internet content")),
    ("healthcare", ("health care", "healthcare", "biotech", "pharma", "diagnostic", "medical")),
)

SCENARIO_TO_THEMES: Mapping[str, tuple[str, ...]] = {
    "china_taiwan": ("semiconductor", "ai_market"),
    "trade_sanctions": ("semiconductor", "ai_market", "energy", "financial"),
    "energy_supply": ("energy",),
    "middle_east": ("energy", "defense"),
    "russia_ukraine": ("defense", "energy"),
}


def candidate_themes(candidate: Mapping[str, Any]) -> set[str]:
    explicit = {
        _norm(value).replace(" ", "_")
        for value in candidate.get("themes") or []
        if str(value or "").strip()
    }
    text = f" {_norm(candidate.get('sector'))} {_norm(candidate.get('industry'))} {_norm(candidate.get('name'))} "
    themes = set(explicit)
    for theme, markers in THEME_RULES:
        if any(marker in text for marker in markers):
            themes.add(theme)
    symbol = str(candidate.get("symbol") or "").upper()
    if symbol in {"COIN", "MSTR", "CRCL", "RIOT", "MARA", "CLSK", "HUT", "IREN"}:
        themes.add("crypto_market")
    return themes


def event_themes(event: Mapping[str, Any]) -> set[str]:
    themes: set[str] = set()
    for raw in event.get("entity_themes") or []:
        value = _norm(raw).replace(" ", "_")
        if value:
            themes.add(value)
    for raw in event.get("scenario_tags") or []:
        tag = _norm(raw).replace(" ", "_")
        themes.update(SCENARIO_TO_THEMES.get(tag, ()))
        if tag.startswith("theme:"):
            themes.add(tag.split(":", 1)[1])
    return themes


def _event_direction(event: Mapping[str, Any], relation: str) -> int:
    if relation == "direct":
        value = _finite(event.get("direct_impact"))
    elif relation == "theme":
        value = _finite(event.get("sector_impact"))
        if value is None or abs(value) < 1e-12:
            value = _finite(event.get("direct_impact"))
    else:
        value = _finite(event.get("index_impact"))
    return _sign(value, epsilon=0.02)


def relevant_events(
    candidate: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    symbol = str(candidate.get("symbol") or "").upper()
    c_themes = candidate_themes(candidate)
    event_cfg = config.get("event") or {}
    max_age = float(event_cfg.get("maximum_age_hours") or 48.0)
    multipliers = {
        "direct": float(event_cfg.get("direct_relevance_multiplier") or 1.0),
        "theme": float(event_cfg.get("theme_relevance_multiplier") or 0.72),
        "macro": float(event_cfg.get("macro_relevance_multiplier") or 0.35),
    }
    out: list[dict[str, Any]] = []
    for raw in events:
        event = dict(raw)
        published = _parse_time(event.get("published_at"))
        if published is None:
            continue
        age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
        if age_hours > max_age:
            continue

        entity_symbols = {str(value).upper() for value in event.get("entity_symbols") or []}
        e_themes = event_themes(event)
        scenario_tags = {_norm(value).replace(" ", "_") for value in event.get("scenario_tags") or []}

        relation = ""
        overlap: set[str] = set()
        if symbol and symbol in entity_symbols:
            relation = "direct"
        else:
            overlap = c_themes & e_themes
            if overlap:
                relation = "theme"
            else:
                scenario_relevant = {
                    tag
                    for tag in scenario_tags
                    if c_themes & set(SCENARIO_TO_THEMES.get(tag, ()))
                }
                if scenario_relevant:
                    relation = "macro"
                    overlap = scenario_relevant
        if not relation:
            continue

        strength = _finite(event.get("event_strength"))
        confidence = _finite(event.get("confidence"))
        severity = _finite(event.get("severity"))
        reliability = _finite(event.get("source_reliability"))
        if strength is None:
            strength = (severity or 0.0) * (confidence or 0.0)
        raw_score = 100.0 * (
            0.35 * _clamp(strength or 0.0, 0.0, 1.0)
            + 0.25 * _clamp(confidence or 0.0, 0.0, 1.0)
            + 0.25 * _clamp(severity or 0.0, 0.0, 1.0)
            + 0.15 * _clamp(reliability or 0.0, 0.0, 1.0)
        )
        relevance_score = _clamp(raw_score * multipliers[relation])
        out.append(
            {
                "event_id": event.get("event_id"),
                "title": event.get("title"),
                "event_domain": event.get("event_domain"),
                "event_kind": event.get("event_kind") or event.get("event_type"),
                "published_at": event.get("published_at"),
                "age_hours": round(age_hours, 4),
                "relation": relation,
                "theme_overlap": sorted(overlap),
                "direction": _event_direction(event, relation),
                "relevance_score": round(relevance_score, 6),
                "confidence": confidence,
                "event_strength": strength,
                "source_reliability": reliability,
                "entity_symbols": sorted(entity_symbols),
            }
        )
    return sorted(out, key=lambda row: (float(row["relevance_score"]), -float(row["age_hours"])), reverse=True)


def _return(candidate: Mapping[str, Any], horizon: int) -> float | None:
    return _finite(((candidate.get("features") or {}).get("returns") or {}).get(str(horizon)))


def candidate_direction(candidate: Mapping[str, Any]) -> int:
    for horizon, epsilon in ((1, 0.003), (5, 0.008), (20, 0.015)):
        direction = _sign(_return(candidate, horizon), epsilon=epsilon)
        if direction:
            return direction
    return 0


def _behavior_vector(candidate: Mapping[str, Any]) -> list[float]:
    return [float(_return(candidate, horizon) or 0.0) for horizon in (1, 5, 20, 60)]


def behavior_similarity(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    va, vb = _behavior_vector(a), _behavior_vector(b)
    na = math.sqrt(sum(value * value for value in va))
    nb = math.sqrt(sum(value * value for value in vb))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    cosine = sum(x * y for x, y in zip(va, vb)) / (na * nb)
    return _clamp((cosine + 1.0) * 50.0) / 100.0


def peer_rows(
    candidate: Mapping[str, Any],
    all_candidates: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    market_cfg = (config.get("markets") or {}).get("US") or {}
    max_peers = int(market_cfg.get("maximum_peers") or 8)
    minimum_behavior = float(market_cfg.get("minimum_behavior_similarity") or 0.72)
    sector = _norm(candidate.get("sector"))
    industry = _norm(candidate.get("industry"))
    themes = candidate_themes(candidate)
    symbol = str(candidate.get("symbol") or "").upper()
    peers: list[dict[str, Any]] = []

    for raw in all_candidates:
        peer = dict(raw)
        peer_symbol = str(peer.get("symbol") or "").upper()
        if not peer_symbol or peer_symbol == symbol:
            continue
        p_sector = _norm(peer.get("sector"))
        p_industry = _norm(peer.get("industry"))
        p_themes = candidate_themes(peer)
        behavioral = behavior_similarity(candidate, peer)
        relation = "behavioral"
        similarity = behavioral
        if industry and p_industry and industry == p_industry:
            relation, similarity = "same_industry", max(similarity, 1.0)
        elif sector and p_sector and sector == p_sector:
            relation, similarity = "same_sector", max(similarity, 0.90)
        elif themes & p_themes:
            relation, similarity = "same_theme", max(similarity, 0.82)
        if similarity < minimum_behavior and relation == "behavioral":
            continue
        peers.append(
            {
                "symbol": peer_symbol,
                "name": peer.get("name"),
                "relation": relation,
                "similarity": round(_clamp(similarity, 0.0, 1.0), 6),
                "return_1d": _return(peer, 1),
                "return_5d": _return(peer, 5),
                "return_20d": _return(peer, 20),
                "volume_ratio_20d": _finite((peer.get("features") or {}).get("volume_ratio_20d")),
                "frontier_rank": peer.get("frontier_rank"),
            }
        )
    peers.sort(
        key=lambda row: (
            float(row["similarity"]),
            abs(float(row.get("return_1d") or 0.0)),
            -int(row.get("frontier_rank") or 9999),
        ),
        reverse=True,
    )
    return peers[:max_peers]


def reaction_score(candidate: Mapping[str, Any], config: Mapping[str, Any]) -> float:
    features = candidate.get("features") or {}
    r1 = abs(float(_return(candidate, 1) or 0.0))
    atr = _finite(features.get("atr_fraction"))
    vol = _finite(features.get("realized_volatility_20d"))
    reaction_cfg = config.get("reaction") or {}
    scale = max(float(reaction_cfg.get("minimum_scale") or 0.01), float(atr or 0.0), float(vol or 0.0))
    move_z = r1 / scale if scale > 0 else 0.0
    move_points = min(72.0, 36.0 * move_z)
    volume_ratio = max(0.0, float(_finite(features.get("volume_ratio_20d")) or 0.0))
    volume_ref = max(1.01, float(reaction_cfg.get("volume_ratio_reference") or 2.0))
    volume_points = min(28.0, max(0.0, volume_ratio - 1.0) / (volume_ref - 1.0) * 28.0)
    return _clamp(move_points + volume_points)


def persistence_score(candidate: Mapping[str, Any], direction: int) -> float:
    if direction == 0:
        return 0.0
    score = 0.0
    for horizon, weight, epsilon in ((1, 20.0, 0.003), (5, 35.0, 0.008), (20, 30.0, 0.015)):
        if _sign(_return(candidate, horizon), epsilon=epsilon) == direction:
            score += weight
    trend = _finite((candidate.get("score_components") or {}).get("trend_quality"))
    if trend is not None:
        score += 15.0 * _clamp(trend) / 100.0
    return _clamp(score)


def peer_confirmation(
    candidate: Mapping[str, Any],
    peers: Sequence[Mapping[str, Any]],
    direction: int,
) -> dict[str, Any]:
    if not peers or direction == 0:
        return {
            "score": 0.0,
            "agreement_rate": 0.0,
            "peer_count": len(peers),
            "median_return_1d": None,
            "leader_symbol": None,
            "lead_lag_watch": False,
        }

    total_weight = 0.0
    agree_weight = 0.0
    one_day: list[float] = []
    leader: tuple[float, str] | None = None
    volume_confirm = 0.0
    for peer in peers:
        similarity = max(0.01, float(peer.get("similarity") or 0.0))
        r1 = _finite(peer.get("return_1d")) or 0.0
        r5 = _finite(peer.get("return_5d")) or 0.0
        peer_direction = _sign(r1, epsilon=0.003) or _sign(r5, epsilon=0.008)
        total_weight += similarity
        if peer_direction == direction:
            agree_weight += similarity
        one_day.append(r1)
        volume = max(0.0, float(_finite(peer.get("volume_ratio_20d")) or 0.0))
        if peer_direction == direction and volume > 1.0:
            volume_confirm += similarity * min(1.0, (volume - 1.0) / 1.5)
        leadership = abs(r1) * similarity * (1.0 + min(2.0, max(0.0, volume - 1.0)))
        if leader is None or leadership > leader[0]:
            leader = (leadership, str(peer.get("symbol") or ""))

    agreement = agree_weight / total_weight if total_weight else 0.0
    volume_component = volume_confirm / total_weight if total_weight else 0.0
    median_r1 = statistics.median(one_day) if one_day else 0.0
    magnitude_component = min(1.0, abs(median_r1) / 0.03)
    score = _clamp(70.0 * agreement + 15.0 * volume_component + 15.0 * magnitude_component)

    own_r1 = abs(float(_return(candidate, 1) or 0.0))
    lead_lag = (
        agreement >= 0.65
        and abs(median_r1) >= 0.012
        and own_r1 <= abs(median_r1) * 0.55
        and _sign(median_r1, epsilon=0.003) == direction
    )
    return {
        "score": round(score, 6),
        "agreement_rate": round(agreement, 6),
        "peer_count": len(peers),
        "median_return_1d": round(median_r1, 8),
        "leader_symbol": leader[1] if leader else None,
        "lead_lag_watch": bool(lead_lag),
    }


def _trigger_type(
    *,
    event_context: Sequence[Mapping[str, Any]],
    reaction: float,
    peer: Mapping[str, Any],
    persistence: float,
) -> str:
    strongest = event_context[0] if event_context else {}
    relation = strongest.get("relation")
    event_score = float(strongest.get("relevance_score") or 0.0)
    peer_score = float(peer.get("score") or 0.0)
    if relation == "direct" and event_score >= 45.0 and reaction >= 50.0:
        return "DIRECT_EVENT_REACTION"
    if relation in {"theme", "macro"} and event_score >= 35.0 and peer_score >= 60.0:
        return "PEER_READTHROUGH"
    if peer.get("lead_lag_watch") and peer_score >= 60.0:
        return "LEAD_LAG_WATCH"
    if peer_score >= 65.0 and persistence >= 65.0 and reaction >= 45.0:
        return "CLUSTER_MOMENTUM"
    if reaction >= 75.0 and persistence >= 60.0:
        return "PRICE_VOLUME_ANOMALY"
    return "BACKGROUND"


def score_candidate(
    candidate: Mapping[str, Any],
    all_candidates: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    direction = candidate_direction(candidate)
    relevant = relevant_events(candidate, events, now=now, config=config)
    strongest = relevant[0] if relevant else None
    event_score = float((strongest or {}).get("relevance_score") or 0.0)
    event_direction = int((strongest or {}).get("direction") or 0)

    reaction = reaction_score(candidate, config)
    peers = peer_rows(candidate, all_candidates, config=config)
    peer = peer_confirmation(candidate, peers, direction)
    persistence = persistence_score(candidate, direction)
    frontier_prior = _clamp(float(candidate.get("opportunity_score") or 0.0))

    if event_direction and direction and event_direction != direction:
        event_score *= 0.65

    weights = config.get("weights") or {}
    components = {
        "event_materiality": _clamp(event_score),
        "entity_reaction": reaction,
        "peer_confirmation": float(peer["score"]),
        "persistence": persistence,
        "frontier_prior": frontier_prior,
    }
    attention = sum(float(components[key]) * float(weights.get(key) or 0.0) for key in components) / 100.0
    attention = _clamp(attention)

    market_cfg = (config.get("markets") or {}).get("US") or {}
    minimum = float(market_cfg.get("minimum_trigger_score") or 62.0)
    hot = float(market_cfg.get("hot_trigger_score") or 76.0)
    deep = float(market_cfg.get("deep_belief_score") or 82.0)
    trigger_type = _trigger_type(
        event_context=relevant,
        reaction=reaction,
        peer=peer,
        persistence=persistence,
    )
    tier = "HOT" if attention >= hot else "WARM" if attention >= minimum else "BACKGROUND"
    deep_eligible = (
        attention >= deep
        and strongest is not None
        and float(components["event_materiality"]) >= 45.0
        and (float(components["peer_confirmation"]) >= 55.0 or float(components["entity_reaction"]) >= 70.0)
    )
    reasons: list[str] = []
    if strongest:
        reasons.append(f"event:{strongest.get('relation')}:{strongest.get('event_kind')}")
    if reaction >= 70:
        reasons.append("abnormal_price_volume_reaction")
    if float(peer["score"]) >= 65:
        reasons.append("peer_confirmation")
    if peer.get("lead_lag_watch"):
        reasons.append("possible_peer_lead_lag")
    if persistence >= 70:
        reasons.append("multi_horizon_persistence")
    if not reasons:
        reasons.append("frontier_background_only")

    return {
        "symbol": candidate.get("symbol"),
        "name": candidate.get("name"),
        "sector": candidate.get("sector"),
        "industry": candidate.get("industry"),
        "frontier_rank": candidate.get("frontier_rank"),
        "opportunity_score": candidate.get("opportunity_score"),
        "direction": "UP" if direction > 0 else "DOWN" if direction < 0 else "FLAT",
        "attention_score": round(attention, 6),
        "attention_tier": tier,
        "trigger_type": trigger_type,
        "components": {key: round(float(value), 6) for key, value in components.items()},
        "event_context": deepcopy(relevant[:4]),
        "peer_context": {
            **peer,
            "peers": deepcopy(peers),
        },
        "time_context": {
            "frontier_observed_at": (candidate.get("freshness") or {}).get("observed_at"),
            "latest_market_session": (candidate.get("features") or {}).get("latest_session"),
            "strongest_event_age_hours": (strongest or {}).get("age_hours"),
        },
        "deep_belief_eligible": bool(deep_eligible),
        "reasons": reasons,
    }


def allocate_attention(
    scored: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    market_cfg = (config.get("markets") or {}).get("US") or {}
    minimum = float(market_cfg.get("minimum_trigger_score") or 62.0)
    max_total = int(market_cfg.get("maximum_attention_slots") or 6)
    trigger_slots = int(market_cfg.get("maximum_trigger_slots") or 4)
    exploration_slots = int(market_cfg.get("exploration_slots") or 2)
    deep_slots = int(market_cfg.get("maximum_deep_belief_slots") or 2)

    ordered = sorted(
        (dict(row) for row in scored),
        key=lambda row: (float(row.get("attention_score") or 0.0), -int(row.get("frontier_rank") or 9999)),
        reverse=True,
    )
    queue: list[dict[str, Any]] = []
    selected: set[str] = set()

    for row in ordered:
        if len(queue) >= min(trigger_slots, max_total):
            break
        if float(row.get("attention_score") or 0.0) < minimum:
            continue
        symbol = str(row.get("symbol") or "")
        queue.append({
            "symbol": symbol,
            "attention_source": "trigger",
            "attention_score": row.get("attention_score"),
            "attention_tier": row.get("attention_tier"),
            "trigger_type": row.get("trigger_type"),
            "deep_belief_eligible": row.get("deep_belief_eligible"),
            "frontier_rank": row.get("frontier_rank"),
        })
        selected.add(symbol)

    exploration = sorted(
        (dict(row) for row in scored if str(row.get("symbol") or "") not in selected),
        key=lambda row: int(row.get("frontier_rank") or 9999),
    )
    for row in exploration[:exploration_slots]:
        if len(queue) >= max_total:
            break
        symbol = str(row.get("symbol") or "")
        queue.append({
            "symbol": symbol,
            "attention_source": "exploration",
            "attention_score": row.get("attention_score"),
            "attention_tier": row.get("attention_tier"),
            "trigger_type": row.get("trigger_type"),
            "deep_belief_eligible": row.get("deep_belief_eligible"),
            "frontier_rank": row.get("frontier_rank"),
        })
        selected.add(symbol)

    deep_queue = [
        {
            "symbol": row.get("symbol"),
            "attention_score": row.get("attention_score"),
            "trigger_type": row.get("trigger_type"),
            "strongest_event_id": ((row.get("event_context") or [{}])[0] or {}).get("event_id"),
            "reason": "high_attention_event_reaction_peer_cluster",
        }
        for row in ordered
        if row.get("deep_belief_eligible") is True
    ][:deep_slots]
    return queue, deep_queue


def build_snapshot(
    frontier: Mapping[str, Any],
    event_snapshot: Mapping[str, Any] | None,
    config: Mapping[str, Any],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    discovery.validate_frontier(frontier)
    market = str(frontier.get("market") or "").upper()
    if market != "US":
        raise contracts.ContractError("market relationship trigger v1 currently supports US only")
    generated = generated_at or datetime.now(timezone.utc)
    event_snapshot = event_snapshot if isinstance(event_snapshot, Mapping) else {}
    events = [dict(row) for row in event_snapshot.get("events") or [] if isinstance(row, Mapping)]
    candidates = [dict(row) for row in frontier.get("candidates") or [] if isinstance(row, Mapping)]

    scored = [
        score_candidate(candidate, candidates, events, now=generated, config=config)
        for candidate in candidates
    ]
    scored.sort(
        key=lambda row: (float(row["attention_score"]), -int(row.get("frontier_rank") or 9999)),
        reverse=True,
    )
    queue, deep_queue = allocate_attention(scored, config=config)
    reference_slots = 10
    compute_reduction = 1.0 - min(reference_slots, len(queue)) / reference_slots

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "version": "market-relationship-trigger-v1.0.0",
        "market": market,
        "generated_at": _iso(generated),
        "mode": "shadow_attention_allocator",
        "central_object": "EVENT_x_ENTITY_x_PEERS_x_MARKET_REACTION_x_TIME",
        "source_frontier_sha256": frontier.get("frontier_sha256"),
        "source_frontier_generated_at": frontier.get("generated_at"),
        "source_event_snapshot_generated_at": event_snapshot.get("generated_at"),
        "frontier_size": len(candidates),
        "event_count_seen": len(events),
        "candidate_count": len(scored),
        "candidates": scored,
        "attention_queue": queue,
        "deep_belief_queue": deep_queue,
        "attention_economics": {
            "current_deep_evidence_reference_slots": reference_slots,
            "counterfactual_attention_slots": len(queue),
            "counterfactual_slot_reduction_fraction": round(max(0.0, compute_reduction), 6),
            "deep_belief_slots": len(deep_queue),
            "principle": "spend_deep_compute_only_after_trigger_or_preserved_exploration",
        },
        "learning_contract": {
            "lead_lag_is_hypothesis_until_settled": True,
            "peer_similarity_is_not_causal_claim": True,
            "future_outcomes_required_for_promotion": True,
            "counterfactual_with_without_required": True,
            "history_is_append_only": True,
        },
        "governance": deepcopy(config.get("governance") or {}),
    }
    payload["snapshot_sha256"] = contracts.payload_sha256(payload)
    validate_snapshot(payload, config)
    return payload


def validate_snapshot(payload: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("market") != "US":
        raise contracts.ContractError("market relationship trigger schema/market mismatch")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("relationship trigger escaped shadow governance")
    if governance.get("deep_belief_execution") is not False:
        raise contracts.ContractError("deep belief cannot execute in trigger v1")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or int(payload.get("candidate_count") or -1) != len(candidates):
        raise contracts.ContractError("relationship trigger candidate count mismatch")
    symbols: set[str] = set()
    for row in candidates:
        symbol = str((row or {}).get("symbol") or "")
        if not symbol or symbol in symbols:
            raise contracts.ContractError("relationship trigger duplicate/missing symbol")
        symbols.add(symbol)
        score = float((row or {}).get("attention_score") or 0.0)
        if not 0.0 <= score <= 100.0:
            raise contracts.ContractError("relationship trigger score outside range")
        if (row or {}).get("attention_tier") not in {"HOT", "WARM", "BACKGROUND"}:
            raise contracts.ContractError("relationship trigger invalid tier")

    market_cfg = (config.get("markets") or {}).get("US") or {}
    queue = payload.get("attention_queue") or []
    if len(queue) > int(market_cfg.get("maximum_attention_slots") or 6):
        raise contracts.ContractError("relationship trigger exceeded attention budget")
    for row in queue:
        if str((row or {}).get("symbol") or "") not in symbols:
            raise contracts.ContractError("attention queue references unknown symbol")
    deep = payload.get("deep_belief_queue") or []
    if len(deep) > int(market_cfg.get("maximum_deep_belief_slots") or 2):
        raise contracts.ContractError("relationship trigger exceeded deep belief budget")

    body = dict(payload)
    stored = str(body.pop("snapshot_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("relationship trigger snapshot hash mismatch")


def _observation_id(snapshot: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    event = ((row.get("event_context") or [{}])[0] or {})
    session = (row.get("time_context") or {}).get("latest_market_session")
    score_band = int(float(row.get("attention_score") or 0.0) // 5) * 5
    raw = "|".join(
        [
            str(snapshot.get("market") or ""),
            str(row.get("symbol") or ""),
            str(session or ""),
            str(event.get("event_id") or "NO_EVENT"),
            str(row.get("trigger_type") or ""),
            str(row.get("direction") or ""),
            str(score_band),
        ]
    )
    return "rel-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def freeze_observations(
    snapshot: Mapping[str, Any],
    *,
    root: Path = HISTORY_ROOT,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    history_cfg = config.get("history") or {}
    if history_cfg.get("enabled") is not True:
        return {"enabled": False, "written": 0, "existing": 0}
    minimum = float(history_cfg.get("minimum_score_to_freeze") or 62.0)
    market = str(snapshot.get("market") or "").lower()
    generated = _parse_time(snapshot.get("generated_at")) or datetime.now(timezone.utc)
    day = generated.date().isoformat()
    candidates = {
        str(row.get("symbol") or ""): row
        for row in snapshot.get("candidates") or []
        if isinstance(row, Mapping)
    }
    queue_sources = {
        str(row.get("symbol") or ""): str(row.get("attention_source") or "")
        for row in snapshot.get("attention_queue") or []
        if isinstance(row, Mapping) and str(row.get("symbol") or "")
    }
    freeze_exploration = history_cfg.get("freeze_exploration_controls") is True
    written = existing = 0
    for symbol in sorted(queue_sources):
        row = candidates.get(symbol)
        source = queue_sources[symbol]
        if not isinstance(row, Mapping):
            continue
        if source == "trigger" and float(row.get("attention_score") or 0.0) < minimum:
            continue
        if source == "exploration" and not freeze_exploration:
            continue
        observation_id = _observation_id(snapshot, row)
        payload = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "observation_id": observation_id,
            "market": str(snapshot.get("market") or ""),
            "symbol": symbol,
            "observed_at": snapshot.get("generated_at"),
            "session_date": (row.get("time_context") or {}).get("latest_market_session"),
            "attention_score": row.get("attention_score"),
            "attention_tier": row.get("attention_tier"),
            "attention_source": source,
            "trigger_type": row.get("trigger_type"),
            "direction": row.get("direction"),
            "components": deepcopy(row.get("components") or {}),
            "event_context": deepcopy(row.get("event_context") or []),
            "peer_context": deepcopy(row.get("peer_context") or {}),
            "frontier_rank": row.get("frontier_rank"),
            "source_frontier_sha256": snapshot.get("source_frontier_sha256"),
            "source_event_snapshot_generated_at": snapshot.get("source_event_snapshot_generated_at"),
            "outcome_contract": {
                "status": "PENDING",
                "settlement_horizons_sessions": [1, 3, 5, 20],
                "outcomes_must_be_separate_immutable_records": True,
                "no_hindsight_mutation": True,
            },
            "governance": {
                "production_decision_influence": False,
                "automatic_policy_writeback": False,
            },
        }
        payload["observation_sha256"] = contracts.payload_sha256(payload)
        path = root / market / day / f"{observation_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if path.exists():
            existing += 1
            continue
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(body)
            temp = Path(handle.name)
        temp.replace(path)
        written += 1
    return {"enabled": True, "written": written, "existing": existing}


def run(
    *,
    frontier_path: Path,
    event_path: Path | None = DEFAULT_EVENT_PATH,
    config_path: Path = CONFIG_PATH,
    output_path: Path | None = None,
    history_root: Path = HISTORY_ROOT,
    freeze_history: bool = True,
) -> dict[str, Any]:
    config = load_config(config_path)
    frontier = _read_json(frontier_path)
    if not isinstance(frontier, Mapping):
        raise TriggerEngineError(f"frontier unavailable: {frontier_path}")
    events = _read_json(event_path, {}) if event_path else {}
    snapshot = build_snapshot(frontier, events if isinstance(events, Mapping) else {}, config)
    history = freeze_observations(snapshot, root=history_root, config=config) if freeze_history else {"enabled": False}
    snapshot["runtime_history"] = history
    snapshot.pop("snapshot_sha256", None)
    snapshot["snapshot_sha256"] = contracts.payload_sha256(snapshot)
    validate_snapshot(snapshot, config)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "us"], default="US")
    parser.add_argument("--frontier", type=Path)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENT_PATH)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--history-root", type=Path, default=HISTORY_ROOT)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    market = str(args.market).upper()
    frontier = args.frontier or (FRONTIER_ROOT / f"{market.lower()}.json")
    output = args.output or (OUTPUT_ROOT / f"{market.lower()}.json")
    snapshot = run(
        frontier_path=frontier,
        event_path=args.events,
        config_path=args.config,
        output_path=output,
        history_root=args.history_root,
        freeze_history=not args.no_history,
    )
    print(
        json.dumps(
            {
                "market": market,
                "frontier_size": snapshot["frontier_size"],
                "event_count_seen": snapshot["event_count_seen"],
                "attention_slots": len(snapshot["attention_queue"]),
                "deep_belief_slots": len(snapshot["deep_belief_queue"]),
                "hot": sum(1 for row in snapshot["candidates"] if row.get("attention_tier") == "HOT"),
                "warm": sum(1 for row in snapshot["candidates"] if row.get("attention_tier") == "WARM"),
                "counterfactual_slot_reduction_fraction": snapshot["attention_economics"]["counterfactual_slot_reduction_fraction"],
                "production_decision_influence": False,
                "history": snapshot.get("runtime_history"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
