from __future__ import annotations
import importlib, json, math, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gse_v2_learning_loop as loop
UTC=timezone.utc

def series(start, values):
    at=datetime.fromisoformat(start.replace("Z","+00:00"))
    return [loop.DailyClose(at+timedelta(days=i), float(v)) for i,v in enumerate(values)]

class FakeMarket:
    def __init__(self, histories): self.histories=histories
    def daily_closes(self, symbol, start, end):
        mapping={"SPY":"SPX","DX-Y.NYB":"USD","^TNX":"US10Y","BZ=F":"BRENT","GC=F":"GOLD"}
        asset=mapping.get(symbol)
        return [p for p in self.histories.get(asset,[]) if start <= p.timestamp <= end]

def response(event_id, cluster, at, success, regime, scenario="middle_east_energy_escalation", asset="SPX", horizon=24):
    dt=datetime.fromisoformat(at.replace("Z","+00:00"))
    return {
      "event_id":event_id,"event_cluster_id":cluster,"market_anchor_key":f"{at}|{asset}|{horizon}",
      "event_at":at,"response_complete_at":(dt+timedelta(days=2)).isoformat().replace("+00:00","Z"),
      "scenario_type":scenario,"asset":asset,"horizon_hours":horizon,
      "transmission_weight":-0.45,"directional_success":success,
      "aligned_return":0.02 if success else -0.02,"raw_return":-0.02 if success else 0.02,
      "source_reliability":.9,"event_features":{"severity":.8,"surprise":.7,"global_scope":.8,"military_relevance":.9,
        "energy_relevance":1.0,"shipping_relevance":.3,"sanctions_relevance":0,"food_relevance":0,"china_relevance":0},
      "regime_features":regime
    }

