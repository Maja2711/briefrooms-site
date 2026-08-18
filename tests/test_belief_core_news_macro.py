from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_adapter_contract import AdapterResult, Observation
from belief_core import BeliefCore, iso_z
from belief_core_live import BELIEFS, freeze_set
from belief_external_live import run_external_cycle
from belief_llm_interpreter import (
    Interpretation,
    interpretation_to_evidence,
    validate_interpretation_payload,
)
from belief_macro_calendar_adapter import (
    MacroEventCalendarAdapter,
    calendar_event_to_observation,
    event_risk_evidence,
    parse_bls_ics,
)
from belief_market_data_adapter import Bar, MarketSnapshot
from belief_news_event_adapter import parse_rss

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def primary_observation(now: datetime) -> Observation:
    return Observation.make(
        adapter="news_event",
        metric="primary_event_document",
        entity="FED",
        observed_at=iso_z(now),
        value="Federal Reserve policy statement",
        unit="text_event",
        source="Federal Reserve press releases",
        source_type="primary",
        source_ref="https://www.federalreserve.gov/newsevents/pressreleases/example.htm",
        reliability=.98,
        independence_cluster="primary-event:fed-example",
        tags=("news_event", "primary_source", "fed_policy"),
        metadata={"document_text": "The Federal Reserve issued a policy statement about monetary conditions."},
    )


def interpreted_event(now: datetime):
    primary = primary_observation(now)
    interpretation = Interpretation(
        belief_id="spx.financial_conditions.supportive",
        direction=-1,
        strength=.72,
        confidence=.86,
        materiality=.90,
        event_type="fed_policy",
        market_scope="macro",
        horizon_hours=24,
        summary="The policy statement is interpreted as tighter broad financial conditions.",
        alternative_hypothesis="Markets may already have priced the policy stance.",
        model="gemini-test",
    )
    result = interpretation_to_evidence(primary, interpretation)
    assert result is not None
    return primary, result


def market_snapshot(now: datetime) -> MarketSnapshot:
    values = {
        "SPY": 100.0,
        "RSP": 50.0,
        "IWM": 200.0,
        "^VIX": 18.0,
        "HYG": 80.0,
        "LQD": 100.0,
        "TLT": 90.0,
        "UUP": 25.0,
    }
    bars = {
        symbol: [Bar(timestamp=now.astimezone(UTC), close=value)]
        for symbol, value in values.items()
    }
    return MarketSnapshot(bars)


class InterpretationContractTest(unittest.TestCase):
    def test_rejects_invented_belief_id(self) -> None:
        with self.assertRaises(ValueError):
            validate_interpretation_payload({
                "belief_id": "spx.magic.always_up",
                "direction": 1,
                "strength": .7,
                "confidence": .9,
                "materiality": .9,
                "event_type": "other",
                "market_scope": "broad_market",
                "horizon_hours": 24,
                "summary": "invalid",
                "alternative_hypothesis": "none",
            })

    def test_llm_evidence_keeps_primary_provenance(self) -> None:
        now = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)
        primary, result = interpreted_event(now)
        self.assertEqual(result.evidence.source_type, "derived")
        self.assertEqual(result.evidence.metadata["primary_observation_id"], primary.observation_id)
        self.assertEqual(result.evidence.metadata["primary_source_ref"], primary.source_ref)
        self.assertEqual(result.observation.metadata["upstream_source_ref"], primary.source_ref)
        self.assertEqual(result.evidence.independence_cluster, primary.independence_cluster)


