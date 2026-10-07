from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_market_data_adapter import Bar, MarketSnapshot
from belief_news_event_adapter import parse_rss
import daily_eurusd_belief_decision as decision
import provenance_contract as provenance
import daily_eurusd_spot as base
import daily_eurusd_spot_v19 as v19
import daily_eurusd_spot_v18 as v18

UTC = timezone.utc


def _state(now: datetime, values: dict[str, tuple[float, float]]) -> dict:
    evidence = []
    beliefs = []
    for index, (belief_id, (probability, confidence)) in enumerate(values.items()):
        eid = f"ev-{index}"
        evidence.append({
            "evidence_id": eid,
            "observed_at": (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        })
        beliefs.append({
            "belief_id": belief_id,
            "probability": probability,
            "confidence": confidence,
            "representative_evidence_ids": [eid],
        })
    return {"schema_version": 2, "beliefs": beliefs, "evidence": evidence}


def _epistemic_state(now: datetime, values: dict[str, tuple[float, float]]) -> dict:
    states = {}
    for index, (belief_id, (probability, confidence)) in enumerate(values.items()):
        states[belief_id] = {
            "state_id": f"eurusd-state-{index}",
            "topic": belief_id,
            "probability": probability,
            "confidence": confidence,
            "delta_probability": 0.01,
            "contradiction": 0.10,
            "freshness": 0.90,
            "audit_status": "clean",
            "member_belief_ids": [belief_id],
            "dominant_support_evidence_ids": [f"e-{index}"],
            "dominant_opposition_evidence_ids": [],
            "drilldown_required": False,
            "drilldown_reasons": [],
        }
    return {
        "contract_version": "belief-epistemic-state-v1",
        "created_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "authority": {
            "llm_may_ignore_aggregate": False,
            "llm_may_override_probability": False,
        },
        "controls": {
            "belief_core_writeback_enabled": False,
        },
        "states": states,
    }


def _snapshot(now: datetime) -> MarketSnapshot:
    rows = []
    price = 1.1200
    for idx in range(90):
        p = price + idx * 0.00002
        rows.append(Bar(
            timestamp=now - timedelta(minutes=30 * (89 - idx)),
            open=p - 0.00005,
            high=p + 0.00020,
            low=p - 0.00020,
            close=p,
        ))
    return MarketSnapshot({base.EURUSD: rows})


