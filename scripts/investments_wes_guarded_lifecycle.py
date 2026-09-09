#!/usr/bin/env python3
"""Canonical production wrapper for the WES rolling lifecycle.

Adds fail-closed validation around early-close replacement authorization before
weekend-carry metadata can be granted. All production workflows should call this
module rather than the unguarded lifecycle directly.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Optional

import investments_wes_lifecycle as base

_BASE_APPLY_OPEN_REENTRY_METADATA = base.apply_open_reentry_metadata


def _authorization(item: Dict[str, Any]) -> Dict[str, Any]:
    auth = item.get("wes_entry_authorization")
    return auth if isinstance(auth, dict) else {}


def validate_open_reentry_authorization(item: Dict[str, Any], now=None) -> None:
    """Fail closed when a live early-reentry position does not match its WES authorization."""
    auth = _authorization(item)
    if str(auth.get("authorization_type") or "") != base.AUTHORIZATION_TYPE:
        return
    if not base.v5.open_position(item):
        return

    now = now or base.v2.now_local()
    if not base.early_reentry_authorized(item, now):
        raise RuntimeError(
            f"WES early-close re-entry authorization is missing or expired for "
            f"{item.get('instrument_id') or item.get('symbol') or 'unknown'}"
        )

    candidate = auth.get("candidate") if isinstance(auth.get("candidate"), dict) else {}
    authorized_direction = str(candidate.get("direction") or "").strip().lower()
    actual_direction = str(item.get("direction") or "").strip().lower()
    valid = {"long", "short"}
    if authorized_direction not in valid or actual_direction not in valid:
        raise RuntimeError(
            f"WES early-close re-entry direction is invalid for "
            f"{item.get('instrument_id') or item.get('symbol') or 'unknown'}: "
            f"authorized={authorized_direction or 'missing'}, actual={actual_direction or 'missing'}"
        )
    if authorized_direction != actual_direction:
        raise RuntimeError(
            f"WES early-close re-entry authorization direction mismatch for "
            f"{item.get('instrument_id') or item.get('symbol') or 'unknown'}: "
            f"authorized={authorized_direction}, actual={actual_direction}"
        )


def apply_open_reentry_metadata(path: Optional[Path] = None):
    """Validate every open early-reentry authorization before granting rolling carry."""
    target = path or base.v2.current_week_path()
    week = base.v4.read(target, {})
    if week:
        now = base.v2.now_local()
        for item in week.get("instruments") or []:
            if not isinstance(item, dict):
                continue
            validate_open_reentry_authorization(item, now)
    return _BASE_APPLY_OPEN_REENTRY_METADATA(target)


# base.run_v5 resolves this global from the base module at runtime. Replacing it
# makes the validation unavoidable even when callers delegate to base.run_v5.
base.apply_open_reentry_metadata = apply_open_reentry_metadata


def run_v5(mode: str) -> None:
    base.run_v5(mode)


def __getattr__(name: str):
    """Keep this module API-compatible with investments_wes_lifecycle."""
    return getattr(base, name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["auto", "forecast", "close", "ensure-exposure", "render"],
        default="auto",
    )
    args = parser.parse_args()
    run_v5(args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
