#!/usr/bin/env python3
"""Resilient Daily Market Alert publisher.

Market data publication must not stop when the optional AI editorial layer is
unavailable. The primary generator is attempted first. On editorial/provider
failure this runner publishes a strictly deterministic bilingual explanation
based only on the validated market snapshots.
"""
from __future__ import annotations

import argparse
import copy
from typing import Any

import update_daily_market_alert as alert


def deterministic_editorial(snapshots: list[alert.MarketSnapshot], mode: str) -> dict[str, Any]:
    instruments = []
    for snapshot in snapshots:
        direction_pl = "wzrosła" if snapshot.direction == "up" else "spadła" if snapshot.direction == "down" else "pozostała stabilna"
        direction_en = "rose" if snapshot.direction == "up" else "fell" if snapshot.direction == "down" else "was broadly unchanged"
        if snapshot.instrument_id == "sp500":
            reason_pl = (
                f"Wartość S&P 500 {direction_pl} względem poprzedniego regularnego zamknięcia. "
                "Brak potwierdzonego pojedynczego nowego katalizatora; krótkoterminowy obraz wyznaczają bieżąca zmiana indeksu oraz wskazane poziomy techniczne."
            )
            reason_en = (
                f"The S&P 500 {direction_en} versus the previous regular-session close. "
                "No single new catalyst is confirmed; the short-term picture is defined by the current index move and the stated technical levels."
            )
            drivers = ["market-price-action"]
        elif snapshot.instrument_id == "brent":
            reason_pl = (
                f"Cena ropy Brent {direction_pl} względem poprzedniego regularnego zamknięcia. "
                "Brak potwierdzonego pojedynczego nowego impulsu; dla scenariusza kluczowe pozostają bieżąca zmiana ceny oraz najbliższe wsparcie i opór."
            )
            reason_en = (
                f"Brent crude {direction_en} versus the previous regular-session close. "
                "No single new driver is confirmed; the current price move and the nearest support and resistance remain decisive for the scenario."
            )
            drivers = ["oil-price-action"]
        else:
            reason_pl = (
                f"Rentowność amerykańskich obligacji 10-letnich {direction_pl} względem poprzedniego regularnego zamknięcia. "
                "Bez potwierdzonego jednego nowego katalizatora ocena opiera się na zmianie rentowności i najbliższych poziomach technicznych."
            )
            reason_en = (
                f"The US 10-year Treasury yield {direction_en} versus the previous regular-session close. "
                "With no single new catalyst confirmed, the assessment is based on the yield move and the nearest technical levels."
            )
            drivers = ["rates-price-action"]

        if snapshot.direction == "flat":
            probabilities = {"range": 60, "continuation": 20, "reversal": 20}
        else:
            probabilities = {"range": 50, "continuation": 30, "reversal": 20}
        instruments.append(
            {
                "id": snapshot.instrument_id,
                "reason": {"pl": reason_pl, "en": reason_en},
                "driver_keys": drivers,
                "source_indexes": [],
                "probabilities": probabilities,
            }
        )

    return {
        "market_regime": {
            "pl": "Bieżący obraz międzyrynkowy oparty na zweryfikowanych kwotowaniach",
            "en": "Current cross-asset picture based on validated market quotes",
        },
        "summary": alert.deterministic_summary(snapshots),
        "instruments": instruments,
        "preclose_note": {
            "pl": "Aktualizacja przed zamknięciem została przygotowana na podstawie zweryfikowanych kwotowań.",
            "en": "The pre-close update was prepared from validated market quotes.",
        } if mode == "preclose" else {"pl": "", "en": ""},
    }


def publish_fallback(mode_requested: str) -> int:
    moment = alert.now_utc()
    mode = alert.resolve_mode(mode_requested, moment)
    if mode == "skip":
        print("Outside the governed NYSE alert window or the market is closed; no update.")
        return 0

    previous = alert.load_json(alert.OUT, {})
    snapshots = [alert.fetch_snapshot(instrument_id) for instrument_id in alert.INSTRUMENTS]
    editorial = deterministic_editorial(snapshots, mode)
    candidate = alert.build_alert(snapshots, [], editorial, mode, moment)
    candidate["editorial_mode"] = "deterministic_fallback"

    if mode == "open":
        candidate["opening_snapshot"] = alert.snapshot_from_alert(candidate)
        candidate.pop("_editorial_preclose_note", None)
        alert.validate_payload(candidate)
        alert.write_json(alert.OUT, candidate)
        alert.archive(candidate, "open")
        print(f"Published deterministic opening alert for {candidate['session_date']}")
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
        alert.validate_payload(candidate)
        alert.write_json(alert.OUT, candidate)
        alert.archive(candidate, "preclose")
        print("Published deterministic pre-close alert without opening baseline")
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
        alert.validate_payload(candidate)
        alert.write_json(alert.OUT, candidate)
        alert.archive(candidate, "preclose")
        print("Published deterministic material pre-close update:", ", ".join(reasons))
    else:
        output = copy.deepcopy(previous)
        output["preclose_check"] = {
            "checked_at": checked_at,
            "material_change": False,
            "reasons": [],
            "note": alert.no_change_note(moment),
        }
        output["editorial_mode"] = output.get("editorial_mode", "primary_ai")
        output.pop("_editorial_preclose_note", None)
        alert.validate_payload(output)
        alert.write_json(alert.OUT, output)
        alert.archive(output, "preclose-check")
        print("Recorded deterministic pre-close check with no material change")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "open", "preclose", "catchup"), default="auto")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        alert.validate_payload(alert.load_json(alert.OUT, {}))
        print("Daily market alert JSON is valid")
        return 0
    try:
        return alert.run(args.mode)
    except Exception as exc:
        print(f"Primary AI alert generation failed: {exc}")
        print("Retrying with deterministic validated-market fallback.")
        return publish_fallback(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
