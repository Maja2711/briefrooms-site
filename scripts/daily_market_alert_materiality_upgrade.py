#!/usr/bin/env python3
"""One-time safe migration of a published alert to the materiality contract.

This does not change the validated market price, session return or publication
identity. It only replaces noise-sized technical levels and their scenario copy,
and records the editorial correction explicitly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import daily_market_alert_materiality as materiality


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _snapshot(item: dict[str, Any]) -> SimpleNamespace:
    current = _number(item.get("price_value"))
    support = _number(item.get("support_value"), current)
    resistance = _number(item.get("resistance_value"), current)
    next_support = _number(item.get("next_support_value"), support)
    next_resistance = _number(item.get("next_resistance_value"), resistance)
    # The original payload does not publish ATR. Use only observed distances to
    # estimate a conservative scale; absolute instrument floors still apply.
    atr_proxy = max(
        abs(current - support),
        abs(resistance - current),
        abs(current - next_support),
        abs(next_resistance - current),
    )
    return SimpleNamespace(
        instrument_id=str(item.get("id") or ""),
        name=str(item.get("name") or item.get("id") or ""),
        price=current,
        atr=atr_proxy,
        support=support,
        resistance=resistance,
        next_support=next_support,
        next_resistance=next_resistance,
        support_text=str(item.get("support") or ""),
        resistance_text=str(item.get("resistance") or ""),
        next_support_text=str(item.get("next_support") or ""),
        next_resistance_text=str(item.get("next_resistance") or ""),
        price_text=str(item.get("price") or ""),
        change_text=str(item.get("change") or ""),
        direction=str(item.get("direction") or "flat"),
    )


def _what_changed(item: dict[str, Any], snapshot: Any, lang: str) -> str:
    instrument_id = snapshot.instrument_id
    price = materiality._format(snapshot.price, instrument_id, lang)
    change = str(item.get("change") or "").replace(",", ".") if lang == "en" else str(item.get("change") or "")
    support = materiality._format(snapshot.support, instrument_id, lang)
    resistance = materiality._format(snapshot.resistance, instrument_id, lang)
    if lang == "pl":
        if instrument_id == "sp500":
            return f"S&P 500 jest na {price}, czyli {change} od poprzedniego zamknięcia, i pozostaje wewnątrz istotnego zakresu {support}–{resistance}."
        if instrument_id == "brent":
            return f"Brent kosztuje {price} i zmienia się o {change} od poprzedniego zamknięcia; istotny zakres na 1–3 sesje wynosi {support}–{resistance}."
        return f"Rentowność US 10Y wynosi {price}, zmieniając się o {change} od poprzedniego zamknięcia; istotny zakres wynosi {support}–{resistance}."
    if instrument_id == "sp500":
        return f"The S&P 500 is at {price}, {change} from the previous close, and remains inside the material {support}–{resistance} range."
    if instrument_id == "brent":
        return f"Brent is at {price}, {change} from the previous close; the material one-to-three-session range is {support}–{resistance}."
    return f"The US 10Y yield is {price}, {change} from the previous close; the material range is {support}–{resistance}."


def upgrade_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    snapshots = [_snapshot(item) for item in payload.get("instruments", [])]
    materiality.apply_materiality_levels(snapshots)
    by_id = {snapshot.instrument_id: snapshot for snapshot in snapshots}

    for item in payload.get("instruments", []):
        instrument_id = str(item.get("id") or "")
        snapshot = by_id[instrument_id]
        item["support_value"] = snapshot.support
        item["resistance_value"] = snapshot.resistance
        item["next_support_value"] = snapshot.next_support
        item["next_resistance_value"] = snapshot.next_resistance
        item["support"] = materiality._format(snapshot.support, instrument_id, "pl")
        item["resistance"] = materiality._format(snapshot.resistance, instrument_id, "pl")
        item["next_support"] = materiality._format(snapshot.next_support, instrument_id, "pl")
        item["next_resistance"] = materiality._format(snapshot.next_resistance, instrument_id, "pl")

        narrative = item.setdefault("narrative", {})
        for lang in ("pl", "en"):
            language_copy = narrative.setdefault(lang, {})
            language_copy["what_changed"] = _what_changed(item, snapshot, lang)
            language_copy["base_case"] = materiality._base_case(instrument_id, snapshot, lang)
        item["reason"] = {
            lang: " ".join(
                str(narrative[lang].get(field) or "").strip()
                for field in ("what_changed", "why_it_matters", "base_case")
            )
            for lang in ("pl", "en")
        }
        item["source_indexes"] = []

    materiality.enrich_payload(payload, snapshots)
    payload["editorial_revision"] = {
        "revised_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": "materiality_correction_without_market_data_change",
        "price_data_changed": False,
        "session_return_changed": False,
        "reason": {
            "pl": "Usunięto poziomy i scenariusze mieszczące się w zwykłym szumie rynkowym.",
            "en": "Levels and scenarios contained within ordinary market noise were removed.",
        },
    }
    return payload, snapshots


def editorial_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_regime": payload.get("market_regime") or {},
        "summary": payload.get("summary") or {},
        "preclose_note": {"pl": "", "en": ""},
        "instruments": [
            {
                "id": item.get("id"),
                "narrative": item.get("narrative") or {},
                "reason": item.get("reason") or {},
                "stance": item.get("stance") or "mixed",
                "driver_keys": item.get("driver_keys") or ["price-action"],
                "source_indexes": [],
                "probabilities": item.get("scenario_probabilities") or {"range": 50, "continuation": 30, "reversal": 20},
            }
            for item in payload.get("instruments", [])
        ],
    }