class PrimarySourceParsingTest(unittest.TestCase):
    def test_rss_parser_keeps_primary_source_url_and_timestamp(self) -> None:
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        rss = """<?xml version='1.0'?>
        <rss><channel><item>
          <title>Policy statement</title>
          <link>https://www.federalreserve.gov/newsevents/pressreleases/test.htm</link>
          <pubDate>Tue, 18 Aug 2026 13:30:00 GMT</pubDate>
          <description>Federal Reserve issued a statement.</description>
        </item></channel></rss>"""
        rows = parse_rss(rss, source="Federal Reserve press releases", now=now, lookback_hours=36)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source_ref, "https://www.federalreserve.gov/newsevents/pressreleases/test.htm")
        self.assertEqual(rows[0].published_at, "2026-08-18T13:30:00Z")
        self.assertEqual(rows[0].entity, "FED")

    def test_bls_calendar_event_and_imminent_risk_evidence(self) -> None:
        now = datetime(2026, 8, 18, 7, 30, tzinfo=NY)
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:cpi-20260818
DTSTART:20260818T083000
SUMMARY:Consumer Price Index
URL:https://www.bls.gov/cpi/
END:VEVENT
END:VCALENDAR
"""
        rows = parse_bls_ics(ics, now=now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].importance, "high")
        primary = calendar_event_to_observation(rows[0], now)
        derived, evidence = event_risk_evidence(primary, now=now)
        self.assertEqual(evidence.belief_id, "spx.volatility.benign")
        self.assertEqual(evidence.direction, -1)
        self.assertEqual(evidence.metadata["primary_source_ref"], "https://www.bls.gov/cpi/")
        self.assertEqual(derived.metadata["upstream_observation_id"], primary.observation_id)


class _AvailableInterpreter:
    available = True
    model = "gemini-test"


class _FakeNewsAdapter:
    def __init__(self, now: datetime) -> None:
        self.interpreter = _AvailableInterpreter()
        self.primary, self.result = interpreted_event(now)

    def run(self, now, *, seen_primary_observation_ids=()):
        if self.primary.observation_id in set(seen_primary_observation_ids):
            return AdapterResult("news_event", (self.primary,), ())
        return AdapterResult(
            "news_event",
            (self.primary, self.result.observation),
            (self.result.evidence,),
        )


class _EmptyMacroAdapter:
    def run(self, now):
        return AdapterResult("macro_event_calendar", (), ())


class ExternalCycleAndFrozenForecastTest(unittest.TestCase):
    def test_external_cycle_retry_is_idempotent(self) -> None:
        now = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "core"
            news = _FakeNewsAdapter(now)
            macro = _EmptyMacroAdapter()
            first = run_external_cycle(state_dir, now, news_adapter=news, macro_adapter=macro)
            second = run_external_cycle(state_dir, now + timedelta(minutes=1), news_adapter=news, macro_adapter=macro)
            self.assertEqual(first["news_evidence"], 1)
            self.assertEqual(first["observations_written"], 2)
            self.assertEqual(second["news_evidence"], 0)
            self.assertEqual(second["observations_written"], 0)
            self.assertEqual(second["processed_event_observation_ids"], 1)
            core = BeliefCore(state_dir)
            self.assertEqual(len(core.evidence), 1)
            self.assertTrue(core.verify_ledger_integrity()["valid"])

    def test_news_evidence_is_frozen_and_verification_keeps_same_snapshot(self) -> None:
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        target = now + timedelta(hours=6)
        _, result = interpreted_event(now)
        with tempfile.TemporaryDirectory() as tmp:
            core = BeliefCore(Path(tmp) / "core")
            core.register_beliefs(BELIEFS)
            core.ingest([result.evidence])
            core.recompute(now)
            snapshot = market_snapshot(now)
            freeze_set(core, snapshot, now, target, "BRACE+BRACE-SPX", "test:news", "neutral")

            forecast = next(
                row for row in core.forecasts.values()
                if row.belief_id == "spx.financial_conditions.supportive"
            )
            frozen_ids = {row["evidence_id"] for row in forecast.evidence_snapshot}
            self.assertIn(result.evidence.evidence_id, frozen_ids)
            self.assertEqual(forecast.predicted_probability, core.beliefs[forecast.belief_id].probability)

            verification = core.verify_forecast(
                forecast.forecast_id,
                outcome=False,
                verified_at=target,
                outcome_source="test deterministic outcome",
                outcome_ref="test://outcome",
            )
            self.assertEqual(verification.predicted_probability, forecast.predicted_probability)
            self.assertEqual(verification.evidence_snapshot, forecast.evidence_snapshot)
            self.assertIn(result.evidence.evidence_id, {row["evidence_id"] for row in verification.evidence_snapshot})
            self.assertTrue(verification.calibration_eligible)


if __name__ == "__main__":
    unittest.main()
