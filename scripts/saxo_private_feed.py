#!/usr/bin/env python3
"""Private Saxo SIM EUR/USD quote adapter for optional Daily paper quality checks.

This module is deliberately NOT a public EUR/USD market-data feed. It must
never return raw Saxo quotes to public artifacts, GitHub logs, or site JSON.
Saxo SIM quotes are usable only for explicitly authorized private testing.
No broker orders are sent.
"""
from __future__ import annotations

import base64
import json
import math
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Mapping

SAXO_WORKER_URL = (
    "https://briefrooms-saxo-eurusd.szczerbinski0577.workers.dev/saxo/quote"
)
# Single explicitly approved private stream endpoint, not a user-supplied URL.
SAXO_STREAM_WORKER_URL = "https://briefrooms-saxo-eurusd-stream.szczerbinski0577.workers.dev/saxo/quote"


def _private_quote_endpoint() -> str:
    """Prefer real WebSocket-fed side quotes ONLY when explicitly activated.

    Missing or malformed streaming configuration must not quietly fall back
    to the old intermittent REST quote and pretend the stream is running.
    """
    if os.environ.get("SAXO_STREAM_PRIMARY") != "1":
        return SAXO_WORKER_URL
    value = os.environ.get("SAXO_PRIVATE_STREAM_URL", "").strip()
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or value != SAXO_STREAM_WORKER_URL or
        parsed.username or parsed.password or parsed.port not in (None, 443)
        or parsed.path != "/saxo/quote" or parsed.query or parsed.fragment):
        raise PrivateSaxoFeedError("private_saxo_stream_endpoint_not_configured")
    return value
MAX_QUOTE_AGE_SECONDS = 90.0
MAX_FUTURE_SECONDS = 30.0

# Cloudflare rejects the default Python-urllib User-Agent for this Worker.
# Verified from GitHub Actions: Python-urllib -> 403, standard browser UA -> 200.
# Keep an explicit User-Agent for this *authenticated private* read-only API.
# No quote data or Saxo credentials may be logged or redistributed.
SAXO_PRIVATE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)



class PrivateSaxoFeedError(ValueError):
    """Fixed, non-sensitive failure; never attach broker response or credentials."""


def _reject() -> None:
    raise PrivateSaxoFeedError("private_saxo_quote_unavailable")


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _reject()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            _reject()
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _reject()


