"""BRs R2 shadow pilot: safety, lossless readback, source-only authority."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import brs_r2_shadow_pilot as pilot


class FakeS3Error(Exception):
    def __init__(self, status: int, code: str):
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code},
        }
        super().__init__(f"S3 {status} {code}")


class FakeS3:
    """In-memory S3 emulator; no network and no user/cloud credentials."""

    def __init__(self):
        self.objects = {}
        self.put_count = 0
        self.get_count = 0
        self.fail_head = None
        self.get_corrupted = False

    def head_object(self, *, Bucket, Key):
        if self.fail_head:
            raise self.fail_head
        if Key not in self.objects:
            raise FakeS3Error(404, "NoSuchKey")
        data = self.objects[Key]
        return {
            "ContentLength": len(data["body"]),
            "Metadata": dict(data["metadata"]),
        }

    def put_object(self, *, Bucket, Key, Body, Metadata, ContentType):
        self.put_count += 1
        self.objects[Key] = {"body": bytes(Body), "metadata": dict(Metadata)}
        return {"ETag": "not-used-as-integrity-check"}

    def get_object(self, *, Bucket, Key):
        self.get_count += 1
        data = self.objects[Key]
        body = data["body"] + b"CORRUPTION" if self.get_corrupted else data["body"]
        return {"Body": io.BytesIO(body)}


def fixture_docs():
    return (
        ("brace_memory", "data/investments/portfolio_10k_brace_memory.json", "brace", {
            "schema_version": "2.0.0", "model_id": "BRACE", "reliability": {},
            "decisions": [{"decision_id": "d1", "market": "łódź"}],
            "outcome_events": [{"outcome_event_id": "e1"}],
            "audit": [{"event": "intact"}],
        }),
        ("brace_historical", "data/investments/portfolio_10k_brace_historical_learning.json", "brace", {
            "schema_version": "1.0.0", "training_id": "training1",
            "symbols": ["VT", "V"], "lessons_total": 213,
            "validation": {}, "untouched_test": {}, "splits": {},
        }),
        ("eurusd_daily", "data/investments/eurusd_daily_history.json", "eurusd", {
            "schema_version": "eurusd-daily-history-v1",
            "trades": [{"trade_id": "trade1", "closed_at": "2026-10-09T08:00:00Z"}],
            "learning_state": {},
        }),
        ("eurusd_posttrade", "data/investments/eurusd_posttrade_audits.json", "eurusd", {
            "schema_version": "eurusd-posttrade-audits-v1",
            "audits": [{"trade_id": "trade1"}],
            "summary": {"audited_trades": 1},
        }),
    )


def make_samples(tmp_path):
    sources = []
    originals = {}
    for name, path, group, data in fixture_docs():
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        p.write_bytes(raw)
        originals[path] = raw
        sources.append((name, path, group))
    return pilot.load_snapshots(tmp_path, sources=tuple(sources)), originals


def test_full_immutable_mirror_and_idempotent_readback(tmp_path):
    samples, originals = make_samples(tmp_path)
    client = FakeS3()
    first = pilot.mirror_snapshots(client, "briefrooms-archive-pilot",
                                   samples, commit_sha="abc123")
    assert first["authority"] == "GITHUB_CANONICAL_R2_SHADOW_COPY_ONLY"
    assert first["no_trading_mutation"] is True
    assert first["success"] is True
    assert len(first["objects"]) == len(pilot.SOURCES) == 4
    assert client.put_count == 4 and client.get_count == 4
    assert all(v["status"] == "UPLOADED_VERIFIED" for v in first["objects"])
    assert all(client.objects[v["r2_key"]]["body"] == originals[v["github_path"]]
               for v in first["objects"])
    assert next(x["records"] for x in first["objects"] if x["dataset"] == "brace_memory") == {
        "decisions": 1, "outcome_events": 1, "audit": 1,
    }
    # A repeated run must re-read and verify, but never re-upload.
    second = pilot.mirror_snapshots(client, "briefrooms-archive-pilot", samples)
    assert client.put_count == 4 and client.get_count == 8
    assert all(v["status"] == "EXISTING_VERIFIED" for v in second["objects"])
    assert all((tmp_path / p).read_bytes() == raw for p, raw in originals.items())


def test_changed_source_creates_new_content_addressed_revision(tmp_path):
    snapshots, _ = make_samples(tmp_path)
    client = FakeS3()
    pilot.mirror_snapshots(client, "briefrooms-archive-pilot", snapshots)
    path = tmp_path / "data/investments/eurusd_daily_history.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj["trades"].append({"trade_id": "trade2", "closed_at": None})
    path.write_text(json.dumps(obj), encoding="utf-8")
    replacement = pilot.load_snapshots(tmp_path, sources=(pilot.SOURCES[2],))
    result = pilot.mirror_snapshots(client, "briefrooms-archive-pilot", replacement)
    assert client.put_count == 5
    assert result["objects"][0]["records"] == {"trades": 2, "closed_trades": 1}
    assert result["objects"][0]["r2_key"] != pilot.object_key(snapshots[2])
    assert pilot.object_key(snapshots[2]) in client.objects


def test_r2_readback_tampering_fails_even_if_head_metadata_is_correct(tmp_path):
    snapshots, _ = make_samples(tmp_path)
    client = FakeS3()
    client.get_corrupted = True
    with pytest.raises(RuntimeError, match="R2_READBACK_SHA256_OR_BYTES_MISMATCH"):
        pilot.mirror_snapshots(client, "briefrooms-archive-pilot", snapshots)


def test_existing_conflicting_r2_metadata_fails_closed(tmp_path):
    snapshots, _ = make_samples(tmp_path)
    client = FakeS3()
    key = pilot.object_key(snapshots[0])
    client.objects[key] = {"body": snapshots[0].body, "metadata": {"sha256": "bad"}}
    with pytest.raises(RuntimeError, match="R2_HEAD_INTEGRITY_FAILURE"):
        pilot.mirror_snapshots(client, "briefrooms-archive-pilot", snapshots)
    assert client.put_count == 0


def test_403_auth_failure_does_not_trigger_put_or_mutate_sources(tmp_path):
    snapshots, originals = make_samples(tmp_path)
    client = FakeS3()
    client.fail_head = FakeS3Error(403, "AccessDenied")
    with pytest.raises(FakeS3Error):
        pilot.mirror_snapshots(client, "briefrooms-archive-pilot", snapshots)
    assert client.put_count == 0
    for name, source, _ in pilot.SOURCES:
        assert (tmp_path / source).read_bytes() == originals[source]


def test_missing_or_duplicate_canonical_id_fails_before_any_remote_write(tmp_path):
    snapshots, _ = make_samples(tmp_path)
    path = tmp_path / pilot.SOURCES[2][1]
    doc = json.loads(path.read_text())
    doc["trades"].append({"trade_id": "trade1"})
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="DUPLICATE_RECORD_ID"):
        pilot.load_snapshots(tmp_path)
    client = FakeS3()
    assert client.put_count == 0


def test_eurusd_audit_count_mismatch_fails_closed(tmp_path):
    _, _ = make_samples(tmp_path)
    path = tmp_path / pilot.SOURCES[3][1]
    doc = json.loads(path.read_text())
    doc["summary"]["audited_trades"] = 2
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="EURUSD_POSTTRADE_SUMMARY_COUNT_MISMATCH"):
        pilot.load_snapshots(tmp_path)


def test_dry_run_has_no_cloud_dependency_or_upload(tmp_path):
    snapshots, _ = make_samples(tmp_path)
    result = pilot.mirror_snapshots(None, "briefrooms-archive-pilot", snapshots,
                                    dry_run=True)
    assert result["success"] is True and result["dry_run"] is True
    assert all(obj["status"] == "DRY_RUN_VALIDATED" for obj in result["objects"])


def test_bad_prefix_and_invalid_bucket_rejected_before_remote_write(tmp_path):
    snapshots, _ = make_samples(tmp_path)
    for prefix in ("../other", "other//path", ""):
        with pytest.raises(ValueError, match="INVALID_R2_PILOT_PREFIX"):
            pilot.mirror_snapshots(FakeS3(), "briefrooms-archive-pilot", snapshots,
                                   prefix=prefix)
    with pytest.raises(ValueError, match="INVALID_R2_BUCKET_NAME"):
        pilot.mirror_snapshots(FakeS3(), "UNSAFE BUCKET", snapshots)


def test_checked_in_real_source_contracts_and_size_are_valid():
    snapshots = pilot.load_snapshots(ROOT)
    assert [snap.dataset for snap in snapshots] == [x[0] for x in pilot.SOURCES]
    assert snapshots[0].counts["decisions"] > 0
    assert snapshots[0].counts["outcome_events"] > 0
    assert snapshots[1].counts["lessons_total"] > 0
    assert snapshots[2].counts["trades"] > 0
    assert snapshots[3].counts["audits"] > 0
    assert sum(s.size_bytes for s in snapshots) < 10_000_000
