#!/usr/bin/env python3
"""Private Saxo EUR/USD price evidence -> Belief Core -> CF-07 EpistemicState.

This is a prospective, ephemeral private projection. It does NOT edit the
public Belief artifact, public Daily ledger or immutable historical forecasts.
Saxo SIM chart observations are non-redistributable and never leave RUNNER_TEMP.
Other macro / USD / rates evidence remains attributed to its actual providers.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from belief_adapter_contract import EvidenceAssessment, Observation, clamp, observation_to_evidence
from belief_core import BeliefCore, iso_z
from belief_epistemic_state import build_runtime_snapshot, persist_runtime_snapshot
from belief_market_data_adapter import MarketSnapshot
from belief_wes_assets_adapter import EURUSD_SYMBOL, _trend_score
from epistemic_consumer_interface import EpistemicConsumerInterface
from saxo_private_chart import SaxoPrivateChartClient

TREND_BELIEF = "eurusd.trend.bullish"
CHART_SOURCE = "SAXO_SIM_CHART_PRIVATE"
MAX_UPSTREAM_AGE = timedelta(hours=2)
MAX_CLOSED_BAR_AGE = timedelta(minutes=65)


class PrivateSaxoBeliefFailure(RuntimeError):
    """Fixed codes only: never include provider payloads, values or file contents."""


def _is_isolated(path: Path) -> bool:
    repository = Path(__file__).resolve().parents[1]
    target = path.resolve()
    return target != repository and not target.is_relative_to(repository)


def _upstream_path(variable: str) -> Path:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise PrivateSaxoBeliefFailure("private_belief_upstream_missing")
    path = Path(value).resolve()
    if not path.is_file() or not _is_isolated(path):
        raise PrivateSaxoBeliefFailure("private_belief_upstream_not_isolated")
    return path


def _check_upstream(payload: dict, now: datetime) -> None:
    # An expired public projection must not be re-stamped with "now" and made
    # to look fresh merely because the Saxo trend component is newly observed.
    try:
        EpistemicConsumerInterface(payload)
        source = datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
        if source.tzinfo is None:
            raise ValueError("timezone required")
        lag = now - source.astimezone(timezone.utc)
        if lag < -timedelta(minutes=2) or lag > MAX_UPSTREAM_AGE:
            raise ValueError("upstream expired")
    except (KeyError, TypeError, ValueError):
        raise PrivateSaxoBeliefFailure("private_belief_upstream_stale_or_invalid") from None


def build_private_saxo_epistemic(private_directory: Path, now: datetime | None = None) -> Path:
    """Return a private EpistemicState path; set env only after all checks pass."""
    if not all(os.environ.get(key) == "1" for key in (
        "BRIEFROOMS_PRIVATE_RUNTIME", "SAXO_PRIVATE_PRIMARY",
        "SAXO_STREAM_PRIMARY", "SAXO_CHART_PRIMARY",
        "SAXO_PRIVATE_BELIEF_PRIMARY",
    )):
        raise PrivateSaxoBeliefFailure("private_belief_runtime_not_enabled")
    directory = private_directory.resolve()
    if not _is_isolated(directory):
        raise PrivateSaxoBeliefFailure("private_belief_directory_not_isolated")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_core = _upstream_path("BELIEF_CORE_STATE")
    source_epistemic = _upstream_path("BELIEF_EPISTEMIC_STATE")
    try:
        _check_upstream(json.loads(source_epistemic.read_text(encoding="utf-8")), now)
        output = directory / "saxo_private_belief"
        if output.exists():
            raise PrivateSaxoBeliefFailure("private_belief_output_already_exists")
        output.mkdir(parents=True)
        shutil.copyfile(source_core, output / "state.json")
        core = BeliefCore(output)
        if TREND_BELIEF not in core.definitions:
            raise PrivateSaxoBeliefFailure("private_belief_trend_definition_missing")

        # This private projection is rebuilt afresh every cycle. Exclude
        # historical Yahoo EUR/USD trend evidence from directional authority;
        # do not splice it with the newly sourced Saxo signal.
        core.evidence = {
            key: value for key, value in core.evidence.items()
            if value.belief_id != TREND_BELIEF
        }
        candles = SaxoPrivateChartClient().bars(EURUSD_SYMBOL, "10d", "30m")
        if len(candles) < 66:
            raise PrivateSaxoBeliefFailure("private_belief_saxo_history_insufficient")
        bar_close = candles[-1].timestamp + timedelta(minutes=30)
        age = now - bar_close
        if age < timedelta(0) or age > MAX_CLOSED_BAR_AGE:
            raise PrivateSaxoBeliefFailure("private_belief_saxo_bar_stale")
        trend = _trend_score(MarketSnapshot({EURUSD_SYMBOL: candles}), EURUSD_SYMBOL,
                             (.0035, .0075, .018))
        if trend is None:
            raise PrivateSaxoBeliefFailure("private_belief_trend_unavailable")

        source_at = iso_z(bar_close)
        observation = Observation.make(
            adapter="saxo_private_eurusd_trend",
            metric="eurusd_trend_composite",
            entity="EURUSD",
            observed_at=source_at,
            value=trend,
            unit="score",
            source="Derived from private Saxo SIM completed 30m bars",
            source_type="derived",
            source_ref="private:saxo_sim_chart:EURUSD:30m:" + source_at,
            reliability=.74,
            independence_cluster="derived:EURUSD:technical",
            tags=("WES", "EURUSD", "saxo_private", "completed_bars"),
            metadata={
                "serial_state_snapshot": True,
                "private_nonredistributable": True,
                "bar_timestamp_basis": "completed_bar_end",
                "coverage": "price_only",
            },
        )
        evidence = observation_to_evidence(
            observation,
            EvidenceAssessment(
                TREND_BELIEF,
                1 if trend >= 0 else -1,
                clamp(abs(trend), .08, .75),
                "technical_trend",
                "Private Saxo completed-bar EUR/USD trend assessment",
            ),
        )
        # Record the immediate Observation node for four-layer provenance.
        (output / "observations.jsonl").write_text(
            json.dumps(asdict(observation), sort_keys=True) + "\n", encoding="utf-8"
        )
        core.ingest((evidence,))
        core.recompute(now)
        representative = core.beliefs[TREND_BELIEF].representative_evidence_ids
        if list(representative) != [evidence.evidence_id]:
            raise PrivateSaxoBeliefFailure("private_belief_trend_lineage_mismatch")
        snapshot = build_runtime_snapshot(output)
        trend_state = (snapshot.get("states") or {}).get(TREND_BELIEF) or {}
        lineage = (trend_state.get("provenance_root") or {}).get("evidence_lineage") or []
        if len(lineage) != 1 or lineage[0].get("source_ref") != observation.source_ref:
            raise PrivateSaxoBeliefFailure("private_belief_epistemic_lineage_mismatch")
        # CF-07 validates aggregate authority, immutable probabilities and
        # qualified source timestamps. Unavailable coverage remains NO_TRADE.
        EpistemicConsumerInterface(snapshot).envelope("DAILY_EURUSD")
        persist_runtime_snapshot(output, snapshot)

        result = output / "epistemic_state.json"
        if not result.is_file():
            raise PrivateSaxoBeliefFailure("private_belief_projection_missing")
        os.environ["BELIEF_CORE_STATE"] = str(output / "state.json")
        os.environ["BELIEF_EPISTEMIC_STATE"] = str(result)
        return result
    except PrivateSaxoBeliefFailure:
        raise
    except Exception:
        # A request/parse failure must never leak Saxo response body or price.
        raise PrivateSaxoBeliefFailure("private_belief_build_failed_closed") from None
