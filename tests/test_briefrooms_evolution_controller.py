from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.briefrooms_evolution_contracts import EvolutionCandidate, PromotionGate
from scripts.briefrooms_evolution_controller import run


class EvolutionContractsTests(unittest.TestCase):
    def test_candidate_cannot_carry_execution_authority(self):
        row = EvolutionCandidate(
            candidate_id="c1",
            candidate_type="test",
            source_module="LE-01",
            target_module="EP-05",
            component_id="x",
            created_at="2026-09-28T20:00:00Z",
            activation_boundary="2026-09-28T20:00:00Z",
            status="DISCOVERED",
            evaluator_profile="test",
            promotion_route="test",
            baseline_version=None,
            challenger_version="v1",
            proposed_change={},
            source_ref="test://c1",
            source_sha256="abc",
            trade_execution_authority=True,
        )
        with self.assertRaises(ValueError):
            row.validate()

    def test_gate_pass_requires_minimum_sample(self):
        gate = PromotionGate(
            gate_id="g1",
            candidate_id="c1",
            evaluated_at="2026-09-28T20:00:00Z",
            status="PASS",
            evaluator_profile="test",
            prospective_only=True,
            minimum_sample=50,
            observed_sample=49,
            criteria={},
            metrics={},
        )
        with self.assertRaises(ValueError):
            gate.validate()


