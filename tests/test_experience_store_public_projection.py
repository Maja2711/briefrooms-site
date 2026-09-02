import json
import tempfile
import unittest
from pathlib import Path

from scripts.experience_store_public_projection import build_projection


class ExperienceStorePublicProjectionTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        store = root / "experience_store.jsonl"
        status = root / "experience_store_status.json"
        report = root / "shadow_alpha_report.json"
        self._write_jsonl(store, [
            {
                "engine": "eurusd-abc-a", "engine_version": "v1", "instrument": "EUR/USD",
                "action": "LONG", "confidence": 0.72, "decision_at": "2026-09-02T10:00:00Z",
                "status": "SETTLED", "decision": {"signal_snapshot": {"secret": 1}},
                "outcome": {"settled_at": "2026-09-02T11:00:00Z", "exit_reason": "TAKE_PROFIT",
                            "gross_return_fraction": 0.0018, "net_return_fraction": None,
                            "mae_fraction": -0.0004, "mfe_fraction": 0.0021, "r_multiple": 1.8,
                            "raw": {"private": True}},
            },
            {
                "engine": "eurusd-abc-a", "engine_version": "v1", "instrument": "EUR/USD",
                "action": "FLAT", "confidence": 0.0, "decision_at": "2026-09-02T12:00:00Z",
                "status": "PENDING", "outcome": None,
            },
            {
                "engine": "gpw", "engine_version": "v2", "instrument": "PKO.WA",
                "action": "AWARIA_DANYCH", "confidence": 75.0, "decision_at": "2026-09-02T13:00:00Z",
                "status": "PENDING", "outcome": None,
            },
        ])
        self._write_json(status, {
            "schema_version": "briefrooms-experience-store-v1", "generated_at": "2026-09-02T13:05:00Z",
            "experience_count": 3, "settled_count": 1, "pending_count": 2,
            "source_ledgers": [{"path": "/private/a", "head_hash": "aaa"}, {"path": "/private/b", "head_hash": "bbb"}],
            "zero_authority": True,
        })
        self._write_json(report, {
            "schema_version": "briefrooms-shadow-alpha-report-v1", "generated_at": "2026-09-02T13:05:00Z",
            "overall": {
                "assessment": "INSUFFICIENT_DATA", "assessment_basis": "raw_edge_only_no_complete_benchmark",
                "minimum_sample_gate": 30,
                "raw_edge": {"sample_size": 1, "mean_net_return_fraction": 0.0018, "win_rate": 1.0,
                             "max_drawdown_fraction": 0.0, "avg_mae_fraction": -0.0004, "avg_mfe_fraction": 0.0021,
                             "profit_factor": "inf"},
                "formal_alpha": {"available": False, "status": "NOT_MEASURABLE", "mean_excess_return_fraction": None},
            },
            "by_engine": {
                "eurusd-abc-a": {
                    "assessment": "INSUFFICIENT_DATA", "assessment_basis": "raw_edge_only_no_complete_benchmark",
                    "minimum_sample_gate": 30,
                    "raw_edge": {"sample_size": 1, "mean_net_return_fraction": 0.0018, "win_rate": 1.0,
                                 "max_drawdown_fraction": 0.0, "avg_mae_fraction": -0.0004, "avg_mfe_fraction": 0.0021,
                                 "profit_factor": "inf"},
                    "formal_alpha": {"available": False, "status": "NOT_MEASURABLE"},
                }
            },
        })
        return store, status, report

    def test_projection_is_sanitized_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, status, report = self._fixture(Path(tmp))
            payload = build_projection(store, status, report)
        encoded = json.dumps(payload)
        self.assertEqual(payload["schema_version"], "briefrooms-experience-store-public-v1")
        self.assertTrue(payload["authority"]["read_only"])
        self.assertFalse(payload["authority"]["production_decision_influence"])
        forbidden_keys = {"signal_snapshot", "head_hash", "event_hash", "previous_hash", "raw", "decision"}

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key, forbidden_keys)
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(payload)
        self.assertNotIn("/private/", encoded)

    def test_summary_engine_actions_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, status, report = self._fixture(Path(tmp))
            payload = build_projection(store, status, report)
        self.assertEqual(payload["summary"]["experience_count"], 3)
        self.assertEqual(payload["summary"]["settled_count"], 1)
        self.assertEqual(payload["summary"]["pending_count"], 2)
        self.assertEqual(payload["summary"]["source_count"], 2)
        arm = next(row for row in payload["engines"] if row["engine"] == "eurusd-abc-a")
        self.assertEqual(arm["actions"]["LONG"], 1)
        self.assertEqual(arm["actions"]["FLAT"], 1)
        self.assertEqual(arm["evidence"]["sample_size"], 1)
        gpw = next(row for row in payload["engines"] if row["engine"] == "gpw")
        self.assertEqual(gpw["actions"]["OTHER"], 1)

    def test_recent_rows_use_net_then_gross_and_do_not_expose_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, status, report = self._fixture(Path(tmp))
            payload = build_projection(store, status, report)
        settled = next(row for row in payload["recent_experiences"] if row["status"] == "SETTLED")
        self.assertEqual(settled["return_basis"], "GROSS")
        self.assertAlmostEqual(settled["return_fraction"], 0.0018)
        self.assertNotIn("raw", settled)
        self.assertNotIn("decision", settled)

    def test_empty_store_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "experience_store.jsonl"
            store.write_text("", encoding="utf-8")
            status = root / "experience_store_status.json"
            report = root / "shadow_alpha_report.json"
            self._write_json(status, {
                "schema_version": "briefrooms-experience-store-v1", "generated_at": "2026-09-02T00:00:00Z",
                "experience_count": 0, "settled_count": 0, "pending_count": 0, "source_ledgers": [],
            })
            self._write_json(report, {
                "schema_version": "briefrooms-shadow-alpha-report-v1", "overall": {"minimum_sample_gate": 30}, "by_engine": {},
            })
            payload = build_projection(store, status, report)
        self.assertEqual(payload["summary"]["experience_count"], 0)
        self.assertEqual(payload["engines"], [])
        self.assertEqual(payload["recent_experiences"], [])


if __name__ == "__main__":
    unittest.main()
