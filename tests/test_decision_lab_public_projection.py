import unittest

from decision_lab_public_projection import build_history_payload, build_payload
from test_evidence_pattern_discovery import ev, verification


class DecisionLabProjectionTests(unittest.TestCase):
    def test_projection_exposes_sanitized_evidence_pattern_contract(self):
        rows = []
        pattern_rows = set(range(20)) | {45, 48, 52, 55, 58}
        for i in range(60):
            if i in pattern_rows:
                evidence = [
                    ev("eps_revision", -1),
                    ev("rates_pressure", 1),
                    ev("noise_a", 1),
                ]
                outcome = False if i == 52 else True
            else:
                evidence = [ev("noise_a", 1), ev("noise_b", -1)]
                outcome = i % 2 == 0
            rows.append(verification(i, outcome, evidence))

        payload = build_payload(
            {"forecasts": [], "verifications": rows},
            {"belief_calibration": {}},
        )

        self.assertEqual(
            payload["schema_version"],
            "briefrooms_decision_lab_public_v2",
        )
        self.assertTrue(payload["evidence_patterns"])
        self.assertEqual(
            payload["evidence_pattern_meta"]["causal_status"],
            "ASSOCIATION_ONLY",
        )
        self.assertFalse(
            payload["evidence_pattern_meta"]["authority"]["decision_influence"]
        )
        self.assertNotIn(
            "evidence_snapshot",
            payload["evidence_patterns"][0],
        )


    def test_market_view_exposes_projection_time_separately_from_source_forecast_time(self):
        state = {
            "forecasts": [{
                "forecast_id": "eur-market-view",
                "entity": "EURUSD",
                "belief_id": "eurusd.trend.bullish",
                "predicted_probability": 0.44,
                "forecast_confidence": 0.47,
                "forecast_at": "2026-09-30T18:25:18Z",
                "target_at": "2026-10-01T18:25:18Z",
                "horizon_hours": 24,
                "metadata": {},
            }],
            "verifications": [],
            "definitions": [],
        }
        report = {
            "generated_at": "2026-10-01T07:13:48Z",
            "belief_calibration": {},
        }
        payload = build_payload(state, report)
        row = payload["market_view"][0]
        self.assertEqual(row["calculated_at"], "2026-10-01T07:13:48Z")
        self.assertEqual(row["as_of"], "2026-09-30T18:25:18Z")

    def test_open_forecast_keeps_weekend_target_unchanged(self):
        state = {
            "forecasts": [{
                "forecast_id": "eur-weekend",
                "entity": "EURUSD",
                "belief_id": "eurusd.trend.bullish",
                "predicted_probability": 0.476,
                "forecast_confidence": 0.474,
                "forecast_at": "2026-09-25T17:02:00+00:00",
                "target_at": "2026-09-26T17:02:00+00:00",
                "horizon_hours": 24,
                "metadata": {},
            }],
            "verifications": [],
            "definitions": [],
        }
        payload = build_payload(state, {"belief_calibration": {}})
        row = payload["forecasts"][0]
        self.assertEqual(row["status"], "OPEN")
        self.assertEqual(row["target_at"], "2026-09-26T17:02:00+00:00")
        self.assertEqual(row["horizon_label"], "24H")

    def test_projection_exposes_legacy_frozen_t0_without_reconstructing_market_data(self):
        state = {
            "forecasts": [{
                "forecast_id": "legacy-t0",
                "entity": "EURUSD",
                "belief_id": "eurusd.trend.bullish",
                "predicted_probability": 0.476,
                "forecast_confidence": 0.474,
                "forecast_at": "2026-09-25T17:02:28.714297Z",
                "target_at": "2026-09-26T17:02:28.714297Z",
                "horizon_hours": 24,
                "metadata": {
                    "market_observed_at": "2026-09-25T17:02:08Z",
                    "outcome_spec": {
                        "kind": "price_above",
                        "symbol": "EURUSD=X",
                        "reference": 1.1397310495376587,
                    },
                },
            }],
            "verifications": [],
            "definitions": [],
        }
        payload = build_payload(state, {"belief_calibration": {}})
        row = payload["forecasts"][0]
        self.assertEqual(row["t0_values"]["EURUSD=X"], 1.1397310495376587)
        self.assertEqual(row["t0_at"], "2026-09-25T17:02:08Z")
        self.assertEqual(row["t0_source"], "frozen_outcome_spec_reference")
        self.assertIsNone(row["forecast_contract_version"])

    def test_session_aware_multihorizon_contract_is_public_and_separate(self):
        forecast = {
            "forecast_id": "uup-session-1s",
            "entity": "BTC",
            "belief_id": "btc.usd_environment.supportive",
            "predicted_probability": 0.43,
            "forecast_confidence": 0.47,
            "forecast_at": "2026-09-28T20:03:30Z",
            "target_at": "2026-09-29T20:00:00Z",
            "horizon_hours": 23.941667,
            "metadata": {
                "multihorizon_contract": "decision-lab-multihorizon-v2-session-aware",
                "primary_research_horizon": True,
                "research_horizon_hours": 24,
                "research_horizon_label": "1S",
                "research_horizon_basis": "US_REGULAR_SESSION_EQUIVALENT",
                "calibration_horizon_bucket": "1S_US_SESSION",
                "elapsed_nominal_target_at": "2026-09-29T20:03:30Z",
                "session_equivalent_count": 1.0,
                "forecast_contract_version": "decision-lab-forecast-contract-v2",
                "model_freeze_version": "belief-core-v2-shadow-2026-09-27",
                "t0_at": "2026-09-28T20:03:30Z",
                "t0_values": {"UUP": 28.69},
                "nominal_target_at": "2026-09-29T20:00:00Z",
                "outcome_spec": {"kind": "value_below", "symbol": "UUP", "reference": 28.69, "threshold": 28.69},
                "market_calendar": "tradable_session_first_available",
            },
        }
        payload = build_payload(
            {
                "forecasts": [forecast],
                "verifications": [],
                "definitions": [{
                    "belief_id": "btc.usd_environment.supportive",
                    "claim": "Broad USD conditions support BTC",
                }],
            },
            {"belief_calibration": {}},
        )
        row = payload["forecasts"][0]
        self.assertEqual(row["horizon_label"], "1S")
        self.assertEqual(row["horizon_basis"], "US_REGULAR_SESSION_EQUIVALENT")
        self.assertEqual(row["elapsed_nominal_target_at"], "2026-09-29T20:03:30Z")
        self.assertEqual(row["session_equivalent_count"], 1.0)
        self.assertEqual(row["calibration_horizon_bucket"], "1S_US_SESSION")
        self.assertEqual(payload["multihorizon_paths"][0]["horizons"][0]["horizon_label"], "1S")

    def test_v3_candidate_forecasts_are_isolated_from_v2_metrics_and_market_view(self):
        candidate = {
            "forecast_id": "v3-open",
            "entity": "SPX",
            "belief_id": "spx.rates.supportive",
            "predicted_probability": 0.62,
            "forecast_confidence": 0.55,
            "forecast_at": "2026-09-28T14:00:00Z",
            "target_at": "2026-09-29T14:00:00Z",
            "horizon_hours": 24,
            "metadata": {
                "candidate_stage": "SHADOW",
                "candidate_isolated_from_control": True,
                "forecast_contract_version": "decision-lab-forecast-contract-v2",
                "t0_at": "2026-09-28T14:00:00Z",
                "t0_values": {"SPY": 770.0},
                "outcome_spec": {"kind": "price_above", "symbol": "SPY", "reference": 770.0},
            },
        }
        payload = build_payload(
            {
                "forecasts": [candidate],
                "verifications": [],
                "definitions": [{
                    "belief_id": "spx.rates.supportive",
                    "claim": "US rates conditions are supportive for SPX into the target horizon",
                }],
            },
            {"belief_calibration": {}},
        )
        self.assertEqual(payload["metrics"]["forecast_count"], 0)
        self.assertEqual(payload["metrics"]["resolved_count"], 0)
        self.assertEqual(payload["forecasts"], [])
        self.assertEqual(payload["market_view"], [])
        summary = payload["belief_core_v3_candidates"]["summary"]
        self.assertEqual(summary["registered"], 11)
        self.assertEqual(summary["wired"], 4)
        self.assertEqual(summary["waiting_for_real_source"], 7)
        self.assertEqual(summary["forecast_count"], 1)
        self.assertEqual(summary["open_count"], 1)
        row = next(
            x for x in payload["belief_core_v3_candidates"]["candidates"]
            if x["belief_id"] == "spx.rates.supportive"
        )
        self.assertEqual(row["forecast_n"], 1)
        self.assertEqual(row["open_n"], 1)
        self.assertEqual(row["sample_n"], 0)
        self.assertEqual(row["review_status"], "COLLECTING")
        self.assertTrue(row["wired"])

    def test_public_history_exposes_only_last_30_days_without_mutating_private_state(self):
        old = {
            "forecast_id": "old-private",
            "entity": "SPX",
            "belief_id": "spx.trend.bullish",
            "predicted_probability": 0.55,
            "forecast_confidence": 0.50,
            "forecast_at": "2026-08-20T12:00:00Z",
            "target_at": "2026-08-21T12:00:00Z",
            "horizon_hours": 24,
            "metadata": {},
        }
        recent = {
            "forecast_id": "recent-public",
            "entity": "SPX",
            "belief_id": "spx.trend.bullish",
            "predicted_probability": 0.60,
            "forecast_confidence": 0.55,
            "forecast_at": "2026-09-20T12:00:00Z",
            "target_at": "2026-09-21T12:00:00Z",
            "horizon_hours": 24,
            "metadata": {},
        }
        v3 = {
            "forecast_id": "v3-separate",
            "entity": "SPX",
            "belief_id": "spx.rates.supportive",
            "predicted_probability": 0.62,
            "forecast_confidence": 0.55,
            "forecast_at": "2026-09-21T12:00:00Z",
            "target_at": "2026-09-22T12:00:00Z",
            "horizon_hours": 24,
            "metadata": {},
        }
        state = {
            "forecasts": [old, recent, v3],
            "verifications": [],
            "definitions": [{
                "belief_id": "spx.trend.bullish",
                "claim": "SPX will be higher at target",
            }],
        }
        history = build_history_payload(
            state,
            now="2026-09-29T00:00:00Z",
            window_days=30,
        )
        self.assertEqual(history["schema_version"], "briefrooms_decision_lab_history_v1")
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["rows"][0]["forecast_id"], "recent-public")
        self.assertFalse(history["older_records_publicly_exposed"])
        self.assertEqual(len(state["forecasts"]), 3)
        self.assertEqual(state["forecasts"][0]["forecast_id"], "old-private")

    def test_projection_exposes_shadow_forecast_contract_without_promotion_authority(self):
        state = {
            "forecasts": [{
                "forecast_id": "contract-v2",
                "entity": "EURUSD",
                "belief_id": "eurusd.trend.bullish",
                "predicted_probability": 0.61,
                "forecast_confidence": 0.7,
                "forecast_at": "2026-09-28T12:00:00+00:00",
                "target_at": "2026-09-29T12:00:00+00:00",
                "horizon_hours": 24,
                "metadata": {
                    "forecast_contract_version": "decision-lab-forecast-contract-v2",
                    "model_freeze_version": "belief-core-v2-shadow-2026-09-27",
                    "t0_at": "2026-09-28T12:00:00Z",
                    "t0_values": {"EURUSD=X": 1.18},
                    "nominal_target_at": "2026-09-29T12:00:00Z",
                    "settlement_rule": "first_bar_at_or_after_nominal_target",
                    "market_calendar": "tradable_session_first_available",
                },
            }],
            "verifications": [], "definitions": [],
        }
        payload = build_payload(state, {"belief_calibration": {}})
        row = payload["forecasts"][0]
        self.assertEqual(row["t0_values"]["EURUSD=X"], 1.18)
        self.assertEqual(row["t0_source"], "forecast_contract_v2")
        self.assertEqual(row["settlement_rule"], "first_bar_at_or_after_nominal_target")
        self.assertFalse(row["production_write_authority"])
        self.assertFalse(row["automatic_promotion"])
        self.assertFalse(payload["automatic_promotion"])
        self.assertEqual(payload["promotion_policy"], "v3_candidates_manual; probability_recalibration_closed_loop_governed")
        self.assertTrue(payload["closed_loop"]["authority"]["automatic_model_overlay_promotion"])
        self.assertTrue(payload["closed_loop"]["authority"]["automatic_rollback"])
        self.assertFalse(payload["closed_loop"]["authority"]["trade_execution_authority"])

if __name__ == "__main__":
    unittest.main()
