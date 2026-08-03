#!/usr/bin/env python3
"""Resilient Daily Market Alert publisher with editorial and materiality QA.

The validated market-data layer remains authoritative. Polish and English copy
are generated and reviewed independently, merged into one payload, and blocked
unless both the editorial and instrument-specific materiality gates pass.
AI/provider failure falls back to structured deterministic copy built only from
validated quotes and promoted 1-3 session decision levels.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta
from typing import Any

import daily_market_alert_editorial_v2 as editorial_v2
import daily_market_alert_materiality as materiality
import daily_market_alert_materiality_upgrade as materiality_upgrade
import update_daily_market_alert as alert

_ORIGINAL_BUILD_ALERT = alert.build_alert
_ORIGINAL_VALIDATE_PAYLOAD = alert.validate_payload


def prepare_snapshots(snapshots: list[alert.MarketSnapshot]) -> list[alert.MarketSnapshot]:
    """Promote raw nearby levels to non-trivial decision levels."""
    return materiality.apply_materiality_levels(snapshots)


def finalize_editorial(
    editorial: dict[str, Any],
    snapshots: list[alert.MarketSnapshot],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    materiality.rewrite_editorial(editorial, snapshots)
    quality = editorial_v2.report(
        editorial, snapshots, candidates, editorial_v2.load_spec()
    )
    if not quality["passed"]:
        raise editorial_v2.EditorialQualityError(
            "Materiality-aware editorial blocked: " + ", ".join(quality["issues"])
        )
    editorial["quality_report"] = quality
    return editorial


def governed_generate_editorial(
    snapshots: list[alert.MarketSnapshot],
    candidates: list[dict[str, Any]],
    mode: str,
    previous: dict[str, Any],
) -> dict[str, Any]:
    prepare_snapshots(snapshots)
    generated = editorial_v2.generate_editorial(
        alert, snapshots, candidates, mode, previous
    )
    return finalize_editorial(generated, snapshots, candidates)


def governed_build_alert(
    snapshots: list[alert.MarketSnapshot],
    candidates: list[dict[str, Any]],
    editorial: dict[str, Any],
    mode: str,
    moment: Any,
) -> dict[str, Any]:
    prepare_snapshots(snapshots)
    payload = _ORIGINAL_BUILD_ALERT(
        snapshots, candidates, editorial, mode, moment
    )
    payload = editorial_v2.enrich_payload(payload, editorial)
    return materiality.enrich_payload(payload, snapshots)


def governed_validate_payload(payload: dict[str, Any]) -> None:
    _ORIGINAL_VALIDATE_PAYLOAD(payload)
    editorial_v2.validate_published_payload(payload)
    materiality.validate_payload(payload)


def install_governed_editorial_contract() -> None:
    alert.generate_editorial = governed_generate_editorial
    alert.build_alert = governed_build_alert
    alert.validate_payload = governed_validate_payload


def upgrade_existing_alert_if_needed(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate an older public alert without reusing obsolete narrative rules."""
    contract = payload.get("materiality_contract") or {}
    editorial_contract = payload.get("editorial_contract") or {}
    quality = payload.get("editorial_quality") or {}
    if (
        contract.get("version") == materiality.VERSION
        and editorial_contract.get("pl_generated_independently")
        and editorial_contract.get("en_generated_independently")
        and quality.get("passed")
    ):
        return payload

    upgraded, snapshots = materiality_upgrade.upgrade_payload(payload)
    mode = str(upgraded.get("edition") or "open")
    if mode not in {"open", "preclose"}:
        mode = "open"
    editorial = editorial_v2.deterministic_editorial(alert, snapshots, mode)
    editorial = finalize_editorial(editorial, snapshots, [])

    by_id = {row["id"]: row for row in editorial.get("instruments", [])}
    for item in upgraded.get("instruments", []):
        row = by_id.get(item.get("id"), {})
        item["narrative"] = row.get("narrative", {})
        item["reason"] = row.get("reason", {})
        item["stance"] = row.get("stance")
        item["driver_keys"] = row.get("driver_keys", [])
        item["source_indexes"] = []

    upgraded = editorial_v2.enrich_payload(upgraded, editorial)
    upgraded = materiality.enrich_payload(upgraded, snapshots)
    upgraded["editorial_mode"] = "deterministic_materiality_migration"
    governed_validate_payload(upgraded)
    alert.write_json(alert.OUT, upgraded)
    alert.archive(upgraded, "materiality-correction")
    print("Migrated the last published alert to the current editorial and materiality contracts")
    return upgraded


