from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from geopolitical_scenario_engine import (
    GeoEvidence,
    GeopoliticalScenarioEngine,
    ScenarioEngine,
    TransmissionGraph,
)

UTC = timezone.utc


def ev(eid: str, title: str, when: datetime, source: str = "test", source_type: str = "secondary", reliability: float = .8):
    return GeoEvidence(
        evidence_id=eid,
        title=title,
        published_at=when.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        source=source,
        source_type=source_type,
        source_ref=f"https://example.test/{eid}",
        reliability=reliability,
        text=title,
        tags=("test",),
    )


class _EvidenceAdapter:
    def __init__(self, rows):
        self.rows = tuple(rows)

    def run(self, now):
        return self.rows


class _Market:
    def __init__(self):
        self.values = {
            "BZ=F": 80.0, "CL=F": 76.0, "GC=F": 2500.0, "HG=F": 4.2,
            "ZW=F": 550.0, "NG=F": 3.0, "SPY": 600.0, "^TNX": 4.25, "DX-Y.NYB": 99.0,
        }

    def latest_value(self, symbol):
        return self.values.get(symbol)


class GSEScenarioTest(unittest.TestCase):
    def test_middle_east_evidence_builds_scenario_and_transmission(self):
        now = datetime(2026, 8, 18, 12, 17, tzinfo=UTC)
        rows = [
            ev("1", "Iran and Israel exchange missile strikes as Gulf tensions escalate", now - timedelta(hours=5), "source-a"),
            ev("2", "Tanker traffic near Strait of Hormuz faces military disruption", now - timedelta(hours=10), "source-b"),
            ev("3", "Iran sanctions action affects energy shipping", now - timedelta(days=2), "OFAC", "primary", .98),
        ]
        scenarios = ScenarioEngine().build(rows, now)
        middle = next(s for s in scenarios if s.scenario_type == "middle_east_energy_escalation")
        self.assertGreater(middle.probability, .10)
        self.assertGreaterEqual(middle.evidence_7d, 2)
        score, contributors = TransmissionGraph().asset_score("BRENT", scenarios)
        self.assertGreater(score, 0)
        self.assertTrue(contributors)

    def test_risk_scenario_can_point_spx_down(self):
        now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        rows = [
            ev("1", "Taiwan Strait military blockade exercise by China", now - timedelta(hours=3), "a"),
            ev("2", "Taiwan semiconductor export controls amid China military escalation", now - timedelta(hours=8), "b"),
        ]
        scenarios = ScenarioEngine().build(rows, now)
        score, _ = TransmissionGraph().asset_score("SPX", scenarios)
        self.assertLess(score, 0)


class GSEFrozenForecastTest(unittest.TestCase):
    def test_freeze_is_idempotent_and_verification_preserves_frozen_snapshot(self):
        now = datetime(2026, 8, 18, 12, 17, tzinfo=UTC)
        rows = [
            ev("1", "Iran Israel missile strike raises Strait of Hormuz tanker risk", now - timedelta(hours=2), "a"),
            ev("2", "Iran Gulf military escalation threatens oil shipping", now - timedelta(hours=4), "b"),
        ]
        market = _Market()
        with tempfile.TemporaryDirectory() as tmp:
            engine = GeopoliticalScenarioEngine(Path(tmp), evidence_adapter=_EvidenceAdapter(rows), market=market)
            first = engine.run(now)
            self.assertGreater(first["active_scenarios"], 0)
            self.assertGreater(first["forecasts_frozen"], 0)
            second = engine.run(now + timedelta(minutes=20))
            self.assertEqual(second["forecasts_frozen"], 0)

            forecasts = engine.store.forecasts()
            target24 = [f for f in forecasts if int(f["horizon_hours"]) == 24]
            self.assertTrue(target24)
            before = {f["forecast_id"]: f["evidence_snapshot"] for f in target24}

            # Move all market series in each forecast's predicted direction.
            for f in target24:
                symbol = f["symbol"]
                base = float(f["baseline_value"])
                market.values[symbol] = base * (1.02 if int(f["direction"]) > 0 else .98)

            due = engine.verify_due(now + timedelta(hours=25))
            self.assertGreater(len(due), 0)
            self.assertTrue(all(v.outcome for v in due))
            after = {f["forecast_id"]: f["evidence_snapshot"] for f in engine.store.forecasts() if int(f["horizon_hours"]) == 24}
            self.assertEqual(before, after)

            report = engine.calibration()
            self.assertGreater(report["overall"]["count"], 0)
            self.assertFalse(report["automatic_tuning_enabled"])

    def test_no_freeze_outside_six_hour_windows(self):
        now = datetime(2026, 8, 18, 13, 17, tzinfo=UTC)
        rows = [
            ev("1", "Russia Ukraine Black Sea port strike disrupts grain export", now - timedelta(hours=2), "a"),
            ev("2", "Black Sea Ukraine missile attack threatens grain port", now - timedelta(hours=5), "b"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            engine = GeopoliticalScenarioEngine(Path(tmp), evidence_adapter=_EvidenceAdapter(rows), market=_Market())
            status = engine.run(now)
            self.assertEqual(status["forecasts_frozen"], 0)


if __name__ == "__main__":
    unittest.main()
