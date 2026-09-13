import copy
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import brace_spx_adaptive_research as adaptive
import brace_spx_generation6 as g6


def sample_report():
    return {
        "strict_gate_passed": False,
        "single_champion_authorized": False,
        "rank_stability": {"median_pairwise_fold_rank_correlation": 0.047619},
        "raw_best_diagnostic_only": {"metrics": {"sharpe_excess": 0.099713}},
        "baselines": {"trend_200d_weekly": {"sharpe_excess": 0.600896}},
    }


def sample_shadow():
    return {
        "latest_market_date": "2026-09-11",
        "observations_collected": 30,
        "warmup_required": 70,
        "observations_remaining": 40,
        "shadow_start": "2026-08-03",
        "status": "warming_up",
        "holdout_accessed": False,
    }


def test_initial_state_is_one_platform_and_preserves_frozen_signature():
    state = adaptive._initial_state(sample_report(), sample_shadow())
    assert state["platform_id"] == "brace-spx"
    assert state["frozen_generation_id"] == g6.GENERATION_ID
    assert state["frozen_candidate_signature"] == g6.candidate_signature()
    assert state["activation_observations"] == 30
    assert len(state["challengers"]) == 4
    assert state["governance"]["mutate_frozen_g6"] is False
    assert state["governance"]["sealed_holdout_access"] is False
    assert state["governance"]["automatic_promotion"] is False


def test_new_challengers_start_at_their_own_evidence_boundary():
    state = adaptive._initial_state(sample_report(), sample_shadow())
    for challenger in state["challengers"]:
        assert challenger["created_market_date"] == "2026-09-11"
    # The 30 observations that existed before challenger creation are context,
    # not prospective evidence for the newly created challengers.
    assert state["activation_observations"] == 30
    assert adaptive.MIN_SELECTION_N == 20
    assert adaptive.MIN_PROMOTION_N == 70


def test_public_candidate_never_exposes_private_weights():
    state = adaptive._initial_state(sample_report(), sample_shadow())
    candidate = state["challengers"][0]
    public = adaptive._public_candidate(candidate, {
        "prospective_n": 0,
        "active_n": 0,
        "cumulative_return": None,
        "sharpe_excess": None,
        "max_drawdown": None,
        "hit_rate_active": None,
    })
    assert "weights" not in public
    assert public["candidate_id"] == "G6-R-C01"
    assert public["created_market_date"] == "2026-09-11"


def test_adaptive_track_fails_closed_if_frozen_signature_changes():
    state = adaptive._initial_state(sample_report(), sample_shadow())
    state = copy.deepcopy(state)
    state["frozen_candidate_signature"] = "tampered"
    prices = pd.DataFrame({"SPY": [1.0]}, index=pd.to_datetime(["2026-09-14"]))
    with pytest.raises(RuntimeError, match="Frozen G6 signature changed"):
        adaptive.run(state, prices, sample_report(), sample_shadow())


def test_adaptive_track_fails_closed_if_g6_mutation_is_requested():
    state = adaptive._initial_state(sample_report(), sample_shadow())
    state = copy.deepcopy(state)
    state["governance"]["mutate_frozen_g6"] = True
    prices = pd.DataFrame({"SPY": [1.0]}, index=pd.to_datetime(["2026-09-14"]))
    with pytest.raises(RuntimeError, match="attempted to mutate frozen G6"):
        adaptive.run(state, prices, sample_report(), sample_shadow())


def test_spawned_challenger_gets_new_id_and_new_market_boundary():
    state = adaptive._initial_state(sample_report(), sample_shadow())
    parent = state["challengers"][0]
    child = adaptive._spawn_adaptive_challenger(
        state,
        parent,
        {"price_trend": 0.05, "rates": -0.02, "liquidity": 0.01, "options_vix": -0.01},
        checkpoint=20,
        market_date="2026-10-09",
    )
    assert child["candidate_id"] == "G6-R-C05"
    assert child["parent_candidate_id"] == "G6-R-C01"
    assert child["created_market_date"] == "2026-10-09"
    assert child["origin"] == "adaptive_checkpoint_20"
    assert abs(sum(child["weights"].values()) - 1.0) < 1e-12
