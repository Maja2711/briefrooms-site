#!/usr/bin/env python3
"""Isolated, scheduled EURUSD Daily Saxo-SIM PAPER cycle.

Private bytes only in Cloudflare R2 and ephemeral runner temp, never GitHub.
This is not a Saxo order gateway. Fails closed on missing/corrupt state,
missing credentials, stale quote, or concurrent R2 modification.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_KEY = "private/saxo-eurusd-daily/v1/state.json"
MARKER_KEY = "private/saxo-eurusd-daily/v1/initialized.json"
SCHEMA = "brs-private-saxo-daily-r2-v1"
FILENAMES = (
    "eurusd_daily_spot.json",
    "eurusd_daily_history.json",
    "eurusd_production_policy_state.json",
    "eurusd_production_decisions.json",
)
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_OBJECT_BYTES = 50 * 1024 * 1024


class PrivateStateFailure(RuntimeError):
    """Never include R2 payloads, account identifiers or private quotations."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _client():
    import boto3
    from botocore.config import Config

    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    key = os.environ.get("SAXO_PRIVATE_R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("SAXO_PRIVATE_R2_SECRET_ACCESS_KEY", "").strip()
    if not (len(account) == 32 and key and secret):
        raise PrivateStateFailure("missing_private_r2_credentials")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"mode": "standard", "max_attempts": 3},
        ),
    )


def _bucket() -> str:
    name = os.environ.get("SAXO_PRIVATE_R2_BUCKET", "").strip()
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in name):
        raise PrivateStateFailure("missing_or_invalid_private_bucket")
    if name == os.environ.get("BRS_R2_PILOT_BUCKET"):
        raise PrivateStateFailure("private_bucket_must_not_be_shadow_archive")
    return name


def _missing(exc: Exception) -> bool:
    try:
        return str(exc.response["Error"]["Code"]) in ("NoSuchKey", "404", "NotFound")
    except (AttributeError, KeyError, TypeError):
        return False


def decode_state(payload: bytes, directory: Path) -> int:
    """Verify all bytes before writing any private file to the ephemeral directory."""
    if len(payload) > MAX_OBJECT_BYTES:
        raise PrivateStateFailure("state_object_too_large")
    try:
        data = json.loads(payload)
        if data["schema"] != SCHEMA or type(data["sequence"]) is not int or data["sequence"] < 1:
            raise ValueError("bad version")
        entries = data["files"]
        if not isinstance(entries, dict) or any(name not in FILENAMES for name in entries):
            raise ValueError("unexpected private file")
        if "eurusd_daily_spot.json" not in entries or "eurusd_daily_history.json" not in entries:
            raise ValueError("incomplete persisted state")
        verified: dict[str, bytes] = {}
        for name, entry in entries.items():
            raw = base64.b64decode(entry["body_b64"], validate=True)
            if len(raw) > MAX_FILE_BYTES or _hash(raw) != entry["sha256"]:
                raise ValueError("invalid checksum")
            if not isinstance(json.loads(raw), dict):
                raise ValueError("invalid JSON")
            verified[name] = raw
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError) as exc:
        raise PrivateStateFailure("state_schema_or_hash_invalid") from None
    # No data from R2 is ever written under the repository.
    for name, raw in verified.items():
        (directory / name).write_bytes(raw)
    return data["sequence"]


