import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from belief_core import BeliefCore
from belief_market_data_adapter import Bar, MarketSnapshot
from brace_sector_factor_belief import (
    SECTOR_FACTOR_BELIEF_IDS,
    SECTOR_FACTOR_BELIEFS,
    REQUIRED_SYMBOLS,
    analysis_stage,
    build_evidence,
    capabilities,
    evaluate_outcome,
    outcome_spec,
    run_cycle,
    safety_controls,
    serial_effective_n,
)


class FakeYahooClient:
    def __init__(self, end: datetime):
        self.end = end

    def bars(self, symbol: str, range_: str = "10d", interval: str = "30m"):
        if interval == "1d":
            return [Bar(self.end - timedelta(days=i), 100.0 + i) for i in range(5, -1, -1)]
        baseline_growth = 0.0005
        faster = {"XLK", "XLF", "XLV", "XLY", "XLP", "XLC", "SOXX", "IWF", "QUAL", "MTUM", "IWM"}
        growth = 0.0012 if symbol in faster else baseline_growth
        if symbol == "IWD":
            growth = 0.0004
        rows = []
        start = self.end - timedelta(minutes=30 * 69)
        for i in range(70):
            close = 100.0 * ((1.0 + growth) ** i)
            rows.append(Bar(start + timedelta(minutes=30 * i), close))
        return rows


def snapshot(end: datetime) -> MarketSnapshot:
    client = FakeYahooClient(end)
    return MarketSnapshot({symbol: client.bars(symbol) for symbol in REQUIRED_SYMBOLS})


class BraceSectorFactorBeliefTests(unittest.TestCase):
    def test_taxonomy_is_sector_factor_only(self):
        self.assertEqual(len(SECTOR_FACTOR_BELIEF_IDS), 11)
        self.assertTrue(all(x.belief_id in SECTOR_FACTOR_BELIEF_IDS for x in SECTOR_FACTOR_BELIEFS))
        self.assertTrue(any(x.startswith("sector.") for x in SECTOR_FACTOR_BELIEF_IDS))
        self.assertTrue(any(x.startswith("factor.") for x in SECTOR_FACTOR_BELIEF_IDS))
        self.assertFalse(any(x.startswith(("AMZN", "GOOGL", "JPM", "entity.")) for x in SECTOR_FACTOR_BELIEF_IDS))

    def test_zero_influence_and_company_layer_disabled(self):
        controls = safety_controls()
        self.assertTrue(controls)
        self.assertTrue(all(value is False for value in controls.values()))
        self.assertFalse(capabilities()["company_entity_beliefs_enabled"])
        self.assertFalse(capabilities()["with_without_bridge_enabled"])
        self.assertFalse(capabilities()["promotion_gate_enabled"])

    def test_relative_leadership_evidence_is_prospective_proxy(self):
        snap = snapshot(datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc))
        result = build_evidence(snap)
        self.assertEqual(len(result.evidence), 11)
        tech = next(x for x in result.evidence if x.belief_id == "sector.technology.leadership")
        self.assertEqual(tech.direction, 1)
        self.assertEqual(tech.source_type, "derived")
        self.assertTrue(tech.metadata["proxy_only"])
        self.assertTrue(tech.metadata["with_without_required_before_promotion"])

    def test_outcome_contract_uses_frozen_relative_ratio(self):
        snap = snapshot(datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc))
        spec = outcome_spec("sector.technology.leadership", snap)
        ref = spec["reference"]
        self.assertTrue(evaluate_outcome(spec, {"XLK": ref * 101.0, "SPY": 100.0}))
        self.assertFalse(evaluate_outcome(spec, {"XLK": ref * 99.0, "SPY": 100.0}))

    def test_analysis_thresholds_never_encode_promotion(self):
        self.assertEqual(analysis_stage(0), "collecting_warmup")
        self.assertEqual(analysis_stage(12), "descriptive_only")
        self.assertEqual(analysis_stage(30), "sector_factor_calibration_analysis_available")

    def test_effective_n_is_bounded_by_raw_n(self):
        rows = [
            {"target_at": f"2026-08-{10+i:02d}T20:00:00Z", "forecast_id": str(i), "outcome": bool(i % 2), "predicted_probability": .55}
            for i in range(8)
        ]
        ess = serial_effective_n(rows)
        self.assertGreaterEqual(ess, 1.0)
        self.assertLessEqual(ess, 8.0)

    def test_first_valid_run_is_activation_only_then_next_session_can_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_client = FakeYahooClient(datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc))
            first = run_cycle(root, datetime(2026, 8, 18, 21, 0, tzinfo=timezone.utc), market_client=first_client)
            core = BeliefCore(root / "belief_core")
            self.assertEqual(len(core.forecasts), 0)
            self.assertTrue(first["activation"]["first_run_activation_only"])
            self.assertFalse(first["activation"]["historical_backfill"])

            second_client = FakeYahooClient(datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc))
            second = run_cycle(root, datetime(2026, 8, 19, 21, 0, tzinfo=timezone.utc), market_client=second_client)
            core2 = BeliefCore(root / "belief_core")
            self.assertEqual(len(core2.forecasts), 11)
            self.assertEqual(second["hierarchy"]["active_layer"], "sector_factor")
            self.assertEqual(second["hierarchy"]["company_entity_beliefs"], "deferred_to_pr12_plus_reviewed_framework")
            self.assertTrue(second["promotion_evidence_standard"]["with_without_required"])
            self.assertFalse(second["promotion_evidence_standard"]["automatic_promotion"])


if __name__ == "__main__":
    unittest.main()
