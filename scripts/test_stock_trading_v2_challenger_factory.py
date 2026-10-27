#!/usr/bin/env python3
"""Synthetic safety tests for Challenger Factory + exact replay."""
from __future__ import annotations

from scripts import stock_trading_v2_challenger_factory as factory
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_exact_replay as exact


def _manifest() -> dict:
    return {
        "revision": 1,
        "components": {"entry": {"version": "v1"}},
    }


def _gpw() -> dict:
    return {"minimum_composite_score": 72}


def _policy() -> dict:
    return {"markets": {"GPW": {"minimum_entry_score": 72}}}


def _hypothesis() -> dict:
    return {
        "component": "entry_score_below_threshold",
        "horizon_sessions": 2,
        "status": "ELIGIBLE_FOR_CHALLENGER_HOLDOUT",
        "evidence": {"observations": 40},
    }


def _event(event_id: str, score: float | None, *, symbol: str, blocker: str = "entry_score_below_threshold") -> dict:
    score_state = {"composite_score": score} if score is not None else {"composite_score": None}
    return {
        "event_id": event_id,
        "market": "GPW",
        "symbol": symbol,
        "decision_at": "2026-09-01T10:00:00+02:00",
        "session_date": "2026-09-01",
        "selected": False,
        "source": {"payload_sha256": "group-1"},
        "candidate_state": {
            "score_state": score_state,
            "decision_path": {
                "gates": [{"name": blocker, "passed": False}],
            },
        },
    }


def _admission(event_id: str) -> dict:
    return {"source_event_id": event_id, "champion": {"action": "CASH"}}


def _outcome(event_id: str, value: float) -> dict:
    return {
        "source_event_id": event_id,
        "horizon_sessions": 2,
        "replay": {"status": "SETTLED", "net_return_percent": value},
    }


def main() -> int:
    rows = factory._entry_threshold_candidates(
        hypothesis=_hypothesis(),
        manifest=_manifest(),
        gpw_config=_gpw(),
        policy=_policy(),
    )
    assert len(rows) == 3
    thresholds = [int(row["replay_contract"]["challenger_threshold"]) for row in rows]
    assert thresholds == [71, 70, 69]
    for row in rows:
        factory.validate_candidate(row)
        assert row["production_component"] == "entry"
        assert row["base_manifest_revision"] == 1
        assert row["base_component_version"] == "v1"
        assert row["deployment_sha256"] == factory.deployment_sha256(row["deployment_spec"])

    long_horizon = _hypothesis()
    long_horizon["horizon_sessions"] = 20
    try:
        factory._entry_threshold_candidates(
            hypothesis=long_horizon,
            manifest=_manifest(),
            gpw_config=_gpw(),
            policy=_policy(),
        )
    except contracts.ContractError:
        pass
    else:
        raise AssertionError("20-session research evidence must not manufacture a Daily Trading deployment")

    candidate = rows[1]  # exact threshold 70

    # Two same-group candidates both qualify. Selection MUST use frozen score,
    # not the realised return. AAA has the higher score but the worse outcome.
    events = [
        _event("e-high-score", 71.5, symbol="AAA.WA"),
        _event("e-high-return", 70.5, symbol="BBB.WA"),
    ]
    admissions = [_admission("e-high-score"), _admission("e-high-return")]
    outcomes = [_outcome("e-high-score", -1.0), _outcome("e-high-return", 8.0)]
    samples = exact.collect_entry_threshold_samples(
        candidate=candidate,
        events=events,
        admissions=admissions,
        outcomes=outcomes,
    )
    assert len(samples) == 1
    assert samples[0]["source_event_id"] == "e-high-score", "replay must never choose by realised outcome"
    assert samples[0]["incremental_net_return_percent"] == -1.0

    # Missing frozen score cannot be reconstructed after the fact.
    no_score = exact.collect_entry_threshold_samples(
        candidate=candidate,
        events=[_event("e-no-score", None, symbol="CCC.WA")],
        admissions=[_admission("e-no-score")],
        outcomes=[_outcome("e-no-score", 10.0)],
    )
    assert no_score == []

    # An ambiguous/generic quant rejection is not treated as an exact entry
    # threshold counterfactual even if the realised outcome was excellent.
    ambiguous = exact.collect_entry_threshold_samples(
        candidate=candidate,
        events=[_event("e-ambiguous", 71.0, symbol="DDD.WA", blocker="quant_candidate")],
        admissions=[_admission("e-ambiguous")],
        outcomes=[_outcome("e-ambiguous", 12.0)],
    )
    assert ambiguous == []

    print("CHALLENGER_FACTORY_EXACT_REPLAY_TESTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
