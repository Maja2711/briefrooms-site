from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from belief_market_data_adapter import Bar, MarketSnapshot
from brace_broad_market_belief import (
    BROAD_MARKET_BELIEFS,
    LIQUIDITY,
    MACRO,
    RATES,
    RISK,
    build_evidence,
    evaluate_outcome,
    outcome_spec,
    required_symbols,
    safety_controls,
)


class BraceBroadMarketBeliefTests(unittest.TestCase):
    @staticmethod
    def _rows(start: float, end: float, *, count: int = 14):
        base = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
        if count <= 1:
            return [Bar(base, end)]
        step = (end - start) / (count - 1)
        return [Bar(base + timedelta(minutes=30 * i), start + step * i) for i in range(count)]

    def _snapshot(self):
        return MarketSnapshot({
            "SPY": self._rows(650.0, 655.0),
            "RSP": self._rows(190.0, 192.0),
            "IWM": self._rows(225.0, 226.0),
            "^VIX": self._rows(18.0, 17.0),
            "HYG": self._rows(80.0, 80.8),
            "LQD": self._rows(110.0, 110.1),
            "TLT": self._rows(90.0, 91.0),
            "UUP": self._rows(28.0, 27.8),
        })

    def test_stage_contains_only_four_broad_market_beliefs(self):
        ids = [row.belief_id for row in BROAD_MARKET_BELIEFS]
        self.assertEqual(ids, [RATES, LIQUIDITY, MACRO, RISK])
        self.assertEqual(len(ids), 4)
        self.assertTrue(all("BRACE" in row.tags for row in BROAD_MARKET_BELIEFS))
        self.assertTrue(all("broad_market" in row.tags for row in BROAD_MARKET_BELIEFS))
        self.assertFalse(any(any(t.startswith("company:") for t in row.tags) for row in BROAD_MARKET_BELIEFS))

    def test_all_production_and_entity_influence_is_hard_off(self):
        controls = safety_controls()
        self.assertTrue(controls)
        self.assertTrue(all(value is False for value in controls.values()))
        self.assertIs(controls["company_entity_beliefs_enabled"], False)
        self.assertIs(controls["sector_factor_beliefs_enabled"], False)
        self.assertIs(controls["active_decision_influence"], False)
        self.assertIs(controls["automatic_tuning"], False)

    def test_market_evidence_targets_every_broad_market_belief(self):
        result = build_evidence(self._snapshot(), None)
        belief_ids = {row.belief_id for row in result.evidence}
        self.assertEqual(belief_ids, {RATES, LIQUIDITY, MACRO, RISK})
        self.assertGreaterEqual(len(result.observations), 4)
        self.assertTrue(all(row.direction == 1 for row in result.evidence))

    def test_outcome_contracts_are_deterministic(self):
        snapshot = self._snapshot()

        rates = outcome_spec(RATES, snapshot)
        self.assertEqual(required_symbols(rates), ("TLT",))
        self.assertTrue(evaluate_outcome(rates, {"TLT": snapshot.latest("TLT") + 1.0}))
        self.assertFalse(evaluate_outcome(rates, {"TLT": snapshot.latest("TLT") - 1.0}))

        liquidity = outcome_spec(LIQUIDITY, snapshot)
        self.assertEqual(required_symbols(liquidity), ("HYG", "LQD"))
        self.assertTrue(evaluate_outcome(liquidity, {"HYG": 82.0, "LQD": 110.0}))
        self.assertFalse(evaluate_outcome(liquidity, {"HYG": 78.0, "LQD": 111.0}))

        macro = outcome_spec(MACRO, snapshot)
        self.assertEqual(required_symbols(macro), ("TLT", "HYG", "UUP"))
        self.assertTrue(evaluate_outcome(macro, {
            "TLT": snapshot.latest("TLT") + .5,
            "HYG": snapshot.latest("HYG") + .2,
            "UUP": snapshot.latest("UUP") + .1,
        }))
        self.assertFalse(evaluate_outcome(macro, {
            "TLT": snapshot.latest("TLT") - .5,
            "HYG": snapshot.latest("HYG") - .2,
            "UUP": snapshot.latest("UUP") + .1,
        }))

        risk = outcome_spec(RISK, snapshot)
        self.assertEqual(required_symbols(risk), ("SPY", "^VIX", "HYG", "LQD"))
        self.assertTrue(evaluate_outcome(risk, {
            "SPY": snapshot.latest("SPY"),
            "^VIX": snapshot.latest("^VIX"),
            "HYG": snapshot.latest("HYG"),
            "LQD": snapshot.latest("LQD"),
        }))
        self.assertFalse(evaluate_outcome(risk, {
            "SPY": snapshot.latest("SPY") * .95,
            "^VIX": 40.0,
            "HYG": snapshot.latest("HYG") * .95,
            "LQD": snapshot.latest("LQD") * 1.05,
        }))

    def test_claims_do_not_overstate_proxy_quality(self):
        result = build_evidence(self._snapshot(), None)
        rates = next(row for row in result.observations if row.metric == "tlt_return_1d_rates_pressure_proxy")
        liquidity = next(row for row in result.observations if row.metric == "hyg_lqd_relative_1d_liquidity_proxy")
        self.assertTrue(rates.metadata["proxy_only"])
        self.assertIn("not a policy-rate series", rates.metadata["interpretation"])
        self.assertTrue(liquidity.metadata["proxy_only"])
        self.assertIn("proxy", liquidity.metadata["interpretation"])


if __name__ == "__main__":
    unittest.main()
