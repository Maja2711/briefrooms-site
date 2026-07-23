from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_historical_seed_calibration as calibration


def reconstructed_multiplier(success: float, failure: float) -> float:
    mean = (calibration.PRIOR_ALPHA + success) / (
        calibration.PRIOR_ALPHA + calibration.PRIOR_BETA + success + failure
    )
    return 1.0 + (mean - 0.5) * calibration.MULTIPLIER_SLOPE


def test_credit_inverse_reproduces_validated_multiplier():
    for requested in (0.82, 0.95, 1.0, 1.05, 1.18):
        success, failure = calibration.credits_for_multiplier(requested)
        assert round(success + failure, 6) == calibration.CREDIT_CAP
        assert abs(reconstructed_multiplier(success, failure) - requested) < 1e-6


def test_calibrate_removes_old_seed_and_preserves_live_events():
    memory = {
        "outcome_events": [
            {"outcome_event_id": "live", "historical_seed": False},
            {"outcome_event_id": "old", "historical_seed": True},
        ]
    }
    training = {
        "activated": True,
        "training_id": "test",
        "multipliers": {"price_vs_ma200": 1.05},
    }
    created = calibration.calibrate(memory, training)
    ids = {item["outcome_event_id"] for item in memory["outcome_events"]}
    assert "live" in ids
    assert "old" not in ids
    assert len(created) == 2
    assert all(item["historical_seed_calibrated"] for item in created)
