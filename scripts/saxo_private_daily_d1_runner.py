#!/usr/bin/env python3
"""Dedicated FREE-tier Cloudflare D1 state for EURUSD Saxo SIM paper Daily.

No Saxo broker orders. No private EURUSD quotes/trades in public Git, Actions
artifacts or logs. Never automatically initialize a missing or deleted state.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from saxo_private_daily_runner import (
    PrivateStateFailure, encode_state, decode_state, private_cycle,
)

ROW_LIMIT = 1_500_000
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS private_saxo_eurusd_daily (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  generation INTEGER NOT NULL CHECK (generation >= 0),
  payload TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  updated_at TEXT NOT NULL
)"""
CREATE_GENESIS_SQL = """INSERT INTO private_saxo_eurusd_daily
 (id, generation, payload, sha256, updated_at)
 VALUES (1, 0, '', '', ?) ON CONFLICT(id) DO NOTHING RETURNING generation"""
SELECT_SQL = """SELECT generation, payload, sha256
 FROM private_saxo_eurusd_daily WHERE id = 1"""
UPDATE_SQL = """UPDATE private_saxo_eurusd_daily
 SET generation = ?, payload = ?, sha256 = ?, updated_at = ?
 WHERE id = 1 AND generation = ? AND sha256 = ?
 RETURNING generation"""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _encode(directory: Path, sequence: int) -> str:
    raw = encode_state(directory, sequence)
    compressed = gzip.compress(raw, compresslevel=7, mtime=0)
    data = base64.b64encode(compressed).decode("ascii")
    if len(data) > ROW_LIMIT:
        raise PrivateStateFailure("private_d1_snapshot_exceeds_safe_row_limit")
    return data


def _decode(bundle: str, directory: Path) -> int:
    try:
        data = base64.b64decode(bundle, validate=True)
        with gzip.GzipFile(fileobj=__import__("io").BytesIO(data), mode="rb") as source:
            raw = source.read(MAX_EXPANDED_BYTES + 1)
        if len(raw) > MAX_EXPANDED_BYTES:
            raise ValueError("decompressed_over_limit")
        return decode_state(raw, directory)
    except Exception:
        raise PrivateStateFailure("private_d1_integrity_failed") from None


class D1:
    def __init__(self) -> None:
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        database = os.environ.get("SAXO_PRIVATE_D1_DATABASE_ID", "")
        token = os.environ.get("SAXO_PRIVATE_D1_API_TOKEN", "")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
            raise PrivateStateFailure("private_d1_account_missing")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", database):
            raise PrivateStateFailure("private_d1_database_missing")
        if not token:
            raise PrivateStateFailure("private_d1_token_missing")
        self.url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account}"
            f"/d1/database/{database}/query"
        )
        self.token = token

    def query(self, sql: str, params: list[str] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"sql": sql, "params": params or []}).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "BriefRooms-Saxo-Private-Daily/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read(3_200_000))
            if payload.get("success") is not True:
                raise ValueError("D1 returned failure")
            result = payload.get("result")
            if not isinstance(result, list) or len(result) != 1:
                raise ValueError("unexpected D1 response shape")
            item = result[0]
            if item.get("success") is not True or not isinstance(item.get("results"), list):
                raise ValueError("D1 query not successful")
            return item
        except Exception:
            # Cloudflare might include submitted SQL or bound values in errors.
            raise PrivateStateFailure("private_d1_query_unavailable") from None


def initialize(d1: D1) -> None:
    """Explicit once-only initialization. Never called from scheduled cycles."""
    d1.query(SCHEMA_SQL)
    rows = d1.query(CREATE_GENESIS_SQL, [datetime.now(timezone.utc).isoformat()])["results"]
    if len(rows) != 1 or rows[0].get("generation") != 0:
        raise PrivateStateFailure("private_d1_state_already_initialized")
    print("PRIVATE_D1_GENESIS_CREATED=TRUE")


def load(d1: D1, directory: Path) -> tuple[int, str]:
    rows = d1.query(SELECT_SQL)["results"]
    if len(rows) != 1:
        raise PrivateStateFailure("private_d1_state_missing_fail_closed")
    row = rows[0]
    generation = row.get("generation")
    stored = row.get("payload")
    digest = row.get("sha256")
    if type(generation) is not int or generation < 0:
        raise PrivateStateFailure("private_d1_generation_invalid")
    if generation == 0:
        if stored != "" or digest != "":
            raise PrivateStateFailure("private_d1_genesis_corrupt")
        return 0, ""
    if not isinstance(stored, str) or not isinstance(digest, str):
        raise PrivateStateFailure("private_d1_payload_invalid")
    if len(stored) > ROW_LIMIT or _hash(stored) != digest:
        raise PrivateStateFailure("private_d1_checksum_mismatch")
    sequence = _decode(stored, directory)
    if sequence != generation:
        raise PrivateStateFailure("private_d1_generation_mismatch")
    return generation, digest


