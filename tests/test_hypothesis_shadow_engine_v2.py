import json
import tempfile
import unittest
from pathlib import Path

from scripts.hypothesis_shadow_engine_v2 import run_cycle, verify, ZERO_AUTHORITY


def write(root: Path, rel: str, value):
    p=root/rel
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value),encoding="utf-8")


class HSE2Tests(unittest.TestCase):
    def populate_sources(self, root: Path):
        write(root,"data/investments/eurusd_x_public_pl.json",{
            "generated_at":"2026-09-28T10:00:00Z","champion_setup_id":"X-BASE","active_challenger_id":"X-TREND","resolved_4h":5,
            "setups":[
                {"setup_id":"X-BASE","n":5,"mean_signed_return_bps":1.0},
                {"setup_id":"X-TREND","n":5,"mean_signed_return_bps":1.5},
            ],"calibration_policy":{"primary_horizon_hours":4}
        })
        write(root,"data/investments/daily_stock_challenger_intents.json",{
            "generated_at":"2026-09-28T10:00:00Z",
            "hypothesis_only":[{
                "hypothesis_id":"stock-h1","market":"gpw","gate":"shortlist_rank",
                "evidence":{"observations":70,"mean_candidate_minus_selected_percent":0.4,"pattern_id":"p1"}
            }]
        })
        write(root,"data/public/brace_spx_platform_public.json",{
            "generated_at":"2026-09-28T10:00:00Z",
            "frozen_track":{"generation_id":"G6"},
            "adaptive_research":{"challengers":[
                {"candidate_id":"G6-R-C01","status":"ACTIVE_RESEARCH","prospective_n":10,"cumulative_return":0.02,"rule":"r1","origin":"test"}
            ]}
        })
        write(root,"data/investments/wes_incremental_alpha_report.json",{
            "historical_backfill_allowed":False,
            "overall":{"resolved_pairs":5,"mean_incremental_alpha_percent":0.10}
        })
        write(root,"data/gse/gse_v2_learning_review_status.json",{
            "published_at":"2026-09-28T10:00:00Z","status":"shadow_learning",
            "prospective":{"paired_n":100,"delta_brier_v2_minus_v1":-0.01}
        })
        write(root,"data/investments/research_lab_report.json",{
            "generated_at":"2026-09-28T10:00:00Z",
            "top_candidates":[{
                "candidate_id":"strat-1","status":"prospective_shadow",
                "shadow_metrics":{"count":4,"mean_net_percent":0.1},
                "spec":{"instrument_id":"eurusd","side":"long","timeframe":"H4","rule":"full_stack"}
            }]
        })
        write(root,"data/investments/fse_public.json",{
            "generated_at":"2026-09-28T10:00:00Z","module_id":"IN-09","mode":"SHADOW_ONLY","production_impact":False,
            "hse_measurements":[
                {"proposal_key":"fse-fractal-memory-eurusd-4h-brier","claim":"FSE Fractal Memory improves Brier.",
                 "champion":"50/50 directional baseline","challenger":"FSE Fractal Memory",
                 "metric_name":"brier_improvement_vs_0_5","target_n":40,
                 "success_mean_edge":0.0025,"reject_mean_edge":-0.0025,"counter":0,"total":0.0,
                 "details":{"instrument":"EURUSD","kind":"directional_memory"}},
                {"proposal_key":"fse-structural-risk-eurusd-4h-brier","claim":"FSE structural risk improves Brier.",
                 "champion":"50/50 large-move baseline","challenger":"FSE Structural Risk",
                 "metric_name":"risk_brier_improvement_vs_0_5","target_n":40,
                 "success_mean_edge":0.0025,"reject_mean_edge":-0.0025,"counter":0,"total":0.0,
                 "details":{"instrument":"EURUSD","kind":"risk_calibration"}}
            ]
        })

    def test_all_seven_sources_register_without_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"state"; public=root/"public.json"
            self.populate_sources(root)
            out=run_cycle(root,state,public,"2026-09-28T10:10:00Z")
            self.assertEqual(out["summary"]["sources_available"],7)
            self.assertGreaterEqual(out["summary"]["experiments_total"],8)
            self.assertEqual(out["summary"]["prospective_evidence_n"],0)
            self.assertEqual(out["authority"],ZERO_AUTHORITY)
            self.assertTrue(verify(state)["zero_authority"])

    def test_wes_fixed_n_uses_only_post_freeze_evidence_and_creates_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"state"; public=root/"public.json"
            self.populate_sources(root)
            first=run_cycle(root,state,public,"2026-09-28T10:10:00Z")
            wes=[x for x in first["experiments"] if x["source_engine"]=="WES"][0]
            self.assertEqual(wes["prospective_n"],0)

            write(root,"data/investments/wes_incremental_alpha_report.json",{
                "historical_backfill_allowed":False,
                "overall":{"resolved_pairs":17,"mean_incremental_alpha_percent":0.20}
            })
            second=run_cycle(root,state,public,"2026-10-05T10:10:00Z")
            wes=[x for x in second["experiments"] if x["source_engine"]=="WES"][0]
            self.assertEqual(wes["status"],"SUPPORTED")
            self.assertEqual(wes["prospective_n"],12)
            self.assertEqual(second["summary"]["lessons_total"],1)
            results=second["recent_results"]
            result=next(x for x in results if x["experiment_id"]==wes["experiment_id"])
            self.assertEqual(result["formal_evaluation_number"],1)
            self.assertEqual(result["sample_n"],12)
            self.assertFalse(result["authority"]["automatic_promotion"])

            write(root,"data/investments/wes_incremental_alpha_report.json",{
                "historical_backfill_allowed":False,
                "overall":{"resolved_pairs":29,"mean_incremental_alpha_percent":-1.0}
            })
            third=run_cycle(root,state,public,"2026-10-12T10:10:00Z")
            wes3=[x for x in third["experiments"] if x["source_engine"]=="WES"][0]
            self.assertEqual(wes3["status"],"SUPPORTED")
            self.assertEqual(len([x for x in third["recent_results"] if x["experiment_id"]==wes["experiment_id"]]),1)

    def test_missing_source_does_not_create_fake_hypothesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"state"; public=root/"public.json"
            write(root,"data/investments/wes_incremental_alpha_report.json",{
                "historical_backfill_allowed":False,
                "overall":{"resolved_pairs":0,"mean_incremental_alpha_percent":None}
            })
            out=run_cycle(root,state,public,"2026-09-28T10:10:00Z")
            self.assertEqual(out["summary"]["sources_available"],1)
            self.assertEqual(out["summary"]["experiments_total"],1)
            self.assertEqual(out["summary"]["prospective_evidence_n"],0)


if __name__=="__main__":
    unittest.main()
