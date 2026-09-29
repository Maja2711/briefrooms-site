from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gse_event_probability_threat_engine import (
    EventProbabilityThreatEngine,
    FORECAST_HORIZONS_H,
)

UTC = timezone.utc


def evidence(
    eid: str,
    title: str,
    when: datetime,
    source: str,
    *,
    source_type: str = "secondary",
    reliability: float = 0.8,
) -> dict:
    return {
        "evidence_id": eid,
        "title": title,
        "text": title,
        "published_at": when.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "source": source,
        "source_type": source_type,
        "source_ref": f"https://example.test/{eid}",
        "reliability": reliability,
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class EventThreatEstimateTests(unittest.TestCase):
    def test_precursor_evidence_raises_probability_above_stored_prior(self):
        now = datetime(2026, 9, 29, 12, 17, tzinfo=UTC)
        rows = [
            evidence("e1", "Russia troop buildup and deployment near Poland raises NATO readiness", now - timedelta(hours=4), "news-a"),
            evidence("e2", "Russian military convoy and logistics activity near Suwalki corridor", now - timedelta(hours=8), "news-b"),
            evidence("e3", "Russia-linked GPS jamming affects Poland and the Baltic region", now - timedelta(hours=16), "news-c"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "gse_evidence.jsonl", rows)
            engine = EventProbabilityThreatEngine(root)
            estimate = engine.estimate("russia_attack_poland", 720, now)
            self.assertGreater(estimate.predicted_probability, estimate.prior_probability)
            self.assertGreaterEqual(estimate.independent_sources_7d, 3)
            self.assertIn("force_posture", estimate.precursor_categories)
            self.assertIn("hybrid_cyber", estimate.precursor_categories)

    def test_target_specific_evidence_does_not_leak_to_other_country(self):
        now = datetime(2026, 9, 29, 12, 17, tzinfo=UTC)
        rows = [
            evidence("e1", "Russia military buildup and deployment near Lithuania", now - timedelta(hours=4), "news-a"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "gse_evidence.jsonl", rows)
            engine = EventProbabilityThreatEngine(root)
            lithuania = engine.estimate("russia_attack_lithuania", 168, now)
            latvia = engine.estimate("russia_attack_latvia", 168, now)
            self.assertGreater(lithuania.predicted_probability, lithuania.prior_probability)
            self.assertEqual(latvia.predicted_probability, latvia.prior_probability)


class EventThreatFreezeVerificationTests(unittest.TestCase):
    def test_daily_freeze_is_idempotent(self):
        now = datetime(2026, 9, 29, 0, 17, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "gse_evidence.jsonl", [])
            engine = EventProbabilityThreatEngine(root)
            first = engine.run(now)
            self.assertEqual(first["last_cycle"]["forecasts_frozen"], len(FORECAST_HORIZONS_H) * 5)
            second = engine.run(now + timedelta(minutes=20))
            self.assertEqual(second["last_cycle"]["forecasts_frozen"], 0)

    def test_hypothetical_attack_headline_does_not_count_as_realization(self):
        start = datetime(2026, 9, 29, 0, 17, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "gse_evidence.jsonl", [])
            engine = EventProbabilityThreatEngine(root)
            engine.run(start)
            rows = [
                evidence(
                    "h1",
                    "Officials warn Russia could attack Poland in a future scenario",
                    start + timedelta(days=2),
                    "news-a",
                ),
                evidence(
                    "h2",
                    "Analysts say Russia might invade Poland in a war game scenario",
                    start + timedelta(days=3),
                    "news-b",
                ),
            ]
            write_jsonl(root / "gse_evidence.jsonl", rows)

            # Build sufficient observation coverage so a negative outcome is eligible.
            cycles = []
            for i in range(50):
                at = start + timedelta(hours=3 * (i + 1))
                cycles.append({
                    "cycle_id": f"c{i}",
                    "scan_at": at.isoformat().replace("+00:00", "Z"),
                    "evidence_30d": len(rows),
                    "source_count_30d": 2,
                })
            write_jsonl(root / "gse_event_threat_cycles.jsonl", cycles)

            due = engine.verify_due(start + timedelta(days=8))
            poland = [
                row for row in due
                if row.event_type == "russia_attack_poland" and row.horizon_hours == 168
            ]
            self.assertEqual(len(poland), 1)
            self.assertFalse(poland[0].outcome)
            self.assertTrue(poland[0].calibration_eligible)

    def test_two_independent_reports_confirm_positive_and_score_brier(self):
        start = datetime(2026, 9, 29, 0, 17, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "gse_evidence.jsonl", [])
            engine = EventProbabilityThreatEngine(root)
            engine.run(start)
            rows = [
                evidence("a1", "Russia attacked Lithuania in an armed attack across the border", start + timedelta(days=2), "GDELT:source-a"),
                evidence("a2", "Russian forces attacked Lithuania and troops entered across the border", start + timedelta(days=2, hours=1), "GDELT:source-b"),
            ]
            write_jsonl(root / "gse_evidence.jsonl", rows)
            due = engine.verify_due(start + timedelta(days=8))
            lithuania = [
                row for row in due
                if row.event_type == "russia_attack_lithuania" and row.horizon_hours == 168
            ]
            self.assertEqual(len(lithuania), 1)
            verification = lithuania[0]
            self.assertTrue(verification.outcome)
            self.assertTrue(verification.calibration_eligible)
            self.assertGreaterEqual(verification.confirmation_sources, 2)
            self.assertGreater(verification.brier_score, 0.0)

            calibration = engine.calibration()
            self.assertGreaterEqual(calibration["overall"]["count"], 1)
            self.assertIsNotNone(calibration["overall"]["delta_brier_vs_prior"])
            self.assertFalse(calibration["automatic_tuning_enabled"])

    def test_negative_without_coverage_is_not_used_for_calibration(self):
        start = datetime(2026, 9, 29, 0, 17, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "gse_evidence.jsonl", [])
            engine = EventProbabilityThreatEngine(root)
            engine.run(start)
            due = engine.verify_due(start + timedelta(days=8))
            poland = [
                row for row in due
                if row.event_type == "russia_attack_poland" and row.horizon_hours == 168
            ]
            self.assertEqual(len(poland), 1)
            self.assertFalse(poland[0].calibration_eligible)
            calibration = engine.calibration()
            self.assertEqual(calibration["overall"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
