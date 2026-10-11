from __future__ import annotations

import copy
import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fse_autonomous_discovery as asd
from fractal_structure_engine import Bar


def bars(count: int = 180, start: datetime | None = None) -> list[Bar]:
    start = start or datetime(2026, 9, 1, tzinfo=timezone.utc)
    return [Bar(start + timedelta(hours=i),
                100 * math.exp(0.0004 * i + 0.012 * math.sin(i * 0.38)
                               + 0.003 * math.sin(i * 0.07))) for i in range(count)]


def clock_after(rows: list[Bar]) -> datetime:
    return rows[-1].timestamp + timedelta(hours=1, minutes=1)


def registered_family(state: dict, rows: list[Bar], instrument: str = "BTCUSD") -> str:
    centroid = list(asd.fingerprint([x.close for x in rows[-32:]]))
    fid = "ASD-TEST0001"
    family = {
        "family_id": fid, "instrument": instrument, "window": 32,
        "horizon_bars": 4, "discovered_at": clock_after(rows).isoformat(),
        "centroid": centroid, "radius": 0.18,
        "methodology": "unsupervised_adaptive_density_medoid_no_named_pattern_templates",
        "name": "Autonomicznie zidentyfikowany testowy motyw",
        "description": "Historyczna hipoteza testowa", "applications": ["research only"],
        "historical_sample_n": 18, "historical_p_up": 0.63,
        "historical_median_abs_return": 0.005,
    }
    family["definition_hash"] = asd.digest(family)
    state["families"][fid] = family
    return fid


