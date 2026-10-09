#!/usr/bin/env python3
"""Opt-in BRs R2 SHADOW archive pilot.

GitHub main remains canonical. Replicate only allowlisted, existing BRACE and
EURUSD JSON snapshots; never mutate GitHub, trading state or source files.
Objects are content addressed by SHA-256 and re-read before reporting success.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Explicit scope: do not crawl repositories or ingest private/secrets files.
SOURCES = (
    ("brace_memory", "data/investments/portfolio_10k_brace_memory.json", "brace"),
    ("brace_historical", "data/investments/portfolio_10k_brace_historical_learning.json", "brace"),
    ("eurusd_daily", "data/investments/eurusd_daily_history.json", "eurusd"),
    ("eurusd_posttrade", "data/investments/eurusd_posttrade_audits.json", "eurusd"),
)
DEFAULT_PREFIX = "pilot/brs-v1"
DEFAULT_REPORT = Path("brs-r2-pilot-report.json")


@dataclass(frozen=True)
class Snapshot:
    dataset: str
    path: str
    group: str
    body: bytes
    sha256: str
    counts: dict[str, int]
    json_value: dict[str, Any]

    @property
    def size_bytes(self) -> int:
        return len(self.body)


def _require_unique_ids(rows: list[Any], field: str, label: str) -> None:
    ids = [x.get(field) if isinstance(x, dict) else None for x in rows]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError(f"INCOMPLETE_RECORD_ID dataset={label} field={field}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"DUPLICATE_RECORD_ID dataset={label} field={field}")


def validate_document(dataset: str, data: Any) -> dict[str, int]:
    """Guard source completeness and give meaningful record-level parity checks."""
    if not isinstance(data, dict) or not data.get("schema_version"):
        raise ValueError(f"INVALID_SOURCE_SCHEMA dataset={dataset}")

    if dataset == "brace_memory":
        if data.get("model_id") != "BRACE" or not isinstance(data.get("reliability"), dict):
            raise ValueError("INVALID_BRACE_MEMORY")
        decisions, outcomes, audit = data.get("decisions"), data.get("outcome_events"), data.get("audit")
        if not all(isinstance(v, list) for v in (decisions, outcomes, audit)):
            raise ValueError("MISSING_BRACE_LEARNING_ARRAY")
        _require_unique_ids(decisions, "decision_id", dataset)
        _require_unique_ids(outcomes, "outcome_event_id", dataset)
        return {"decisions": len(decisions), "outcome_events": len(outcomes), "audit": len(audit)}

    if dataset == "brace_historical":
        symbols = data.get("symbols")
        lessons = data.get("lessons_total")
        if (not isinstance(symbols, list) or not symbols or
                not isinstance(lessons, int) or isinstance(lessons, bool) or lessons < 0 or
                not data.get("training_id") or
                not isinstance(data.get("validation"), dict) or
                not isinstance(data.get("untouched_test"), dict) or
                not isinstance(data.get("splits"), dict)):
            raise ValueError("INCOMPLETE_BRACE_HISTORICAL_TRAINING")
        if any(not isinstance(symbol, str) or not symbol for symbol in symbols):
            raise ValueError("INVALID_BRACE_HISTORICAL_SYMBOL")
        return {"symbols": len(symbols), "lessons_total": lessons}

    if dataset == "eurusd_daily":
        trades = data.get("trades")
        if not isinstance(trades, list) or not isinstance(data.get("learning_state"), dict):
            raise ValueError("INCOMPLETE_EURUSD_DAILY_HISTORY")
        _require_unique_ids(trades, "trade_id", dataset)
        closed = sum(bool(row.get("closed_at")) for row in trades)
        return {"trades": len(trades), "closed_trades": closed}

    if dataset == "eurusd_posttrade":
        audits = data.get("audits")
        summary = data.get("summary")
        if not isinstance(audits, list) or not isinstance(summary, dict):
            raise ValueError("INCOMPLETE_EURUSD_POSTTRADE_AUDITS")
        _require_unique_ids(audits, "trade_id", dataset)
        if summary.get("audited_trades") != len(audits):
            raise ValueError("EURUSD_POSTTRADE_SUMMARY_COUNT_MISMATCH")
        return {"audits": len(audits)}

    raise ValueError(f"UNAPPROVED_DATASET {dataset}")


def load_snapshots(root: Path = ROOT, sources=SOURCES) -> list[Snapshot]:
    """Validate ALL sources before allowing any remote write."""
    snapshots = []
    for dataset, source_path, group in sources:
        source = root / source_path
        content = source.read_bytes()  # Never reformat source JSON.
        data = json.loads(content)
        counts = validate_document(dataset, data)
        snapshots.append(Snapshot(
            dataset=dataset, path=source_path, group=group, body=content,
            sha256=hashlib.sha256(content).hexdigest(), counts=counts, json_value=data,
        ))
    if not snapshots:
        raise ValueError("EMPTY_PILOT_SOURCE_ALLOWLIST")
    return snapshots


def object_key(snapshot: Snapshot, prefix: str = DEFAULT_PREFIX) -> str:
    cleaned = prefix.strip("/")
    if not re.fullmatch(r"[a-zA-Z0-9/_-]+", cleaned) or ".." in cleaned or "//" in cleaned:
        raise ValueError("INVALID_R2_PILOT_PREFIX")
    # Content-addressed keys: changed source creates a new immutable revision.
    filename = Path(snapshot.path).name
    return f"{cleaned}/{snapshot.group}/{filename}/{snapshot.sha256}.json"


def _not_found(error: Exception) -> bool:
    # Handle only the expected S3 404; permission/network faults MUST fail.
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = str(response.get("Error", {}).get("Code", ""))
    return status == 404 or code in ("404", "NoSuchKey", "NotFound")


def _verify_remote(client: Any, bucket: str, key: str, snapshot: Snapshot) -> None:
    head = client.head_object(Bucket=bucket, Key=key)
    metadata = {str(k).lower(): str(v) for k, v in head.get("Metadata", {}).items()}
    if (metadata.get("sha256") != snapshot.sha256 or
            int(head.get("ContentLength", -1)) != snapshot.size_bytes):
        raise RuntimeError(f"R2_HEAD_INTEGRITY_FAILURE dataset={snapshot.dataset}")
    response = client.get_object(Bucket=bucket, Key=key)
    with response["Body"] as stream:
        raw = stream.read()
    if hashlib.sha256(raw).hexdigest() != snapshot.sha256 or raw != snapshot.body:
        raise RuntimeError(f"R2_READBACK_SHA256_OR_BYTES_MISMATCH dataset={snapshot.dataset}")
    # SHA256 covers byte equality, but independently assert record completeness.
    remote_counts = validate_document(snapshot.dataset, json.loads(raw))
    if remote_counts != snapshot.counts:
        raise RuntimeError(f"R2_READBACK_RECORD_COUNT_MISMATCH dataset={snapshot.dataset}")


def mirror_snapshots(client: Any, bucket: str, snapshots: list[Snapshot],
                     prefix: str = DEFAULT_PREFIX, dry_run: bool = False,
                     commit_sha: str = "") -> dict[str, Any]:
    """Upload exclusively to private R2, never copy R2 data back into GitHub."""
    if not bucket or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise ValueError("INVALID_R2_BUCKET_NAME")
    if not snapshots:
        raise ValueError("NO_SNAPSHOTS_TO_MIRROR")
    report: dict[str, Any] = {
        "schema_version": "brs-r2-shadow-pilot-v1",
        "authority": "GITHUB_CANONICAL_R2_SHADOW_COPY_ONLY",
        "no_trading_mutation": True,
        "dry_run": bool(dry_run),
        "git_sha": commit_sha,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "objects": [],
    }
    # No local files or GitHub branch are ever rewritten by this function.
    for snap in snapshots:
        key = object_key(snap, prefix)
        if dry_run:
            action = "DRY_RUN_VALIDATED"
        else:
            try:
                client.head_object(Bucket=bucket, Key=key)
            except Exception as error:
                if not _not_found(error):
                    raise
                client.put_object(
                    Bucket=bucket, Key=key, Body=snap.body,
                    ContentType="application/json; charset=utf-8",
                    Metadata={"sha256": snap.sha256, "dataset": snap.dataset},
                )
                action = "UPLOADED_VERIFIED"
            else:
                action = "EXISTING_VERIFIED"
            # Mandatory readback, even for already existing objects.
            _verify_remote(client, bucket, key, snap)
        report["objects"].append({
            "dataset": snap.dataset, "github_path": snap.path,
            "r2_key": key, "sha256": snap.sha256,
            "bytes": snap.size_bytes, "records": snap.counts,
            "status": action,
        })
    report["success"] = True
    return report


def client_from_environment() -> Any:
    """Create standard Cloudflare R2 S3 client, credentials never logged."""
    account = os.getenv("BRS_R2_ACCOUNT_ID", "").strip()
    access = os.getenv("BRS_R2_ACCESS_KEY_ID", "").strip()
    secret = os.getenv("BRS_R2_SECRET_ACCESS_KEY", "").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not access or not secret:
        raise RuntimeError("R2_PILOT_CREDENTIALS_MISSING_OR_INVALID")
    import boto3
    from botocore.config import Config

    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{account.lower()}.r2.cloudflarestorage.com",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"mode": "standard", "max_attempts": 3},
                      connect_timeout=10, read_timeout=60),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bucket", default=os.getenv("BRS_R2_PILOT_BUCKET", ""))
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    snapshots = load_snapshots()
    if args.dry_run:
        # The local dry run has no cloud access and still checks all source schemas.
        report = mirror_snapshots(None, args.bucket or "brs-r2-pilot-dry-run",
                                  snapshots, args.prefix, dry_run=True,
                                  commit_sha=os.getenv("GITHUB_SHA", ""))
    else:
        # Explicit opt-in, fail-closed even when launched outside GitHub Actions.
        if os.getenv("BRS_R2_PILOT_ENABLED") != "true":
            raise RuntimeError("R2_PILOT_NOT_ENABLED")
        report = mirror_snapshots(client_from_environment(), args.bucket, snapshots,
                                  args.prefix, commit_sha=os.getenv("GITHUB_SHA", ""))

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    for entry in report["objects"]:
        print(f"R2_PILOT {entry['dataset']} {entry['status']} "
              f"bytes={entry['bytes']} records={entry['records']}")
    print(f"R2_PILOT_PASS mode={'DRY_RUN' if args.dry_run else 'R2_READBACK'} "
          f"objects={len(report['objects'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