class Tests(unittest.TestCase):
    def test_regime_uses_strictly_prior_day(self):
        histories={a:series("2024-01-01T00:00:00Z",[100+i for i in range(80)]) for a in loop.CORE_REGIME_ASSETS}
        f=loop.regime_features(histories, datetime(2024,3,10,12,tzinfo=UTC))
        self.assertIn("SPX.ret_20",f)
        self.assertTrue(math.isfinite(f["SPX.ret_20"]))

    def test_enriched_library_adds_cluster_and_features(self):
        catalog={"schema_version":"x","catalog_version":"v1","events":[{
          "event_id":"e1","event_at":"2024-03-01T00:00:00Z","event_cluster_id":"c1",
          "source_reliability":.95,"features":{"severity":.9},"scenario_types":["middle_east_energy_escalation"]}]}
        legacy={"responses":[{"event_id":"e1","event_at":"2024-03-01T00:00:00Z","response_complete_at":"2024-03-03T00:00:00Z",
          "scenario_type":"middle_east_energy_escalation","asset":"SPX","horizon_hours":24,"market_anchor_key":"x",
          "transmission_weight":-.45,"directional_success":True,"aligned_return":.01}]}
        histories={a:series("2023-10-01T00:00:00Z",[100+i*.1 for i in range(190)]) for a in loop.CORE_REGIME_ASSETS}
        enriched=loop.build_enriched_library(legacy,catalog,FakeMarket(histories),built_at=datetime(2026,1,1,tzinfo=UTC))
        row=enriched["responses"][0]
        self.assertEqual(row["event_cluster_id"],"c1")
        self.assertEqual(row["source_reliability"],.95)
        self.assertIn("SPX.ret_20",row["regime_features"])

    def test_similarity_prefers_matching_regime(self):
        now=datetime(2025,1,1,tzinfo=UTC)
        rows=[
          response("a","a","2020-01-01T00:00:00Z",True,{"SPX.ret_20":-.10,"USD.ret_20":.05}),
          response("b","b","2021-01-01T00:00:00Z",False,{"SPX.ret_20":.10,"USD.ret_20":-.05}),
          response("c","c","2022-01-01T00:00:00Z",True,{"SPX.ret_20":-.09,"USD.ret_20":.04}),
        ]
        post=loop.posterior_for_scenario(rows,scenario_type="middle_east_energy_escalation",asset="SPX",horizon_hours=24,
          forecast_at=now,current_regime={"SPX.ret_20":-.095,"USD.ret_20":.045},
          current_event=rows[0]["event_features"],temperature=.5,prior_strength=2)
        self.assertGreater(post["probability_transmission_direction"],.5)
        self.assertEqual(post["neighbours"][0]["event_id"],"c")

    def test_walkforward_is_point_in_time(self):
        rows=[
          response("a","a","2020-01-01T00:00:00Z",True,{"SPX.ret_20":-.1}),
          response("b","b","2021-01-01T00:00:00Z",True,{"SPX.ret_20":-.1}),
          response("c","c","2022-01-01T00:00:00Z",False,{"SPX.ret_20":.1}),
          response("d","d","2023-01-01T00:00:00Z",False,{"SPX.ret_20":.1}),
        ]
        preds=loop.historical_walkforward_predictions({"responses":rows},temperature=.85,prior_strength=4)
        ids={x["event_id"] for x in preds}
        self.assertNotIn("a",ids)
        self.assertNotIn("b",ids)
        self.assertIn("c",ids)
        self.assertIn("d",ids)

    def test_candidate_is_bounded_and_has_zero_authority(self):
        rows=[
          response("a","a","2020-01-01T00:00:00Z",True,{"SPX.ret_20":-.1}),
          response("b","b","2021-01-01T00:00:00Z",True,{"SPX.ret_20":-.08}),
          response("c","c","2022-01-01T00:00:00Z",True,{"SPX.ret_20":-.09}),
        ]
        baseline={"forecast_id":"f1","batch_id":"b","asset":"SPX","symbol":"SPY","forecast_at":"2025-01-10T12:00:00Z",
          "target_at":"2025-01-11T12:00:00Z","horizon_hours":24,"direction":-1,"predicted_probability":.7,
          "scenario_snapshot":[{"scenario_type":"middle_east_energy_escalation","probability":.8,"confidence":.8,"acceleration":1.0}]}
        histories={a:series("2024-09-01T00:00:00Z",[100-i*.05 for i in range(140)]) for a in loop.CORE_REGIME_ASSETS}
        original=json.loads(json.dumps(baseline))
        candidate=loop.candidate_from_baseline(baseline,{"responses":rows},histories)
        self.assertIsNotNone(candidate)
        self.assertGreaterEqual(candidate["v2_regime_candidate_probability"],.5)
        self.assertLessEqual(candidate["v2_regime_candidate_probability"],.85)
        self.assertFalse(candidate["decision_influence"])
        self.assertFalse(candidate["automatic_tuning_applied"])
        self.assertEqual(baseline,original)

    def test_policy_learning_never_auto_applies(self):
        rows=[]
        for i in range(15):
            year=2008+i
            rows.append(response(f"e{i}",f"c{i}",f"{year}-01-01T00:00:00Z",i%2==0,{"SPX.ret_20":(-.1 if i%2==0 else .1)}))
        proposal=loop.propose_policy({"responses":rows})
        self.assertFalse(proposal["automatically_applied"])
        self.assertTrue(proposal["active_policy_unchanged"])

    def test_hash_chained_learning_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            kwargs=dict(now=datetime(2026,1,1,tzinfo=UTC),enriched_library={"x":1},
              historical_report={"x":2},policy_proposal={"x":3},calibration={"x":4},candidates_added=1,verifications_added=2)
            loop.append_learning_ledger(root,**kwargs)
            kwargs["now"]=datetime(2026,1,1,1,tzinfo=UTC)
            loop.append_learning_ledger(root,**kwargs)
            rows=loop.read_jsonl(root/"gse_v2_learning_ledger.jsonl")
            loop._validate_ledger(rows)
            self.assertEqual(rows[1]["previous_hash"],rows[0]["record_hash"])

    def test_readiness_cannot_auto_promote(self):
        hist={"overall":{"regime_aware":{"n":100},"delta_brier_regime_minus_unweighted":-.01}}
        cal={"overall":{"paired_n":100,"delta_brier_v2_minus_v1":-.01,"delta_log_loss_v2_minus_v1":-.01,"calibration_bias_v2_regime":.01}}
        gate=loop.readiness(hist,cal)
        self.assertEqual(gate["status"],"eligible_for_human_promotion_review")
        self.assertFalse(gate["automatic_promotion"])

    def test_run_writes_learning_state_without_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            catalog_path=root/"catalog.json"
            catalog={"schema_version":"x","catalog_version":"v1","events":[]}
            legacy_rows=[]
            for i in range(8):
                at=datetime(2015+i,1,10,tzinfo=UTC)
                eid=f"e{i}"
                catalog["events"].append({
                    "event_id":eid,"event_cluster_id":f"c{i}","event_at":at.isoformat().replace("+00:00","Z"),
                    "scenario_types":["middle_east_energy_escalation"],"source_reliability":.9,
                    "features":{"severity":.7,"surprise":.6}
                })
                legacy_rows.append({
                    "event_id":eid,"event_at":at.isoformat().replace("+00:00","Z"),
                    "response_complete_at":(at+timedelta(days=2)).isoformat().replace("+00:00","Z"),
                    "scenario_type":"middle_east_energy_escalation","asset":"SPX","horizon_hours":24,
                    "market_anchor_key":f"{at.date()}|SPX|24","transmission_weight":-.45,
                    "directional_success":i%2==0,"aligned_return":.01 if i%2==0 else -.01
                })
            catalog_path.write_text(json.dumps(catalog))
            (root/"gse_historical_analogue_library.json").write_text(json.dumps({"responses":legacy_rows}))
            forecast={"forecast_id":"f-live","batch_id":"b","asset":"SPX","symbol":"SPY",
                "forecast_at":"2025-01-10T12:00:00Z","target_at":"2025-01-11T12:00:00Z",
                "horizon_hours":24,"direction":-1,"predicted_probability":.70,
                "scenario_snapshot":[{"scenario_type":"middle_east_energy_escalation","probability":.8,"confidence":.8,"acceleration":1.0}]}
            (root/"gse_forecasts.jsonl").write_text(json.dumps(forecast)+"\n")
            verification={"forecast_id":"f-live","outcome":True,"verified_at":"2025-01-11T13:00:00Z"}
            (root/"gse_verifications.jsonl").write_text(json.dumps(verification)+"\n")
            start=datetime(2014,8,1,tzinfo=UTC)
            values=[100.0 + 0.01*i for i in range(3900)]
            histories={a:[loop.DailyClose(start+timedelta(days=i),v) for i,v in enumerate(values)]
                       for a in loop.CORE_REGIME_ASSETS}
            state=loop.run(root,catalog_path,now=datetime(2025,1,12,tzinfo=UTC),
                refresh_days=7,market=FakeMarket(histories))
            self.assertEqual(state["mode"],"shadow")
            self.assertFalse(state["controls"]["automatic_tuning_enabled"])
            self.assertTrue((root/"gse_v2_learning_ledger.jsonl").exists())
            self.assertTrue((root/"gse_v2_regime_forecasts.jsonl").exists())
            self.assertTrue((root/"gse_v2_regime_calibration.json").exists())

if __name__=="__main__": unittest.main()