class FSEAutonomousDiscoveryTests(unittest.TestCase):
    def test_price_level_invariant_geometry(self):
        a = [x.close for x in bars(130)]
        one = asd.fingerprint(a)
        two = asd.fingerprint([x * 120 for x in a])
        self.assertEqual(one, two)
        self.assertEqual(len(one), 15)

    def test_learner_forms_families_without_fixed_market_labels(self):
        sample = []
        for center in (0.0, 1.9):
            for i in range(9):
                vec = tuple(center + 0.003 * ((k + i) % 4) for k in range(15))
                sample.append({"vector": vec, "return": 0.02 if i % 2 else -0.01,
                               "end_at": str(i), "window": 32})
        groups = asd.learn_groups(sample)
        self.assertGreaterEqual(len(groups), 2)
        self.assertTrue(all(x["count"] >= asd.MIN_SUPPORT for x in groups))
        self.assertFalse(any("reversal" in x for x in groups))

    def test_auto_name_is_derived_from_discovered_shape(self):
        members = [{"return": 0.01}] * 8 + [{"return": -0.01}] * 4
        up = asd.describe([0, .1, .5, 1, .4, .2, 1.2, .8, 1.5, 2, 2.5, 3, 0, 0, 0], members)
        down = asd.describe([0, -.1, -.5, -1, -.4, -.2, -1.2, -.8, -1.5, -2, -2.5, -3, 0, 0, 0], members)
        self.assertNotEqual(up["name"], down["name"])
        self.assertEqual(up["applications"], [])
        self.assertIn("bez dowodu prospektywnego", up["description"])

    def test_freeze_separates_discovery_from_later_outcome(self):
        source = bars()
        state = asd.blank_state()
        fid = registered_family(state, source)
        frozen_at = clock_after(source)
        self.assertTrue(asd.capture(state, "BTCUSD", source, frozen_at))
        row = state["snapshots"][-1]
        self.assertEqual(row["family_id"], fid)
        self.assertEqual(row["p_up"], 0.63)
        self.assertEqual(len(state["resolutions"]), 0)
        self.assertTrue(row["prospective"])
        self.assertFalse(row["execution_authority"])
        self.assertFalse(asd.capture(state, "BTCUSD", source, frozen_at))
        extended = source + [Bar(source[-1].timestamp + timedelta(hours=i),
                                 source[-1].close * (1 + .001 * i)) for i in range(1, 5)]
        before_end = extended[-1].timestamp + timedelta(minutes=30)
        self.assertEqual(asd.resolve(state, "BTCUSD", extended, before_end), 0)
        self.assertEqual(asd.resolve(state, "BTCUSD", extended, clock_after(extended)), 1)
        self.assertEqual(asd.resolve(state, "BTCUSD", extended, clock_after(extended)), 0)
        asd.verify(state)
        score = asd.family_evidence(state, fid)
        self.assertEqual(score["prospective_n"], 1)
        self.assertEqual(score["evidence_status"], "PROSPECTIVE_COLLECTION")
        self.assertEqual(score["mean_brier"], round((.63 - 1) ** 2, 6))

    def test_hash_chains_detect_frozen_evidence_mutation(self):
        source = bars()
        state = asd.blank_state()
        registered_family(state, source)
        asd.capture(state, "BTCUSD", source, clock_after(source))
        tampered = copy.deepcopy(state)
        tampered["snapshots"][0]["p_up"] = 0.99
        with self.assertRaises(ValueError):
            asd.verify(tampered)
        tampered = copy.deepcopy(state)
        tampered["families"]["ASD-TEST0001"]["radius"] = 3
        with self.assertRaises(ValueError):
            asd.verify(tampered)

    def test_english_publication_is_read_only(self):
        source = bars()
        state = asd.blank_state()
        fid = registered_family(state, source)
        before = copy.deepcopy(state)
        public = asd.publication(state, clock_after(source))
        family = next(row for row in public["families"] if row["family_id"] == fid)
        self.assertIn("name_en", family)
        self.assertIn("description_en", family)
        self.assertEqual(family["applications_en"], [])
        self.assertIn("historical analogues", family["description_en"])
        self.assertEqual(family["application_status"], "UNDETERMINED")
        self.assertEqual(family["applications"], [])
        self.assertEqual(len(family["shape_points"]), 12)
        self.assertEqual(state, before)
        asd.verify(state)
        self.assertNotIn("name_en", state["families"][fid])

    @staticmethod
    def add_prospective_results(state, family_id, n, brier, baseline,
                                abs_move=None, baseline_abs=None):
        for k in range(n):
            asd.append_evidence(state, "resolutions", {
                "snapshot_id": f"test:{family_id}:{k}",
                "instrument": "BTCUSD", "family_id": family_id,
                "outcome_observed_at": (datetime(2026, 10, 1, tzinfo=timezone.utc)
                                        + timedelta(hours=k)).isoformat(),
                "resolved_at": (datetime(2026, 10, 1, tzinfo=timezone.utc)
                                + timedelta(hours=k+4)).isoformat(),
                "outcome_up": 1, "forward_return": 0.005,
                "brier": brier, "baseline_brier": baseline,
                "abs_move_mae": abs_move, "baseline_abs_move_mae": baseline_abs,
                "prospective": True, "execution_authority": False
            })

    def test_reject_at_40_without_positive_evidence_and_hide_from_frontend(self):
        original = bars()
        state = asd.blank_state()
        fid = registered_family(state, original)
        self.add_prospective_results(state, fid, 39, .27, .25)
        t = clock_after(original)
        self.assertEqual(asd.retire_failed_families(state, t), 0)
        self.assertEqual(asd.publication(state, t)["summary"]["families"], 1)
        asd.append_evidence(state, "resolutions", {
            "snapshot_id": "test:40", "instrument": "BTCUSD", "family_id": fid,
            "outcome_observed_at": t.isoformat(), "resolved_at": t.isoformat(),
            "brier": .27, "baseline_brier": .25,
            "prospective": True, "execution_authority": False
        })
        self.assertEqual(asd.family_evidence(state, fid)["evidence_status"], "NO_PROVEN_EDGE")
        self.assertEqual(asd.retire_failed_families(state, t), 1)
        self.assertEqual(asd.retire_failed_families(state, t), 0)
        self.assertIn(fid, state["families"])
        self.assertEqual(len(state["lifecycle_events"]), 1)
        self.assertEqual(state["lifecycle_events"][0]["evaluated_n"], 40)
        visible = asd.publication(state, t)
        self.assertEqual(visible["families"], [])
        self.assertEqual(visible["summary"]["retired_families"], 1)
        self.assertFalse(asd.capture(state, "BTCUSD", [], t))
        asd.verify(state)
        tampered = copy.deepcopy(state)
        tampered["lifecycle_events"][0]["reason"] = "REVIEW_CANDIDATE"
        with self.assertRaises(ValueError):
            asd.verify(tampered)

    def test_positive_directional_gain_at_40_is_review_not_auto_promotion(self):
        state = asd.blank_state()
        fid = registered_family(state, bars())
        self.add_prospective_results(state, fid, 40, .15, .25)
        status = asd.family_evidence(state, fid)
        self.assertTrue(status["directional_pass"])
        self.assertEqual(status["evidence_status"], "REVIEW_CANDIDATE")
        t = clock_after(bars())
        self.assertEqual(asd.retire_failed_families(state, t), 0)
        row = asd.publication(state, t)["families"][0]
        self.assertEqual(row["application_status"], "RESEARCH_CANDIDATE")
        self.assertEqual(len(row["applications"]), 1)
        self.assertEqual(len(row["applications_en"]), 1)
        self.assertFalse(asd.publication(state, t)["automatic_promotion"])

    def test_positive_absolute_movement_gain_can_rescue_weak_direction(self):
        state = asd.blank_state()
        fid = registered_family(state, bars())
        self.add_prospective_results(state, fid, 40, .27, .25, .002, .009)
        status = asd.family_evidence(state, fid)
        self.assertFalse(status["directional_pass"])
        self.assertTrue(status["absolute_move_pass"])
        self.assertEqual(status["evidence_status"], "REVIEW_CANDIDATE")
        row = asd.publication(state, clock_after(bars()))["families"][0]
        self.assertEqual(len(row["applications"]), 1)
        self.assertIn("rozmiaru ruchu", row["applications"][0])

    def test_growing_catalogue_has_no_14_family_lifetime_cap(self):
        from unittest.mock import patch
        state = asd.blank_state()
        original = bars()
        registered_family(state, original)
        for i in range(20):
            base = copy.deepcopy(state["families"]["ASD-TEST0001"])
            base["family_id"] = f"ASD-ALREADY{i:04d}"
            base["centroid"] = [float(i+2)] * 15
            base["definition_hash"] = asd.digest({k:v for k,v in base.items() if k!="definition_hash"})
            state["families"][base["family_id"]] = base
        group = {"centroid": [200.0] * 15, "radius": .15,
                 "members": [{"return": 0.02}] * 8, "count": 8}
        with patch.object(asd, "candidates", return_value=[{"vector": [200.0] * 15}]*8),\
             patch.object(asd, "learn_groups", return_value=[group]):
            created = asd.discover(state, "BTCUSD", original, clock_after(original))
        self.assertGreater(created, 0)
        self.assertGreater(len(state["families"]), 21)
        asd.verify(state)

    def test_retired_family_cannot_reenter_with_identical_historical_shape(self):
        from unittest.mock import patch
        state = asd.blank_state()
        source = bars()
        fid = registered_family(state, source)
        self.add_prospective_results(state, fid, 40, .28, .25)
        asd.retire_failed_families(state, clock_after(source))
        f = state["families"][fid]
        old_group = {"centroid": f["centroid"], "radius": f["radius"],
                     "members": [{"return": 0.0}] * 8, "count": 8}
        with patch.object(asd, "candidates", return_value=[{"vector": f["centroid"]}]*8),\
             patch.object(asd, "learn_groups", return_value=[old_group]):
            created = asd.discover(state, "BTCUSD", source, clock_after(source))
        self.assertEqual(created, 0)
        self.assertEqual(len(state["families"]), 1)
        asd.verify(state)

    def test_no_unfinished_bar_can_enter_snapshot(self):
        source = bars()
        state = asd.blank_state()
        registered_family(state, source)
        self.assertFalse(asd.capture(state, "BTCUSD", source,
                                     source[-1].timestamp + timedelta(minutes=40)))
        self.assertEqual(state["snapshots"], [])

    def test_promotion_and_production_authority_remain_disabled(self):
        state = asd.blank_state()
        report = asd.publication(state, datetime(2026, 10, 11, tzinfo=timezone.utc))
        self.assertEqual(report["summary"]["prospective_resolutions"], 0)
        self.assertFalse(report["automatic_promotion"])
        self.assertFalse(report["production_impact"])
        self.assertTrue(all(v is False for v in report["authority"].values()))

    def test_prospective_evidence_and_historical_support_not_confused(self):
        state = asd.blank_state()
        registered_family(state, bars())
        row = asd.publication(state, datetime(2026, 10, 11, tzinfo=timezone.utc))["families"][0]
        self.assertEqual(row["historical_sample_n"], 18)
        self.assertEqual(row["prospective_n"], 0)
        self.assertIsNone(row["incremental_brier"])
        self.assertEqual(row["evidence_status"], "HISTORICAL_DISCOVERY_ONLY")

    def test_no_hindsight_when_input_contains_future_unclosed_bars(self):
        source = bars()
        clock = clock_after(source)
        extra = source + [Bar(source[-1].timestamp + timedelta(hours=i),
                              source[-1].close * (1 + i / 100)) for i in range(1, 4)]
        state = asd.blank_state()
        registered_family(state, source)
        asd.cycle(state, {"BTCUSD": extra}, clock)
        self.assertEqual(len(state["resolutions"]), 0)
        self.assertEqual(state["snapshots"][-1]["observed_at"], source[-1].timestamp.isoformat())


if __name__ == "__main__":
    unittest.main()