def publish_fallback(mode_requested: str) -> int:
    moment = alert.now_utc()
    mode = alert.resolve_mode(mode_requested, moment)
    if mode == "skip":
        existing = alert.load_json(alert.OUT, {})
        if existing:
            upgrade_existing_alert_if_needed(existing)
        print("Outside the governed NYSE alert window or the market is closed; no market update.")
        return 0

    previous = alert.load_json(alert.OUT, {})
    snapshots = [alert.fetch_snapshot(instrument_id) for instrument_id in alert.INSTRUMENTS]
    prepare_snapshots(snapshots)
    editorial = editorial_v2.deterministic_editorial(alert, snapshots, mode)
    editorial = finalize_editorial(editorial, snapshots, [])
    candidate = governed_build_alert(snapshots, [], editorial, mode, moment)
    candidate["editorial_mode"] = "structured_deterministic_materiality_fallback"

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
        try:
            governed_validate_payload(output)
        except Exception:
            candidate["opening_snapshot"] = opening
            candidate["preclose_check"] = {
                "checked_at": checked_at,
                "material_change": True,
                "reasons": ["materiality-contract-upgrade"],
                "note": {
                    "pl": "Alert odświeżono, ponieważ poprzednia wersja nie spełniała aktualnego filtra istotności poziomów.",
                    "en": "The alert was refreshed because the previous edition did not meet the current level-materiality filter.",
                },
            }
            candidate.pop("_editorial_preclose_note", None)
            governed_validate_payload(candidate)
            alert.write_json(alert.OUT, candidate)
            alert.archive(candidate, "preclose")
            print("Published materiality-contract upgrade")
            return 0
        alert.write_json(alert.OUT, output)
        alert.archive(output, "preclose-check")
        print("Recorded governed deterministic pre-close check with no material change")
    return 0


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=alert.UTC)


def _fresh_same_session_open(previous: dict[str, Any], moment: datetime) -> bool:
    if previous.get("session_date") != moment.astimezone(alert.NY).date().isoformat():
        return False
    if previous.get("edition") != "open":
        return False
    updated = _parse_time(previous.get("updated_at"))
    if updated is None:
        return False
    return moment - updated.astimezone(alert.UTC) < timedelta(minutes=45)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("auto", "open", "preclose", "catchup"), default="auto"
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    install_governed_editorial_contract()

    if args.validate_only:
        payload = upgrade_existing_alert_if_needed(alert.load_json(alert.OUT, {}))
        governed_validate_payload(payload)
        print("Daily market alert JSON, editorial and materiality contracts are valid")
        return 0

    moment = alert.now_utc()
    resolved = alert.resolve_mode(args.mode, moment)
    previous = alert.load_json(alert.OUT, {})
    if resolved == "skip":
        if previous:
            upgrade_existing_alert_if_needed(previous)
        print("Outside the governed NYSE alert window or the market is closed; no market update.")
        return 0
    if args.mode == "catchup" and resolved == "open" and _fresh_same_session_open(previous, moment):
        governed_validate_payload(previous)
        print("A current same-session opening alert already exists; duplicate catch-up skipped.")
        return 0

    try:
        return alert.run(args.mode)
    except Exception as primary_exc:
        print(f"Primary language-separated alert generation failed: {primary_exc}")
        print("Retrying with structured deterministic validated-market fallback.")
        try:
            return publish_fallback(args.mode)
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Daily Market Alert failed in both primary and deterministic paths. "
                f"Primary: {primary_exc}; fallback: {fallback_exc}"
            ) from fallback_exc


if __name__ == "__main__":
    raise SystemExit(main())