class BeliefDecisionTest(unittest.TestCase):
    def test_final_direction_is_synthesized_from_belief_core(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        state = _state(now, {
            "eurusd.trend.bullish": (.78, .90),
            "eurusd.usd_environment.supportive": (.70, .80),
            "eurusd.us_rates_pressure.supportive": (.62, .70),
            "eurusd.macro_surprise.supportive": (.66, .75),
            "eurusd.policy_differential.supportive": (.60, .70),
        })
        result = decision.synthesize(state, observed_at=now)
        self.assertEqual(result["direction"], "LONG")
        self.assertGreaterEqual(result["score"], 60.0)
        self.assertEqual(result["decision_source"], "NATIVE_BELIEF_FIRST")
        self.assertEqual(result["epistemic_source"], "BELIEF_CORE_CF07")
        self.assertFalse(result["legacy_raw_score_direction_authority"])

    def test_slightly_narrower_production_neutral_band_allows_qualified_43_74_short(self):
        now = datetime(2026, 10, 7, 14, 7, 49, tzinfo=UTC)
        state = _state(now, {
            "eurusd.trend.bullish": (.37, .44),
            "eurusd.usd_environment.supportive": (.32, .56),
            "eurusd.us_rates_pressure.supportive": (.54, .37),
        })
        consumer = {
            "available": True,
            "contract": "epistemic-consumer-interface-v1",
            "aggregate_authoritative": True,
            "aggregate_probability": 0.4374,
            "aggregate_confidence": 0.2502,
            "coverage_weight": 0.533874,
        }
        result = decision.synthesize(state, observed_at=now, authoritative_consumer=consumer)
        self.assertEqual(result["thresholds"], {"long": 56.0, "short": 44.0})
        self.assertEqual(result["score"], 43.74)
        self.assertEqual(result["direction"], "SHORT")

    def test_45_score_remains_neutral_after_participation_change(self):
        now = datetime(2026, 10, 7, 14, 7, 49, tzinfo=UTC)
        state = _state(now, {
            "eurusd.trend.bullish": (.45, .60),
            "eurusd.usd_environment.supportive": (.45, .60),
            "eurusd.us_rates_pressure.supportive": (.45, .60),
        })
        consumer = {
            "available": True,
            "contract": "epistemic-consumer-interface-v1",
            "aggregate_authoritative": True,
            "aggregate_probability": 0.45,
            "aggregate_confidence": 0.30,
            "coverage_weight": 0.60,
        }
        result = decision.synthesize(state, observed_at=now, authoritative_consumer=consumer)
        self.assertEqual(result["direction"], "FLAT")
        self.assertIn("belief_score_neutral", result["reasons"])

    def test_missing_belief_state_fails_closed(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        result = decision.synthesize({}, observed_at=now)
        self.assertEqual(result["direction"], "FLAT")
        self.assertIn("belief_state_unavailable", result["reasons"])

    def test_missing_data_is_not_neutral_vote(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        state = _state(now, {
            "eurusd.trend.bullish": (.80, .90),
        })
        result = decision.synthesize(state, observed_at=now)
        self.assertEqual(result["direction"], "FLAT")
        self.assertIn("insufficient_belief_coverage", result["reasons"])
        self.assertLess(result["coverage_weight"], 0.45)

    def test_macro_and_policy_are_part_of_final_direction_not_post_signal_veto(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        state = _state(now, {
            "eurusd.trend.bullish": (.62, .70),
            "eurusd.usd_environment.supportive": (.58, .60),
            "eurusd.us_rates_pressure.supportive": (.56, .60),
            "eurusd.macro_surprise.supportive": (.05, 1.00),
            "eurusd.policy_differential.supportive": (.05, 1.00),
        })
        result = decision.synthesize(state, observed_at=now)
        self.assertNotEqual(result["direction"], "LONG")
        ids = {row["belief_id"] for row in result["used_beliefs"]}
        self.assertIn("eurusd.macro_surprise.supportive", ids)
        self.assertIn("eurusd.policy_differential.supportive", ids)

    def test_production_epistemic_interface_is_the_decision_input(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        payload = _epistemic_state(now, {
            "eurusd.trend.bullish": (.80, .90),
            "eurusd.usd_environment.supportive": (.70, .80),
            "eurusd.us_rates_pressure.supportive": (.65, .70),
            "eurusd.macro_surprise.supportive": (.65, .70),
            "eurusd.policy_differential.supportive": (.60, .70),
        })
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ep = root / "epistemic_state.json"
            ep.write_text(json.dumps(payload), encoding="utf-8")
            raw = root / "state.json"
            raw.write_text("{}", encoding="utf-8")
            (root / "observations.jsonl").write_text("", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "BELIEF_EPISTEMIC_STATE": str(ep),
                    "BELIEF_CORE_STATE": str(raw),
                    "DAILY_EURUSD_REQUIRE_EPISTEMIC": "1",
                },
                clear=False,
            ):
                output = v19.build_output(_snapshot(now), {"trades": []})
        self.assertEqual(output.metadata["final_decision"]["direction"], "LONG")
        consumer = output.metadata["final_decision"]["epistemic_consumer"]
        self.assertEqual(consumer["contract"], "epistemic-consumer-interface-v1")
        self.assertEqual(consumer["consumer"], "DAILY_EURUSD")
        self.assertTrue(consumer["available"])
        self.assertFalse(consumer["consumer_may_override_probability"])
        self.assertTrue(output.metadata["final_decision"]["epistemic_aggregate_authoritative"])
        provenance.verify_attached(output.metadata["final_decision"])
        self.assertEqual(
            output.metadata["final_decision"]["provenance"]["domain_provenance"]["epistemic_consumer_contract"],
            "epistemic-consumer-interface-v1",
        )
        self.assertAlmostEqual(
            output.score,
            100.0 * float(consumer["aggregate_probability"]),
            places=2,
        )

    def test_production_requires_epistemic_interface_and_fails_closed_without_it(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        with mock.patch.dict(
            os.environ,
            {
                "BELIEF_EPISTEMIC_STATE": "",
                "BELIEF_CORE_STATE": "",
                "DAILY_EURUSD_REQUIRE_EPISTEMIC": "1",
            },
            clear=False,
        ):
            output = v19.build_output(_snapshot(now), {"trades": []})
        self.assertEqual(output.metadata["final_decision"]["direction"], "FLAT")
        self.assertEqual(
            output.metadata["final_decision"]["epistemic_consumer"]["reason"],
            "required_epistemic_consumer_state_unavailable",
        )

    def test_v19_build_does_not_use_legacy_raw_direction_score(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        state = _state(now, {
            "eurusd.trend.bullish": (.80, .90),
            "eurusd.usd_environment.supportive": (.70, .80),
            "eurusd.us_rates_pressure.supportive": (.65, .70),
            "eurusd.macro_surprise.supportive": (.65, .70),
            "eurusd.policy_differential.supportive": (.60, .70),
        })
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            (root / "observations.jsonl").write_text("", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "BELIEF_CORE_STATE": str(path),
                    "BELIEF_EPISTEMIC_STATE": "",
                    "DAILY_EURUSD_REQUIRE_EPISTEMIC": "",
                },
                clear=False,
            ):
                with mock.patch.object(base, "_raw_state", side_effect=AssertionError("legacy raw direction called")):
                    output = v19.build_output(_snapshot(now), {"trades": []})
        self.assertEqual(output.decision_mode, "WITH")
        self.assertEqual(output.metadata["direction_authority"]["owner"], "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE")
        self.assertEqual(output.metadata["final_decision"]["decision_source"], "NATIVE_BELIEF_FIRST")

    def test_flat_runtime_clone_used_by_v18_cycle_stays_v19(self):
        now = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
        candidate = v19.DailyEngineOutput(
            instrument="EUR/USD",
            timestamp=now.isoformat().replace("+00:00", "Z"),
            direction="FLAT",
            score=50.0,
            confidence=0.0,
            entry=None,
            stop=None,
            target=None,
            horizon="intraday_to_27h",
            engine_version=v19.ENGINE_VERSION,
            status="NO_TRADE",
            decision_mode="WITH",
            metadata={"candidate": {"direction": "FLAT", "accepted": False}},
        ).validate()
        projected = v18._clone(candidate)
        self.assertEqual(projected.engine_version, v19.ENGINE_VERSION)
        self.assertEqual(projected.decision_mode, "WITH")
        self.assertEqual(projected.direction, "FLAT")
        self.assertEqual(projected.status, "NO_TRADE")

    def test_open_and_closed_runtime_projection_stays_v19_with_mode(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        candidate = v19.DailyEngineOutput(
            instrument="EUR/USD",
            timestamp=now.isoformat().replace("+00:00", "Z"),
            direction="LONG",
            score=65.0,
            confidence=.6,
            entry=1.12,
            stop=1.11,
            target=1.138,
            horizon="intraday_to_27h",
            engine_version=v19.ENGINE_VERSION,
            status="SIGNAL",
            decision_mode="WITH",
            metadata={
                "final_decision": {"direction": "LONG"},
                "candidate": {"direction": "LONG", "score": 65.0, "confidence": .6},
                "risk": {},
            },
        ).validate()
        position = {
            "trade_id": "legacy-position",
            "status": "OPEN",
            "direction": "LONG",
            "entry": 1.12,
            "stop": 1.11,
            "target": 1.138,
            "opened_at": now.isoformat().replace("+00:00", "Z"),
            "entry_score": 65.0,
            "entry_confidence": .6,
            "engine_version": "eurusd-daily-spot-v1.8.0",
        }
        opened = v19._open_output_v19(candidate, position, 1.121)
        self.assertEqual(opened.engine_version, v19.ENGINE_VERSION)
        self.assertEqual(opened.decision_mode, "WITH")
        self.assertFalse(opened.metadata["runtime_projection"]["historical_position_rewritten"])

    def test_fse_and_contextual_learner_have_zero_v19_production_influence(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        candidate = v19.DailyEngineOutput(
            instrument="EUR/USD",
            timestamp=now.isoformat().replace("+00:00", "Z"),
            direction="LONG",
            score=70.0,
            confidence=.7,
            entry=1.12,
            stop=1.11,
            target=1.138,
            horizon="intraday_to_27h",
            engine_version=v19.ENGINE_VERSION,
            status="SIGNAL",
            decision_mode="WITH",
            metadata={"final_decision": {"direction": "LONG"}},
        ).validate()
        with mock.patch.object(v19.v18, "_live_recommendation", side_effect=AssertionError("legacy contextual production path called")):
            out = v19._fresh_policy_shadow(candidate, None, [], now)
        self.assertEqual(out.direction, "LONG")
        self.assertFalse(out.metadata["contextual_entry_policy"]["decision_influence"])
        self.assertFalse(out.metadata["contextual_entry_policy"]["FSE_production_influence"])

    def test_v19_runtime_fetches_only_direct_eurusd_market_fact(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        snapshot = _snapshot(now)

        class Client:
            def bars(self, symbol, range_="10d", interval="30m"):
                if symbol != base.EURUSD:
                    raise AssertionError(f"direct explanatory market read leaked into v1.9: {symbol}")
                return list(snapshot.bars[base.EURUSD])

        fetched = v19.fetch_snapshot(Client())
        self.assertEqual(set(fetched.bars), {base.EURUSD})

    def test_official_ecb_feed_is_normalized_as_ecb_primary_event(self):
        now = datetime(2026, 10, 29, 14, 0, tzinfo=UTC)
        xml = """<rss><channel><item>
        <title>Monetary policy decisions</title>
        <link>https://www.ecb.europa.eu/press/pr/date/2026/html/example.en.html</link>
        <pubDate>Thu, 29 Oct 2026 13:15:00 GMT</pubDate>
        <description>The Governing Council decided on interest rates.</description>
        </item></channel></rss>"""
        rows = parse_rss(
            xml,
            source="European Central Bank press releases",
            now=now,
            lookback_hours=36,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].entity, "ECB")
        self.assertEqual(rows[0].category_hint, "ecb_primary")

    def test_calendar_coverage_failure_is_not_treated_as_clear(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path = root / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            coverage = {
                "adapter": "macro_event_calendar",
                "metric": "calendar_coverage",
                "observed_at": now.isoformat().replace("+00:00", "Z"),
                "metadata": {
                    "complete": False,
                    "sources": {"BLS": {"status": "failed"}, "ECB": {"status": "ok"}},
                },
            }
            (root / "observations.jsonl").write_text(json.dumps(coverage) + "\n", encoding="utf-8")
            safety = decision.calendar_safety(observed_at=now, state_path=state_path, decision={})
        self.assertTrue(safety["blocked"])
        self.assertEqual(safety["reason"], "belief_calendar_coverage_failed")

    def test_complete_fresh_calendar_without_event_is_clear(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path = root / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            coverage = {
                "adapter": "macro_event_calendar",
                "metric": "calendar_coverage",
                "observed_at": now.isoformat().replace("+00:00", "Z"),
                "metadata": {
                    "complete": True,
                    "sources": {
                        key: {"status": "ok"}
                        for key in ("BLS", "BEA", "FOMC", "EUROSTAT", "ECB")
                    },
                },
            }
            (root / "observations.jsonl").write_text(json.dumps(coverage) + "\n", encoding="utf-8")
            safety = decision.calendar_safety(observed_at=now, state_path=state_path, decision={})
        self.assertFalse(safety["blocked"])
        self.assertEqual(safety["reason"], "clear")

    def test_imminent_belief_calendar_blocks_execution_not_final_decision(self):
        now = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)
        state = _state(now, {
            "eurusd.trend.bullish": (.80, .90),
            "eurusd.usd_environment.supportive": (.70, .80),
            "eurusd.us_rates_pressure.supportive": (.65, .70),
            "eurusd.macro_surprise.supportive": (.65, .70),
            "eurusd.policy_differential.supportive": (.60, .70),
        })
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            event_at = now + timedelta(minutes=20)
            obs = {
                "adapter": "macro_event_calendar",
                "metric": "scheduled_macro_event",
                "source": "official",
                "source_ref": "test://event",
                "metadata": {
                    "importance": "high",
                    "title": "High impact release",
                    "event_at": event_at.isoformat().replace("+00:00", "Z"),
                    "region": "US",
                },
            }
            (root / "observations.jsonl").write_text(json.dumps(obs) + "\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "BELIEF_CORE_STATE": str(path),
                    "BELIEF_EPISTEMIC_STATE": "",
                    "DAILY_EURUSD_REQUIRE_EPISTEMIC": "",
                },
                clear=False,
            ):
                output = v19.build_output(_snapshot(now), {"trades": []})
        self.assertEqual(output.metadata["final_decision"]["direction"], "LONG")
        self.assertFalse(output.metadata["execution_admission"]["allowed"])
        self.assertIn("belief_high_impact_event_imminent", output.metadata["execution_admission"]["reasons"])


if __name__ == "__main__":
    unittest.main()
