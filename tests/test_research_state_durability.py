from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import research_state_durability as rsd

UTC = timezone.utc
LAYER = "pr12_company_entity"


class ResearchStateDurabilityTests(unittest.TestCase):
    def _seed_pr12(self, root: Path, value: int = 1) -> None:
        (root / "ENTITY_ACTIVATION_STATE.json").write_text(json.dumps({"value": value}), encoding="utf-8")
        (root / "BRACE_COMPANY_ENTITY_FRAMEWORK_REPORT.json").write_text(
            json.dumps({"mode": "research_shadow"}), encoding="utf-8"
        )

    def test_registry_covers_canonical_pr10_to_pr19_research_chain(self) -> None:
        self.assertEqual(
            set(rsd.LAYERS),
            {
                "pr10_broad_market",
                "pr11_sector_factor",
                "pr12_company_entity",
                "pr13_primary_source",
                "pr14_interpretation",
                "pr15_belief_forecast",
                "pr16_calibration",
                "pr16_1_world_state",
                "pr17_entity_bridge",
                "pr19_epistemic_graph",
            },
        )
        primary = [spec.primary_artifact for spec in rsd.LAYERS.values()]
        checkpoints = [rsd.checkpoint_artifact_name(layer) for layer in rsd.LAYERS]
        self.assertEqual(len(primary), len(set(primary)))
        self.assertEqual(len(checkpoints), len(set(checkpoints)))
        self.assertTrue(all(spec.required_files for spec in rsd.LAYERS.values()))

    def test_seal_verify_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_pr12(root)
            manifest = rsd.seal_state(
                LAYER,
                root,
                now=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
                producer_run_id="100",
                producer_head_sha="abc",
            )
            verified = rsd.verify_state(LAYER, root)
            self.assertEqual(verified["checkpoint_id"], manifest["checkpoint_id"])
            self.assertEqual(manifest["durability_contract"]["missing_prior_state_policy"], "FAIL_CLOSED")
            self.assertFalse(manifest["durability_contract"]["silent_first_run_reset"])
            self.assertFalse(manifest["durability_contract"]["public_repository_state_persistence"])

            self._seed_pr12(root, value=2)
            with self.assertRaisesRegex(RuntimeError, "payload hash/size manifest mismatch"):
                rsd.verify_state(LAYER, root)

    def test_next_checkpoint_preserves_parent_in_append_only_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_pr12(root)
            first = rsd.seal_state(
                LAYER,
                root,
                now=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
                producer_run_id="100",
                producer_head_sha="abc",
            )
            self._seed_pr12(root, value=2)
            second = rsd.seal_state(
                LAYER,
                root,
                now=datetime(2026, 8, 21, 18, 0, tzinfo=UTC),
                producer_run_id="101",
                producer_head_sha="def",
            )
            self.assertEqual(second["parent_checkpoint_id"], first["checkpoint_id"])
            history = [
                json.loads(line)
                for line in (root / rsd.HISTORY_FILENAME).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([row["checkpoint_id"] for row in history], [first["checkpoint_id"]])
            rsd.verify_state(LAYER, root)

    def test_legacy_artifact_can_be_migrated_without_claiming_historical_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_pr12(root)
            self.assertEqual(rsd.verify_state(LAYER, root, allow_legacy=True)["status"], "legacy_unsealed")
            manifest = rsd.seal_state(
                LAYER,
                root,
                now=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
                producer_run_id="legacy-run",
                producer_head_sha="legacy-sha",
                migration_from_legacy=True,
            )
            self.assertIsNone(manifest["parent_checkpoint_id"])
            self.assertTrue(manifest["legacy_parent_without_manifest"])
            rsd.verify_state(LAYER, root)

    def test_pack_requires_sealed_state_and_round_trips_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            root.mkdir()
            self._seed_pr12(root)
            output = Path(tmp) / "checkpoint.tgz"
            with self.assertRaisesRegex(RuntimeError, "durability manifest is missing"):
                rsd.pack_state(LAYER, root, output)
            rsd.seal_state(LAYER, root)
            rsd.pack_state(LAYER, root, output)
            unpacked = Path(tmp) / "unpacked"
            unpacked.mkdir()
            rsd._safe_extract_tar(output, unpacked)
            self.assertTrue((unpacked / rsd.MANIFEST_FILENAME).is_file())
            rsd.verify_state(LAYER, unpacked)

    def test_restore_prefers_primary_then_checkpoint_and_never_silent_bootstraps(self) -> None:
        primary = {"source": "primary", "status": "sealed", "layer_id": LAYER}
        checkpoint = {"source": "durability_checkpoint", "status": "sealed", "layer_id": LAYER}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(rsd, "_try_restore_from_artifact_name", return_value=primary) as mocked:
                self.assertEqual(
                    rsd.restore_state(LAYER, root, repository="o/r", token="t")["source"],
                    "primary",
                )
                self.assertEqual(mocked.call_count, 1)

            with patch.object(rsd, "_try_restore_from_artifact_name", side_effect=[None, checkpoint]) as mocked:
                self.assertEqual(
                    rsd.restore_state(LAYER, root, repository="o/r", token="t")["source"],
                    "durability_checkpoint",
                )
                self.assertEqual(mocked.call_count, 2)

            with patch.object(rsd, "_try_restore_from_artifact_name", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "FAIL_CLOSED"):
                    rsd.restore_state(LAYER, root, repository="o/r", token="t")
                optional = rsd.restore_state(LAYER, root, repository="o/r", token="t", optional=True)
                self.assertEqual(optional["status"], "missing_optional")

    def test_artifact_candidate_must_be_successful_main_run_from_expected_producer(self) -> None:
        artifact = {"workflow_run": {"id": 77, "head_branch": "main", "head_sha": "abc"}}
        with patch.object(
            rsd,
            "_api_json",
            return_value={
                "name": "BRACE Company-Entity Framework Shadow",
                "head_branch": "main",
                "conclusion": "success",
            },
        ):
            self.assertTrue(
                rsd._successful_main_candidate(
                    "o/r", "t", artifact, "BRACE Company-Entity Framework Shadow"
                )
            )
        feature = {"workflow_run": {"id": 77, "head_branch": "feature", "head_sha": "abc"}}
        with patch.object(rsd, "_api_json") as mocked:
            self.assertFalse(
                rsd._successful_main_candidate(
                    "o/r", "t", feature, "BRACE Company-Entity Framework Shadow"
                )
            )
            mocked.assert_not_called()

    def test_tar_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tgz"
            with tarfile.open(archive, "w:gz") as tf:
                info = tarfile.TarInfo("../escape.json")
                raw = b"{}"
                info.size = len(raw)
                tf.addfile(info, io.BytesIO(raw))
            destination = Path(tmp) / "out"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                rsd._safe_extract_tar(archive, destination)


if __name__ == "__main__":
    unittest.main()
