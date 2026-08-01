#!/usr/bin/env python3
"""Resilient Daily Market Alert publisher with language-separated editorial QA.

The validated market-data layer remains authoritative. Polish and English copy
are generated and reviewed independently, merged into one payload, and blocked
unless the deterministic quality gate passes. AI/provider failure falls back to
structured deterministic copy built only from validated quotes and levels.
"""
from __future__ import annotations

import argparse
import copy
from typing import Any

import daily_market_alert_editorial_v2 as editorial_v2
import update_daily_market_alert as alert

_ORIGINAL_BUILD_ALERT = alert.build_alert
_ORIGINAL_VALIDATE_PAYLOAD = alert.validate_payload


def governed_generate_editorial(
    snapshots: list[alert.MarketSnapshot],
    candidates: list[dict[str, Any]],
    mode: str,
    previous: dict[str, Any],
) -> dict[str, Any]:
    return editorial_v2.generate_editorial(
        alert, snapshots, candidates, mode, previous
    )


def governed_build_alert(
    snapshots: list[alert.MarketSnapshot],
    candidates: list[dict[str, Any]],
    editorial: dict[str, Any],
    mode: str,
    moment: Any,
) -> dict[str, Any]:
    payload = _ORIGINAL_BUILD_ALERT(
        snapshots, candidates, editorial, mode, moment
    )
    return editorial_v2.enrich_payload(payload, editorial)


def governed_validate_payload(payload: dict[str, Any]) -> None:
    _ORIGINAL_VALIDATE_PAYLOAD(payload)
    editorial_v2.validate_published_payload(payload)


def install_governed_editorial_contract() -> None:
    alert.generate_editorial = governed_generate_editorial
    alert.build_alert = governed_build_alert
    alert.validate_payload = governed_validate_payload


def publish_fallback(mode_requested: str) -> int:
    moment = alert.now_utc()
    mode = alert.resolve_mode(mode_requested, moment)
    if mode == "skip":
        print("Outside the governed NYSE alert window or the market is closed; no update.")
        return 0

    previous = alert.load_json(alert.OUT, {})
    snapshots = [alert.fetch_snapshot(instrument_id) for instrument_id in alert.INSTRUMENTS]
    editorial = editorial_v2.deterministic_editorial(alert, snapshots, mode)
    candidate = governed_build_alert(snapshots, [], editorial, mode, moment)
    candidate["editorial_mode"] = "structured_deterministic_fallback"

    if mode == "open":
        candidate["opening_snapshot"] = alert.snapshot_from_alert(candidate)
        candidate.pop("_editorial_preclose_note", None)
        governed_validate_payload(candidate)
        alert.write_json(alert.OUT, candidate)
        alert.archive(candidate, "open")
        print(f"Published governed deterministic opening alert for {candidate['session_date']}")
        return 0

    same_session = previous.get("session_date") == candidate.get("session_date")
    opening = previous.get("opening_snapshot") if same_session else None
    checked_at = moment.astimezone(alert.WARSAW).isoformat(timespec="seconds")
    if not isinstance(opening, dict):
        candidate["opening_snapshot"] = alert.snapshot_from_alert(candidate)
        candidate["preclose_check"] = {
            "checked_at": checked_at,
            "material_change": True,
            "reasons": ["opening-alert-unavailable"],
            "note": {
                "pl": "Opublikowano pełny alert przed zamknięciem, ponieważ brakowało prawidłowego alertu po otwarciu tej sesji.",
                "en": "A full pre-close alert was published because a valid post-open alert for this session was unavailable.",
            },
        }
        candidate.pop("_editorial_preclose_note", None)
        governed_validate_payload(candidate)
        alert.write_json(alert.OUT, candidate)
        alert.archive(candidate, "preclose")
        print("Published governed deterministic pre-close alert without opening baseline")
        return 0

    reasons = alert.material_reasons(opening, candidate)
    if reasons:
        candidate["opening_snapshot"] = opening
        candidate["preclose_check"] = {
            "checked_at": checked_at,
            "material_change": True,
            "reasons": reasons,
            "note": alert.material_note(candidate),
        }
        governed_validate_payload(candidate)
        alert.write_json(alert.OUT, candidate)
        alert.archive(candidate, "preclose")
        print("Published governed deterministic material pre-close update:", ", ".join(reasons))
    else:
        output = copy.deepcopy(previous)
        output["preclose_check"] = {
            "checked_at": checked_at,
            "material_change": False,
            "reasons": [],
            "note": alert.no_change_note(moment),
        }
        output["editorial_mode"] = output.get(
            "editorial_mode", "language_separated_ai"
        )
        output.pop("_editorial_preclose_note", None)
        governed_validate_payload(output)
        alert.write_json(alert.OUT, output)
        alert.archive(output, "preclose-check")
        print("Recorded governed deterministic pre-close check with no material change")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("auto", "open", "preclose", "catchup"), default="auto"
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    install_governed_editorial_contract()
    if args.validate_only:
        governed_validate_payload(alert.load_json(alert.OUT, {}))
        print("Daily market alert JSON and editorial quality contract are valid")
        return 0
    try:
        return alert.run(args.mode)
    except Exception as exc:
        print(f"Primary language-separated alert generation failed: {exc}")
        print("Retrying with structured deterministic validated-market fallback.")
        return publish_fallback(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
