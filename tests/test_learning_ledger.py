import json
import tempfile
import unittest
from pathlib import Path

from scripts.learning_ledger import append_event, read_events, safety_controls, verify_chain


class LearningLedgerTests(unittest.TestCase):
    def test_append_is_idempotent_and_hash_chained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            first = append_event(path, event_type="forecast", occurred_at="2026-08-23T10:00:00Z", subject_id="forecast-1", payload={"probability": 0.7})
            same = append_event(path, event_type="forecast", occurred_at="2026-08-23T10:00:00Z", subject_id="forecast-1", payload={"probability": 0.7})
            self.assertEqual(first["event_id"], same["event_id"])
            self.assertEqual(1, len(read_events(path)))
            second = append_event(path, event_type="outcome", occurred_at="2026-08-24T10:00:00Z", subject_id="forecast-1", payload={"outcome": True})
            self.assertEqual(first["event_hash"], second["previous_hash"])
            self.assertTrue(verify_chain(path)["ok"])

    def test_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            append_event(path, event_type="decision", occurred_at="2026-08-23T10:00:00Z", subject_id="decision-1", payload={"action": "HOLD"})
            row = json.loads(path.read_text(encoding="utf-8"))
            row["payload"]["action"] = "BUY"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            self.assertFalse(verify_chain(path)["ok"])
            with self.assertRaises(RuntimeError):
                append_event(path, event_type="outcome", occurred_at="2026-08-24T10:00:00Z", subject_id="decision-1", payload={"return": 0.1})

    def test_zero_authority(self):
        self.assertTrue(safety_controls())
        self.assertTrue(all(value is False for value in safety_controls().values()))

    def test_rejects_unknown_event_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                append_event(Path(tmp) / "ledger.jsonl", event_type="auto_tune", occurred_at="2026-08-23T10:00:00Z", subject_id="x", payload={})


if __name__ == "__main__":
    unittest.main()
