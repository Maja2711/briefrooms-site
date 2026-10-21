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
        "methodology_version":asd.VERSION,"historical_independent_n":18,
        **asd.selection_contract(state),
        "historical_median_abs_return": 0.005,
    }
    family["definition_hash"] = asd.digest(family)
    state["families"][fid] = family
    asd.register_trial(state,fid,clock_after(rows))
    return fid


class FSEAutonomousDiscoveryTests(unittest.TestCase):
    @staticmethod
    def episode(vector, index, window=32):
        return {"vector":vector,"return":.01,"end_at":str(index),"window":window,
                "start_index":index*80,"end_index":index*80+window-1,"target_end_index":index*80+window+3}

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
                               "end_at": str(i), "window": 32,"start_index":i*40,"target_end_index":i*40+35})
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
                                 source[-1].close * (1 + .001 * i)) for i in range(1, 6)]
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
                                abs_move=None, baseline_abs=None,start_index=0,spacing=5):
        for k in range(start_index,start_index+n):
            asd.append_evidence(state, "resolutions", {
                "snapshot_id": f"test:{family_id}:{k}",
                "instrument": "BTCUSD", "family_id": family_id,
                "methodology_version":asd.VERSION,
                "frozen_at":(datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=k*spacing)).isoformat(),
                "outcome_closed_at":(datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=k*spacing+4)).isoformat(),
                "outcome_observed_at": (datetime(2026, 10, 1, tzinfo=timezone.utc)
                                        + timedelta(hours=k*spacing+3)).isoformat(),
                "resolved_at": (datetime(2026, 10, 1, tzinfo=timezone.utc)
                                + timedelta(hours=k*spacing+4)).isoformat(),
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
            "methodology_version":asd.VERSION,
            "frozen_at":(datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=39*5)).isoformat(),
            "outcome_closed_at":(datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=39*5+4)).isoformat(),
            "outcome_observed_at":(datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=39*5+3)).isoformat(),
            "resolved_at":(datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=39*5+4)).isoformat(),
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
            base.update(asd.selection_contract(state))
            base["definition_hash"] = asd.digest({k:v for k,v in base.items() if k!="definition_hash"})
            state["families"][base["family_id"]] = base
            asd.register_trial(state,base["family_id"],clock_after(original))
        members=[{"vector":[200.0]*15,"return":.02,"end_at":str(i),"start_index":i*40,"target_end_index":i*40+35} for i in range(8)]
        group = {"centroid": [200.0] * 15, "radius": .15,
                 "members": members, "count": 8}
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
        def candidate_rows(_bars, window):
            return ([{"vector": f["centroid"]}]*8 if window == 32 else [])
        def group_rows(rows):
            return [old_group] if rows else []
        with patch.object(asd, "candidates", side_effect=candidate_rows),\
             patch.object(asd, "learn_groups", side_effect=group_rows):
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

    def test_query_purge_excludes_current_motif_and_training_target(self):
        source=bars(1000)
        for window in asd.WINDOWS:
            sample=asd.candidates(source,window)
            self.assertTrue(sample)
            self.assertTrue(all(r["target_end_index"]<len(source)-window for r in sample))
            self.assertGreater(len(asd.independent_episodes(sample)),asd.MIN_SUPPORT)

    def test_overlapping_windows_are_not_six_independent_occurrences(self):
        raw=[{"vector":[.1]*15,"return":.01,"end_at":str(i),"start_index":i*8,"target_end_index":i*8+67} for i in range(6)]
        self.assertEqual(len(asd.independent_episodes(raw)),1)
        self.assertEqual(asd.learn_groups(raw),[])
        separate=[self.episode([.1]*15,i,64) for i in range(6)]
        self.assertEqual(asd.learn_groups(separate)[0]["independent_count"],6)

    def test_sparse_bridge_does_not_erase_two_dense_families(self):
        values=[i*.001 for i in range(8)]+[.1,.2,.3]+[.35+i*.001 for i in range(8)]
        sample=[self.episode([value]*15,i) for i,value in enumerate(values)]
        groups=asd.learn_groups(sample)
        self.assertEqual(len(groups),2)
        self.assertTrue(all(g["independent_count"]>=8 for g in groups))
        reversed_groups=asd.learn_groups(list(reversed(sample)))
        self.assertEqual([(g["centroid"],g["count"]) for g in groups],[(g["centroid"],g["count"]) for g in reversed_groups])
        changed=[dict(r,**{"return":-999}) for r in sample]
        self.assertEqual([g["centroid"] for g in groups],[g["centroid"] for g in asd.learn_groups(changed)])

    def test_quality_gate_abstains_on_weak_or_ambiguous_match(self):
        source=bars(); t=clock_after(source)
        state=asd.blank_state(); fid=registered_family(state,source)
        family=state["families"][fid]
        family["centroid"]=[x+.16 for x in family["centroid"]]
        family["definition_hash"]=asd.digest({k:v for k,v in family.items() if k!="definition_hash"})
        self.assertTrue(asd.capture(state,"BTCUSD",source,t))
        self.assertEqual(state["snapshots"][-1]["status"],"FROZEN_LOW_QUALITY")
        self.assertIsNone(state["snapshots"][-1]["p_up"])
        state=asd.blank_state(); fid=registered_family(state,source)
        other=copy.deepcopy(state["families"][fid]); other["family_id"]="ASD-SECOND"
        other.update(asd.selection_contract(state)); other["centroid"]=[x+.001 for x in other["centroid"]]
        other["definition_hash"]=asd.digest({k:v for k,v in other.items() if k!="definition_hash"})
        state["families"][other["family_id"]]=other; asd.register_trial(state,other["family_id"],t)
        asd.capture(state,"BTCUSD",source,t)
        self.assertEqual(state["snapshots"][-1]["status"],"FROZEN_AMBIGUOUS")
        self.assertIsNone(state["snapshots"][-1]["family_id"])
        self.assertTrue(state["snapshots"][-1]["query_fingerprints"])
        asd.verify(state)

    def test_forty_overlapping_outcomes_cannot_pass_independent_holdout(self):
        state=asd.blank_state(); fid=registered_family(state,bars())
        self.add_prospective_results(state,fid,40,.1,.25,spacing=1)
        score=asd.family_evidence(state,fid)
        self.assertEqual(score["prospective_n"],40)
        self.assertEqual(score["independent_n"],10)
        self.assertFalse(score["formal_evaluated"])
        self.assertEqual(asd.finalize_selections(state,datetime(2027,1,1,tzinfo=timezone.utc)),0)
        self.assertEqual(asd.retire_failed_families(state,datetime(2027,1,1,tzinfo=timezone.utc)),0)

    def test_fixed_holdout_verdict_is_frozen_once_and_not_reselected(self):
        state=asd.blank_state(); fid=registered_family(state,bars())
        self.add_prospective_results(state,fid,40,.15,.25)
        t=datetime(2027,1,1,tzinfo=timezone.utc)
        self.assertEqual(asd.finalize_selections(state,t),1)
        frozen=copy.deepcopy(state["selection_events"])
        initial=asd.family_evidence(state,fid)
        self.add_prospective_results(state,fid,50,.9,.25,start_index=40)
        self.assertEqual(asd.finalize_selections(state,t),0)
        self.assertEqual(state["selection_events"],frozen)
        current=asd.family_evidence(state,fid)
        self.assertEqual(current["prospective_n"],90)
        self.assertEqual(current["holdout_n"],40)
        self.assertEqual(current["mean_brier"],initial["mean_brier"])
        self.assertTrue(current["directional_pass"])
        asd.verify(state)

    def test_trial_budget_is_durable_and_does_not_cap_exploratory_catalogue(self):
        state=asd.blank_state(); first=registered_family(state,bars()); t=clock_after(bars())
        for i in range(1,26):
            family=copy.deepcopy(state["families"][first]); family["family_id"]=f"ASD-BUDGET-{i}"
            family.update(asd.selection_contract(state)); family["centroid"]=[float(i)]*15
            family["definition_hash"]=asd.digest({k:v for k,v in family.items() if k!="definition_hash"})
            state["families"][family["family_id"]]=family; asd.register_trial(state,family["family_id"],t)
        self.assertEqual(len(state["families"]),26)
        restored=copy.deepcopy(state); contract=asd.selection_contract(restored)
        self.assertFalse(contract["selection_eligible"])
        self.assertIsNone(contract["selection_trial_index"])
        self.add_prospective_results(restored,"ASD-BUDGET-25",40,.01,.25)
        self.assertFalse(asd.family_evidence(restored,"ASD-BUDGET-25")["formal_evaluated"])
        self.assertEqual(asd.publication(restored,t)["summary"]["selection_trials_used"],24)
        asd.verify(restored)
        corrupted=copy.deepcopy(restored); corrupted["selection_events"]=[]
        with self.assertRaises(ValueError):asd.verify(corrupted)

    def test_migration_archives_legacy_without_rewriting_definitions_or_evidence(self):
        source=bars(1000); state=asd.blank_state(); fid=registered_family(state,source)
        family=state["families"][fid]
        for key in ["methodology_version","historical_independent_n","selection_trial_index","selection_target_n","selection_alpha","selection_eligible"]:family.pop(key)
        family["definition_hash"]=asd.digest({k:v for k,v in family.items() if k!="definition_hash"})
        state["schema_version"]=asd.LEGACY_VERSION; state.pop("selection_events")
        old_snapshot={"snapshot_id":"BTCUSD:legacy","instrument":"BTCUSD","family_id":fid,"observed_at":source[100].timestamp.isoformat(),"frozen_at":source[101].timestamp.isoformat(),"reference_close":source[100].close,"p_up":.63,"baseline_p_up":.5}
        asd.append_evidence(state,"snapshots",old_snapshot)
        asd.append_evidence(state,"resolutions",{"snapshot_id":"BTCUSD:legacy:resolved","instrument":"BTCUSD","family_id":fid,"brier":.2,"baseline_brier":.25})
        original_family=copy.deepcopy(family); original_snapshots=copy.deepcopy(state["snapshots"]); original_resolutions=copy.deepcopy(state["resolutions"])
        asd.cycle(state,{"BTCUSD":source},clock_after(source))
        self.assertEqual(state["schema_version"],asd.VERSION)
        self.assertEqual(state["families"][fid],original_family)
        self.assertEqual(state["snapshots"][:len(original_snapshots)],original_snapshots)
        self.assertEqual(state["resolutions"][:len(original_resolutions)],original_resolutions)
        self.assertFalse(any(r["snapshot_id"]=="BTCUSD:legacy" for r in state["resolutions"]))
        public=asd.publication(state,clock_after(source))
        self.assertEqual(public["legacy_evidence"]["families"],1)
        self.assertEqual(public["legacy_evidence"]["snapshots"],1)
        self.assertFalse(public["legacy_evidence"]["included_in_current_metrics"])
        archive=next(f for f in public["legacy_families"] if f["family_id"]==fid)
        self.assertEqual(archive["description"],original_family["description"])
        self.assertEqual(archive["applications"],original_family["applications"])
        self.assertEqual(archive["historical_sample_n"],18)
        self.assertEqual(archive["historical_p_up"],.63)
        self.assertEqual(archive["legacy_prospective_n"],1)
        self.assertEqual(archive["mean_brier"],.2)
        self.assertEqual(archive["evidence_status"],"ARCHIVED_LEGACY")
        self.assertNotIn(fid,[f["family_id"] for f in public["families"]])
        self.assertTrue(all(r["family_id"]!=fid for r in public["current_matches"]))
        asd.verify(state)

    def test_live_reference_freeze_excludes_already_open_target_hour(self):
        end=datetime(2026,10,21,19,tzinfo=timezone.utc)
        source=bars(180,start=end-timedelta(hours=179)); state=asd.blank_state()
        registered_family(state,source)
        frozen=end+timedelta(hours=1,minutes=57)
        live=Bar(end+timedelta(hours=1),120,source_observed_at=frozen-timedelta(minutes=1))
        self.assertTrue(asd.capture(state,"BTCUSD",source,frozen,reference_bar=live))
        snap=state["snapshots"][-1]
        self.assertEqual(snap["reference_price"],120)
        self.assertEqual(asd.utc(snap["reference_source_observed_at"]),frozen-timedelta(minutes=1))
        self.assertEqual(snap["target_selection"],asd.TARGET_SELECTION)
        future=source+[Bar(end+timedelta(hours=i),999 if i==1 else 121) for i in range(1,6)]
        self.assertEqual(asd.resolve(state,"BTCUSD",future,end+timedelta(hours=5,minutes=59)),0)
        self.assertEqual(asd.resolve(state,"BTCUSD",future,end+timedelta(hours=6)),1)
        result=state["resolutions"][-1]
        self.assertEqual(result["target_bar_opens"][0],(end+timedelta(hours=2)).isoformat())
        self.assertTrue(all(asd.utc(t)>=frozen for t in result["target_bar_opens"]))
        self.assertAlmostEqual(result["forward_return"],121/120-1,places=8)

    def test_reference_requires_fresh_source_clock_and_live_clock_is_post_fetch(self):
        from unittest.mock import patch
        end=datetime(2026,10,21,19,tzinfo=timezone.utc)
        source=bars(180,start=end-timedelta(hours=179)); frozen=end+timedelta(hours=1,minutes=57)
        live=Bar(end+timedelta(hours=1),120,source_observed_at=frozen-timedelta(minutes=1))
        state=asd.blank_state(); registered_family(state,source)
        self.assertFalse(asd.capture(state,"BTCUSD",source,frozen))
        future_quote=Bar(live.timestamp,120,source_observed_at=frozen+timedelta(seconds=1))
        self.assertFalse(asd.capture(state,"BTCUSD",source,frozen,reference_bar=future_quote))
        with patch.object(asd,"now_utc",return_value=frozen):
            asd.cycle(state,{"BTCUSD":source+[live]},end+timedelta(hours=1),live_clock=True)
        self.assertEqual(len(state["snapshots"]),1)
        self.assertEqual(asd.utc(state["snapshots"][0]["frozen_at"]),frozen)
        self.assertEqual(state["snapshots"][0]["reference_price"],120)
        fixed=asd.blank_state(); registered_family(fixed,source)
        asd.cycle(fixed,{"BTCUSD":source+[live]},end+timedelta(hours=1,minutes=30))
        self.assertEqual(fixed["snapshots"],[])


if __name__ == "__main__":
    unittest.main()