def save(d1: D1, directory: Path, previous: int, previous_digest: str) -> None:
    body = _encode(directory, previous + 1)
    digest = _hash(body)
    # One atomic SQL UPDATE with optimistic concurrency. Never overwrite a
    # different private trade position or concurrently modified history.
    rows = d1.query(UPDATE_SQL, [
        str(previous + 1), body, digest,
        datetime.now(timezone.utc).isoformat(),
        str(previous), previous_digest,
    ])["results"]
    if len(rows) != 1 or int(rows[0].get("generation", -1)) != previous + 1:
        raise PrivateStateFailure("private_d1_concurrent_update_blocked")
    verified = d1.query(SELECT_SQL)["results"]
    if (len(verified) != 1 or verified[0].get("generation") != previous + 1
            or verified[0].get("sha256") != digest or verified[0].get("payload") != body):
        raise PrivateStateFailure("private_d1_write_readback_mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true")
    args = parser.parse_args(argv)

    try:
        d1 = D1()
        if args.initialize:
            if os.environ.get("SAXO_PRIVATE_D1_BOOTSTRAP_ALLOWED") != "true":
                raise PrivateStateFailure("private_d1_bootstrap_not_authorized")
            initialize(d1)
            print("RESULT=PRIVATE_SAXO_D1_INITIALIZED")
            return 0
        if (os.environ.get("BRIEFROOMS_PRIVATE_RUNTIME") != "1"
                or os.environ.get("SAXO_PRIVATE_PRIMARY") != "1"
                or os.environ.get("SAXO_PRIVATE_AUTO_ENABLED") != "true"):
            raise PrivateStateFailure("private_d1_runtime_not_enabled")
        # A private Daily cycle cannot silently revert to the legacy REST
        # Saxo Worker after the native SIM WebSocket stream was commissioned.
        from saxo_private_feed import _private_quote_endpoint, SAXO_STREAM_WORKER_URL, PrivateSaxoFeedError
        if os.environ.get("SAXO_STREAM_PRIMARY") != "1":
            raise PrivateStateFailure("private_d1_stream_not_enabled")
        try:
            if _private_quote_endpoint() != SAXO_STREAM_WORKER_URL:
                raise PrivateSaxoFeedError("private_saxo_stream_endpoint_not_configured")
        except PrivateSaxoFeedError:
            raise PrivateStateFailure("private_d1_stream_endpoint_invalid") from None
        if not os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD"):
            raise PrivateStateFailure("private_saxo_auth_missing")
        # A five-minute schedule continues over weekends. Do not manufacture
        # an indicative trade or treat the closed FX market as a failed cycle.
        from daily_eurusd_cycle_watchdog import fx_market_active
        if not fx_market_active(datetime.now(timezone.utc)):
            print("RESULT=PRIVATE_SAXO_D1_MARKET_INACTIVE_NOOP")
            return 0
        with tempfile.TemporaryDirectory(prefix="brs-saxo-d1-private-") as name:
            directory = Path(name).resolve()
            repo = Path(__file__).resolve().parents[1]
            if directory == repo or directory.is_relative_to(repo):
                raise PrivateStateFailure("private_d1_directory_not_isolated")
            os.environ["BRIEFROOMS_PRIVATE_DATA_DIR"] = str(directory)
            generation, previous_digest = load(d1, directory)
            outcome = private_cycle(directory)
            save(d1, directory, generation, previous_digest)
        print("PRIVATE_SAXO_DAILY_CYCLE=" + outcome)
        print("RESULT=PRIVATE_SAXO_D1_DAILY_CYCLE_PASS")
        return 0
    except Exception as exc:
        # Diagnostics contain only code-defined categories, never response data.
        reason = str(exc) if isinstance(exc, PrivateStateFailure) else "private_cycle_failed"
        print("RESULT=PRIVATE_SAXO_D1_DAILY_FAIL_CLOSED")
        print("FAILURE_CLASS=" + reason)
        return 1


if __name__ == "__main__":
    sys.exit(main())
