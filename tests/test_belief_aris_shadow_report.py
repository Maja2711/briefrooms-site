import unittest

from scripts.belief_aris_shadow_report import aggregate


def report(selected="full_representatives", retention=1.0, selected_complexity=4, full_complexity=4, gap=0.0):
    return {
        "contract_version": "belief-aris-shadow-v1",
        "mode": "research_shadow",
        "authority": {"decision_influence": False, "belief_core_writeback_enabled": False},
        "beliefs": {
            "spx.trend.bullish": {
                "selected_representation": selected,
                "representation_disagreement": 0.02,
                "residual_probability_gap": gap,
                "representations": [
                    {
                        "name": "full_representatives",
                        "information_retention": 1.0,
                        "complexity_units": full_complexity,
                        "retained_effective_mass": 1.0,
                        "residual_effective_mass": 0.0,
                        "pruned": False,
                    },
                    {
                        "name": "fresh_signal",
                        "information_retention": retention,
                        "complexity_units": selected_complexity,
                        "retained_effective_mass": retention,
                        "residual_effective_mass": max(0.0, 1.0-retention),
                        "pruned": selected != "fresh_signal",
                    },
                ],
            }
        },
    }


class ARISShadowReportTests(unittest.TestCase):
    def test_small_sample_collects_more(self):
        out = aggregate([report() for _ in range(3)])
        self.assertEqual(out["recommendation"], "COLLECT_MORE_SHADOW_DATA")
        self.assertFalse(out["authority"]["automatic_promotion_enabled"])

    def test_candidate_review_requires_large_evidence_base(self):
        rows = []
        for i in range(120):
            rows.append(report(selected="fresh_signal" if i < 30 else "full_representatives", retention=0.95, selected_complexity=2, full_complexity=4, gap=0.01))
        out = aggregate(rows)
        self.assertEqual(out["recommendation"], "REVIEW_CANDIDATES_FOR_CALIBRATION_TESTING")
        self.assertGreaterEqual(out["non_full_win_share"], 0.20)

    def test_full_representation_dominance_stays_shadow(self):
        out = aggregate([report() for _ in range(120)])
        self.assertEqual(out["recommendation"], "KEEP_SHADOW")
        self.assertIn("simpler_representations_rarely_win", out["promotion_blockers"])


if __name__ == "__main__":
    unittest.main()