def validate_private_saxo_snapshot(
    payload: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate private SIM bid/ask pair for simulated execution."""
    mid = validate_private_saxo_quote(payload, now=now)
    return {
        "bid": float(payload["bid"]),
        "ask": float(payload["ask"]),
        "mid": mid,
        "observed_at": _time(
            payload.get("last_updated")
            or payload.get("last_updated_at")
            or payload.get("timestamp")
            or payload.get("LastUpdated")
        ),
        "source": "SAXO_SIM",
        "redistributable": False,
    }


def validate_private_saxo_quote(
    payload: Mapping[str, Any], *, now: datetime | None = None
) -> float:
    """Validate an actual Saxo SIM FX spot quote; return mid only in memory.

    The caller MUST NOT serialize or print the returned price as Saxo data.
    A non-redistributable source cannot be substituted for a licensed public
    execution feed. This method enforces freshness and coherent bid/ask.
    """
    if not isinstance(payload, Mapping):
        _reject()
    if payload.get("source") != "SAXO_SIM" or payload.get("instrument") not in (
        "EUR/USD", "EURUSD"
    ):
        _reject()
    if payload.get("redistributable") is not False:
        _reject()
    if payload.get("trading_enabled") is not False:
        _reject()
    # In stream-primary mode, reject old REST-like responses even when they
    # have the same BID/ASK fields. Never silently fake a WebSocket feed.
    if (os.environ.get("SAXO_STREAM_PRIMARY") == "1"
            and payload.get("timestamp_origin") != "SAXO_STREAM_PRICE_SIDE_RECEIVED_AT"):
        _reject()
    if str(payload.get("market_state") or "").lower() != "open":
        _reject()
    try:
        if float(payload.get("delayed_by_minutes", -1)) != 0:
            _reject()
        bid = float(payload["bid"])
        ask = float(payload["ask"])
        mid = float(payload["mid"])
        if not all(math.isfinite(x) for x in (bid, ask, mid)):
            _reject()
        if not (0.8 < bid <= mid <= ask < 1.5):
            _reject()
        if not 0 < (ask - bid) <= 0.0015:
            _reject()
        if abs(mid - (bid + ask) / 2.0) > 0.00002:
            _reject()
    except (TypeError, KeyError, ValueError, OverflowError):
        _reject()
    stamp = _time(
        payload.get("last_updated")
        or payload.get("last_updated_at")
        or payload.get("timestamp")
        or payload.get("LastUpdated")
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - stamp).total_seconds()
    if age < -MAX_FUTURE_SECONDS or age > MAX_QUOTE_AGE_SECONDS:
        _reject()
    return mid


def assert_private_state_destination(
    position: Mapping[str, Any], output_path: Any, history_path: Any = None
) -> None:
    """Never let Saxo-derived positions be persisted in a public checkout."""
    from pathlib import Path

    execution = position.get("execution_price_engine") or {}
    if not isinstance(execution, Mapping) or execution.get("private_saxo_primary") is not True:
        return
    if (os.environ.get("SAXO_PRIVATE_PRIMARY") != "1"
            or os.environ.get("BRIEFROOMS_PRIVATE_RUNTIME") != "1"):
        raise ValueError("private Saxo exit needs private runtime")
    root_name = os.environ.get("BRIEFROOMS_PRIVATE_DATA_DIR", "")
    if not root_name or not Path(root_name).is_absolute():
        raise ValueError("private Saxo exit needs absolute off-repository state")
    root = Path(root_name).resolve()
    repository = Path(__file__).resolve().parents[1]
    if root == repository or root.is_relative_to(repository):
        raise ValueError("private Saxo state cannot be stored in public repository")
    paths = [output_path] + ([history_path] if history_path is not None else [])
    if any(not Path(p).resolve().is_relative_to(root) for p in paths):
        raise ValueError("private Saxo state must stay in private directory")


def private_saxo_exit_record(
    position: Mapping[str, Any], trigger: Mapping[str, Any], *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Settle a privately triggered paper exit at fresh Saxo SIM BID/ASK.

    Yahoo/Belief/macro may establish *why* exit is necessary. Saxo alone sets
    the current exit price. No trade is written if the Saxo quote fails.
    """
    from daily_eurusd_lifecycle import _close_record, parse_iso

    execution = position.get("execution_price_engine") or {}
    if not isinstance(execution, Mapping) or execution.get("private_saxo_primary") is not True:
        raise PrivateSaxoFeedError("private_saxo_position_required")
    if (os.environ.get("SAXO_PRIVATE_PRIMARY") != "1"
            or os.environ.get("BRIEFROOMS_PRIVATE_RUNTIME") != "1"):
        raise PrivateSaxoFeedError("private_saxo_runtime_required")
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    quote = fetch_private_saxo_quote(now=clock)
    stamp = quote["observed_at"]
    if not isinstance(stamp, datetime) or not 0 <= (clock - stamp).total_seconds() <= 15:
        _reject()
    opened = parse_iso(str(position.get("opened_at") or ""))
    if opened is None or stamp <= opened:
        _reject()
    direction = str(position.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        _reject()
    price = round(float(quote["bid"] if direction == "LONG" else quote["ask"]), 5)
    reason = str(trigger.get("exit_reason") or "")
    if not reason:
        _reject()
    result = _close_record(
        position, exit_reason=reason, exit_price=price,
        exited_at=stamp, exit_bar=None,
    )
    # Preserve non-price trigger diagnostics without treating the Yahoo bar
    # as the execution quote or backdating a private Saxo fill.
    source_monitor = trigger.get("monitor") or {}
    if isinstance(source_monitor, Mapping):
        for name in ("dynamic_exit", "macro_event", "event_intelligence"):
            if name in source_monitor:
                result["monitor"][name] = source_monitor[name]
    for name in (
        "entry_thesis", "execution_price_engine", "decision_manifest",
        "contextual_entry_policy_at_entry",
    ):
        if name in position and isinstance(position[name], Mapping):
            result[name] = dict(position[name])
    result["monitor"].update({
        "source": "Saxo OpenAPI SIM:EUR/USD (private indicative quote)",
        "trigger_source": "Yahoo 1m OHLC / macro / exit policy",
        "exit_price_basis": "SAXO_SIM_BID_FOR_LONG_ASK_FOR_SHORT",
        "quote_timestamp": stamp.isoformat().replace("+00:00", "Z"),
        "private_saxo_bid": quote["bid"],
        "private_saxo_ask": quote["ask"],
        "redistributable": False,
        "no_broker_order": True,
        "trigger_closed_at_proposed": trigger.get("closed_at"),
    })
    return result


def fetch_private_saxo_quote(*, now: datetime | None = None) -> dict[str, Any]:
    """Obtain private Bid/Ask/Mid using machine-to-machine Cloudflare auth."""
    username = os.environ.get("SAXO_PRIVATE_BASIC_USER", "admin")
    password = os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD", "")
    if not username or not password or any(ch in username for ch in "\r\n:"):
        _reject()
    header = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        _private_quote_endpoint(),
        headers={
            "Authorization": f"Basic {header}",
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "User-Agent": SAXO_PRIVATE_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            if getattr(response, "status", 200) != 200:
                _reject()
            payload = json.loads(response.read(16385))
        return validate_private_saxo_snapshot(payload, now=now)
    except Exception:
        _reject()


def fetch_private_saxo_mid(*, now: datetime | None = None) -> float:
    """Access the fixed user-owned Worker over HTTPS using GitHub Actions secret.

    Password is the private Cloudflare Worker's HTTP Basic password, NOT the
    Saxo app key/secret. This function returns only an in-memory validated mid.
    Errors are intentionally sanitized (no body, URL query, or credentials).
    """
    username = os.environ.get("SAXO_PRIVATE_BASIC_USER", "admin")
    password = os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD", "")
    if not username or not password or any(ch in username for ch in "\r\n:"):
        _reject()
    header = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        _private_quote_endpoint(),
        headers={
            "Authorization": f"Basic {header}",
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "User-Agent": SAXO_PRIVATE_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            if getattr(response, "status", 200) != 200:
                _reject()
            payload = json.loads(response.read(16385))
        return validate_private_saxo_quote(payload, now=now)
    except Exception:
        _reject()