def encode_state(directory: Path, sequence: int) -> bytes:
    entries: dict[str, dict[str, str]] = {}
    for name in FILENAMES:
        path = directory / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise PrivateStateFailure("private_file_too_large")
        try:
            if not isinstance(json.loads(raw), dict):
                raise ValueError("not a json object")
        except (ValueError, UnicodeDecodeError):
            raise PrivateStateFailure("private_output_not_json") from None
        entries[name] = {"body_b64": base64.b64encode(raw).decode("ascii"),
                         "sha256": _hash(raw)}
    if not {"eurusd_daily_spot.json", "eurusd_daily_history.json"}.issubset(entries):
        raise PrivateStateFailure("cycle_did_not_persist_position_and_history")
    payload = json.dumps({
        "schema": SCHEMA, "sequence": sequence, "files": entries,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "trading_enabled": False, "redistributable": False,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_OBJECT_BYTES:
        raise PrivateStateFailure("private_state_snapshot_too_large")
    return payload


def load_r2_state(client: Any, bucket: str, directory: Path) -> tuple[int, str | None]:
    try:
        response = client.get_object(Bucket=bucket, Key=STATE_KEY)
        etag = response.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise PrivateStateFailure("r2_missing_etag")
        raw = response["Body"].read(MAX_OBJECT_BYTES + 1)
        return decode_state(raw, directory), etag
    except Exception as exc:
        if isinstance(exc, PrivateStateFailure):
            raise
        if not _missing(exc):
            raise PrivateStateFailure("r2_private_state_unreadable") from None

    # A lost canonical state may NEVER silently reinitialize a trading history.
    try:
        client.head_object(Bucket=bucket, Key=MARKER_KEY)
    except Exception as exc:
        if not _missing(exc):
            raise PrivateStateFailure("r2_initialization_guard_unreadable") from None
        return 0, None
    raise PrivateStateFailure("r2_state_disappeared_after_initialization")


def save_r2_state(client: Any, bucket: str, directory: Path, sequence: int, etag: str | None) -> None:
    payload = encode_state(directory, sequence + 1)
    kwargs: dict[str, Any] = {
        "Bucket": bucket, "Key": STATE_KEY, "Body": payload,
        "ContentType": "application/json", "CacheControl": "no-store",
    }
    kwargs["IfMatch" if etag else "IfNoneMatch"] = etag if etag else "*"
    try:
        client.put_object(**kwargs)
    except Exception:
        # Never retry an uncertain write with a different precondition.
        raise PrivateStateFailure("r2_conditional_write_failed") from None
    try:
        readback = client.get_object(Bucket=bucket, Key=STATE_KEY)["Body"].read(MAX_OBJECT_BYTES + 1)
        if _hash(readback) != _hash(payload):
            raise PrivateStateFailure("r2_readback_checksum_mismatch")
        if etag is None:
            marker = b'{"schema":"brs-private-saxo-daily-initialized-v1"}'
            client.put_object(
                Bucket=bucket, Key=MARKER_KEY, Body=marker,
                ContentType="application/json", IfNoneMatch="*",
            )
    except Exception:
        raise PrivateStateFailure("r2_postwrite_verification_failed") from None


def private_cycle(directory: Path) -> str:
    # No stdout/stderr from trading modules, which could include private prices.
    from daily_eurusd_spot_v19 import run_cycle
    from daily_eurusd_fast_lifecycle import apply_if_triggered

    output_path = directory / "eurusd_daily_spot.json"
    history_path = directory / "eurusd_daily_history.json"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        run_cycle(output_path, history_path)
        # Exit-only production guard: does not create positions or new direction.
        result = apply_if_triggered(output_path, history_path)
    if not isinstance(result, dict):
        raise PrivateStateFailure("invalid_private_lifecycle_result")
    return "EXIT" if result.get("changed") is True else "EVALUATED"


def main() -> int:
    if os.environ.get("BRIEFROOMS_PRIVATE_RUNTIME") != "1" or os.environ.get("SAXO_PRIVATE_PRIMARY") != "1":
        print("RESULT=BLOCKED_PRIVATE_RUNTIME_NOT_ENABLED")
        return 2
    if os.environ.get("SAXO_PRIVATE_AUTO_ENABLED") != "true":
        print("RESULT=BLOCKED_PRIVATE_AUTOMATION_NOT_ENABLED")
        return 2
    if not os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD"):
        print("RESULT=BLOCKED_MISSING_SAXO_PRIVATE_BASIC_PASSWORD")
        return 2
    try:
        client, bucket = _client(), _bucket()
        with tempfile.TemporaryDirectory(prefix="brs-saxo-private-") as folder:
            root = Path(folder).resolve()
            repository = Path(__file__).resolve().parents[1]
            if root == repository or root.is_relative_to(repository):
                raise PrivateStateFailure("private_root_under_repository")
            os.environ["BRIEFROOMS_PRIVATE_DATA_DIR"] = str(root)
            prior_sequence, etag = load_r2_state(client, bucket, root)
            outcome = private_cycle(root)
            save_r2_state(client, bucket, root, prior_sequence, etag)
        print("RESULT=PRIVATE_SAXO_DAILY_CYCLE_PASS")
        print("PRIVATE_SAXO_CYCLE_KIND=" + outcome)
        return 0
    except Exception as exc:
        # Never include exception text: it may contain provider data or paths.
        code = str(exc) if isinstance(exc, PrivateStateFailure) else "private_cycle_failed"
        print("RESULT=PRIVATE_SAXO_DAILY_FAIL_CLOSED")
        print("PRIVATE_SAXO_FAILURE_CLASS=" + code)
        return 1


if __name__ == "__main__":
    sys.exit(main())