class EvolutionControllerTests(unittest.TestCase):
    def _paths(self, root: Path):
        return {
            "state": root / "evolution.json",
            "public": root / "public.json",
            "audit": root / "audit.jsonl",
            "belief_state": root / "belief-state.json",
            "closed": root / "closed.json",
            "lab": root / "lab.json",
            "experience": root / "experience.jsonl",
            "regret": root / "regret.json",
            "policy": root / "policy.json",
            "v3": root / "v3.json",
        }

    def _write_base_policy(self, p: Path):
        p.write_text(json.dumps({
            "schema_version":"belief-core-production-overrides-v1",
            "updated_at":None,
            "authority":{},
            "overrides":{},
            "history":[],
        }), encoding="utf-8")

    def _write_base_v3(self, p: Path):
        p.write_text(json.dumps({
            "schema_version":"belief-v3-production-registry-v1",
            "updated_at":None,
            "authority":{},
            "active":{},
            "history":[],
        }), encoding="utf-8")

    def test_belief_candidate_promotes_only_after_controller_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self._paths(root)
            self._write_base_policy(p["policy"])
            self._write_base_v3(p["v3"])

            forecasts, verifications = [], []
            from datetime import datetime, timedelta, timezone
            start=datetime(2026,9,29,tzinfo=timezone.utc)
            for i in range(60):
                fid=f"f{i}"
                at_dt=start+timedelta(hours=i)
                at=at_dt.isoformat().replace("+00:00","Z")
                outcome=bool(i%2)
                forecasts.append({
                    "forecast_id":fid,
                    "belief_id":"spx.trend.bullish",
                    "entity":"SPX",
                    "forecast_at":at,
                    "target_at":(at_dt+timedelta(hours=24)).isoformat().replace("+00:00","Z"),
                    "horizon_hours":24,
                    "predicted_probability":.90,
                    "metadata":{"raw_probability":.90},
                })
                verifications.append({
                    "forecast_id":fid,
                    "belief_id":"spx.trend.bullish",
                    "predicted_probability":.90,
                    "forecast_at":at,
                    "outcome":outcome,
                    "calibration_eligible":True,
                })
            p["belief_state"].write_text(json.dumps({
                "forecasts":forecasts,
                "verifications":verifications,
            }), encoding="utf-8")
            p["closed"].write_text(json.dumps({
                "generated_at":"2026-10-10T00:00:00Z",
                "challengers":{
                    "spx.trend.bullish":{
                        "status":"ready_for_evolution_controller",
                        "created_at":"2026-09-28T20:00:00Z",
                        "frozen_at":"2026-09-28T20:00:00Z",
                        "transform":{"type":"logit_affine_v1","intercept":0.0,"slope":0.0},
                        "trigger_reasons":["material_calibration_bias"],
                        "prospective":{
                            "n":60,
                            "control":{"brier":.41,"log_loss":1.20,"ece":.40,"accuracy":.50},
                            "challenger":{"brier":.25,"log_loss":.693,"ece":0.0,"accuracy":.50},
                            "brier_relative_improvement":.39024,
                            "stability":{"improved":4,"blocks":4},
                        },
                    }
                },
            }), encoding="utf-8")
            p["lab"].write_text(json.dumps({"evidence_patterns":[]}), encoding="utf-8")

            payload=run(
                state_path=p["state"], public_path=p["public"], audit_path=p["audit"],
                belief_state_path=p["belief_state"], belief_closed_loop_path=p["closed"],
                decision_lab_public_path=p["lab"], experience_store_path=None,
                trading_regret_path=None, belief_policy_path=p["policy"],
                v3_registry_path=p["v3"], now="2026-10-10T00:00:00Z",
            )
            policy=json.loads(p["policy"].read_text())
            active=policy["overrides"]["spx.trend.bullish"]
            self.assertTrue(active["active"])
            self.assertEqual(payload["summary"]["active_production_versions"],1)
            self.assertEqual(payload["summary"]["gate_pass"],1)
            self.assertFalse(payload["authority"]["trade_execution"])

    def test_pattern_experience_and_regret_create_only_governed_hypotheses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=self._paths(root)
            self._write_base_policy(p["policy"])
            self._write_base_v3(p["v3"])
            p["belief_state"].write_text(json.dumps({"forecasts":[],"verifications":[]}),encoding="utf-8")
            p["closed"].write_text(json.dumps({"challengers":{}}),encoding="utf-8")
            p["lab"].write_text(json.dumps({
                "evidence_patterns":[{
                    "pattern_id":"BRP-1","pattern_key":"abc","belief_id":"spx.trend.bullish",
                    "status":"REPLICATED","atoms":["e::momentum::+1","r::risk_on"],
                    "expected_outcome":True,"horizon_bucket":"6-24h",
                    "discovery":{"n":12,"lift":.10,"mdl_gain_bits":2.0},
                    "holdout":{"n":6,"lift":.08,"success_rate":.75},
                }]
            }),encoding="utf-8")
            exp=[]
            for i in range(20):
                exp.append(json.dumps({
                    "status":"SETTLED","engine":"test_engine","action":"LONG",
                    "decision":{"regime":"risk_on"},
                    "outcome":{"net_return_fraction":-.01,"exit_reason":"STOP_LOSS"},
                }))
            p["experience"].write_text("\n".join(exp)+"\n",encoding="utf-8")
            p["regret"].write_text(json.dumps({
                "challenger_hypotheses":[{
                    "component":"entry_score_below_threshold",
                    "horizon_sessions":5,
                    "status":"ELIGIBLE_FOR_CHALLENGER_HOLDOUT",
                    "evidence":{"observations":35,"mean_incremental_r_vs_champion":.12},
                    "suggested_experiment":{"type":"conditional_score_challenger"},
                }]
            }),encoding="utf-8")

            payload=run(
                state_path=p["state"], public_path=p["public"], audit_path=p["audit"],
                belief_state_path=p["belief_state"], belief_closed_loop_path=p["closed"],
                decision_lab_public_path=p["lab"], experience_store_path=p["experience"],
                trading_regret_path=p["regret"], belief_policy_path=p["policy"],
                v3_registry_path=p["v3"], now="2026-10-10T00:00:00Z",
            )
            kinds={x["kind"] for x in payload["hypotheses"]}
            self.assertIn("evidence_pattern_modifier",kinds)
            self.assertIn("recurring_decision_failure",kinds)
            self.assertIn("opportunity_regret_component",kinds)
            self.assertGreaterEqual(payload["summary"]["delegated_actions"],1)
            self.assertEqual(json.loads(p["policy"].read_text())["overrides"],{})

    def test_v3_requires_frozen_discovery_then_new_matched_oos_before_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            from datetime import datetime, timedelta, timezone
            root=Path(tmp)
            p=self._paths(root)
            self._write_base_policy(p["policy"])
            self._write_base_v3(p["v3"])
            p["closed"].write_text(json.dumps({"challengers":{}}),encoding="utf-8")
            p["lab"].write_text(json.dumps({"evidence_patterns":[]}),encoding="utf-8")

            forecasts=[]
            verifications=[]
            def add_pair(idx, at):
                target=at+timedelta(hours=24)
                target_text=target.isoformat().replace("+00:00","Z")
                at_text=at.isoformat().replace("+00:00","Z")
                control_id=f"control-{idx}"
                candidate_id=f"candidate-{idx}"
                forecasts.extend([
                    {
                        "forecast_id":control_id,
                        "belief_id":"spx.trend.bullish",
                        "entity":"SPX",
                        "forecast_at":at_text,
                        "target_at":target_text,
                        "horizon_hours":24,
                        "predicted_probability":.55,
                        "metadata":{"outcome_spec":{"kind":"price_above","symbol":"SPY","reference":700.0}},
                    },
                    {
                        "forecast_id":candidate_id,
                        "belief_id":"spx.rates.supportive",
                        "entity":"SPX",
                        "forecast_at":at_text,
                        "target_at":target_text,
                        "horizon_hours":24,
                        "predicted_probability":.80,
                        "metadata":{"outcome_spec":{"kind":"price_above","symbol":"SPY","reference":700.0}},
                    },
                ])
                for fid,bid,pred in ((control_id,"spx.trend.bullish",.55),(candidate_id,"spx.rates.supportive",.80)):
                    verifications.append({
                        "forecast_id":fid,
                        "belief_id":bid,
                        "predicted_probability":pred,
                        "forecast_at":at_text,
                        "outcome":True,
                        "calibration_eligible":True,
                    })

            start=datetime(2026,9,20,tzinfo=timezone.utc)
            for i in range(50):
                add_pair(i,start+timedelta(hours=i))
            p["belief_state"].write_text(json.dumps({"forecasts":forecasts,"verifications":verifications}),encoding="utf-8")

            first=run(
                state_path=p["state"], public_path=p["public"], audit_path=p["audit"],
                belief_state_path=p["belief_state"], belief_closed_loop_path=p["closed"],
                decision_lab_public_path=p["lab"], experience_store_path=None,
                trading_regret_path=None, belief_policy_path=p["policy"],
                v3_registry_path=p["v3"], now="2026-10-01T00:00:00Z",
            )
            v3_candidates=[x for x in first["candidates"] if x["candidate_type"]=="belief_v3"]
            self.assertEqual(len(v3_candidates),1)
            self.assertEqual(v3_candidates[0]["status"],"OOS_RUNNING")
            self.assertEqual(json.loads(p["v3"].read_text())["active"],{})

            future=datetime(2026,10,2,tzinfo=timezone.utc)
            for i in range(50,100):
                add_pair(i,future+timedelta(hours=i-50))
            p["belief_state"].write_text(json.dumps({"forecasts":forecasts,"verifications":verifications}),encoding="utf-8")

            second=run(
                state_path=p["state"], public_path=p["public"], audit_path=p["audit"],
                belief_state_path=p["belief_state"], belief_closed_loop_path=p["closed"],
                decision_lab_public_path=p["lab"], experience_store_path=None,
                trading_regret_path=None, belief_policy_path=p["policy"],
                v3_registry_path=p["v3"], now="2026-10-10T00:00:00Z",
            )
            registry=json.loads(p["v3"].read_text())
            self.assertIn("spx.rates.supportive",registry["active"])
            active=registry["active"]["spx.rates.supportive"]
            self.assertFalse(active["trade_execution"])
            promoted=[x for x in second["candidates"] if x["component_id"]=="belief_v3:spx.rates.supportive"][0]
            self.assertEqual(promoted["status"],"PROMOTED")

    def test_central_monitor_rolls_back_degrading_belief_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=self._paths(root)
            self._write_base_v3(p["v3"])
            p["policy"].write_text(json.dumps({
                "schema_version":"belief-core-production-overrides-v1",
                "updated_at":None,
                "authority":{},
                "overrides":{
                    "__GLOBAL__":{
                        "active":True,
                        "belief_id":"__GLOBAL__",
                        "version":"prod-1",
                        "candidate_id":"cand-1",
                        "transform":{"type":"logit_affine_v1","intercept":0.0,"slope":1.0},
                        "promoted_at":"2026-09-28T00:00:00Z",
                    }
                },
                "history":[],
            }),encoding="utf-8")
            forecasts,verifications=[],[]
            for i in range(30):
                fid=f"r{i}"
                at=f"2026-09-29T{(i%24):02d}:{(i//24)*10:02d}:00Z"
                y=bool(i%2)
                forecasts.append({
                    "forecast_id":fid,"belief_id":"spx.trend.bullish","entity":"SPX",
                    "forecast_at":at,"target_at":"2026-10-01T00:00:00Z","horizon_hours":24,
                    "predicted_probability":.90,
                    "metadata":{"raw_probability":.50},
                })
                verifications.append({
                    "forecast_id":fid,"belief_id":"spx.trend.bullish",
                    "predicted_probability":.90,"forecast_at":at,"outcome":y,
                    "calibration_eligible":True,
                })
            p["belief_state"].write_text(json.dumps({"forecasts":forecasts,"verifications":verifications}),encoding="utf-8")
            p["closed"].write_text(json.dumps({"challengers":{}}),encoding="utf-8")
            p["lab"].write_text(json.dumps({"evidence_patterns":[]}),encoding="utf-8")

            payload=run(
                state_path=p["state"], public_path=p["public"], audit_path=p["audit"],
                belief_state_path=p["belief_state"], belief_closed_loop_path=p["closed"],
                decision_lab_public_path=p["lab"], experience_store_path=None,
                trading_regret_path=None, belief_policy_path=p["policy"],
                v3_registry_path=p["v3"], now="2026-10-10T00:00:00Z",
            )
            policy=json.loads(p["policy"].read_text())
            self.assertFalse(policy["overrides"]["__GLOBAL__"]["active"])
            self.assertEqual(payload["summary"]["rollback_events"],1)
            self.assertEqual(payload["summary"]["retirement_actions"],1)


if __name__ == "__main__":
    unittest.main()
