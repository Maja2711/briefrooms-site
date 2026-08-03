#!/usr/bin/env python3
"""Instrument-specific materiality layer for the Daily Market Alert.

The alert may show nearby market structure, but it must not turn tiny moves into
pseudo-forecasts. This module promotes support/resistance and follow-through
levels to distances that are meaningful over a 1-3 session horizon. It is
fully deterministic, runs before editorial generation, and validates the final
public payload. It never changes prices or invents a market event.
"""
from __future__ import annotations

import math
from typing import Any

VERSION = "1.0.0"

POLICY: dict[str, dict[str, float]] = {
    # Absolute floors are combined with price- and ATR-based floors. Values are
    # deliberately wider than ordinary intraday noise for each asset class.
    "sp500": {
        "minimum_trigger_absolute": 25.0,
        "minimum_trigger_price_fraction": 0.0030,
        "minimum_trigger_atr_fraction": 0.45,
        "minimum_target_absolute": 35.0,
        "minimum_target_price_fraction": 0.0045,
        "minimum_target_atr_fraction": 0.65,
        "rounding_step": 5.0,
    },
    "brent": {
        "minimum_trigger_absolute": 0.75,
        "minimum_trigger_price_fraction": 0.0100,
        "minimum_trigger_atr_fraction": 0.45,
        "minimum_target_absolute": 1.25,
        "minimum_target_price_fraction": 0.0150,
        "minimum_target_atr_fraction": 0.70,
        "rounding_step": 0.50,
    },
    "us10y": {
        # Yield values are percentage points: 0.05 = 5 bp, 0.08 = 8 bp.
        "minimum_trigger_absolute": 0.05,
        "minimum_trigger_price_fraction": 0.0,
        "minimum_trigger_atr_fraction": 0.45,
        "minimum_target_absolute": 0.08,
        "minimum_target_price_fraction": 0.0,
        "minimum_target_atr_fraction": 0.70,
        "rounding_step": 0.01,
    },
}


class MaterialityError(ValueError):
    pass


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _thresholds(instrument_id: str, current: float, atr: float) -> tuple[float, float]:
    cfg = POLICY[instrument_id]
    trigger = max(
        cfg["minimum_trigger_absolute"],
        abs(current) * cfg["minimum_trigger_price_fraction"],
        abs(atr) * cfg["minimum_trigger_atr_fraction"],
    )
    target = max(
        cfg["minimum_target_absolute"],
        abs(current) * cfg["minimum_target_price_fraction"],
        abs(atr) * cfg["minimum_target_atr_fraction"],
    )
    return trigger, target


def _round_outward(value: float, step: float, direction: str) -> float:
    scaled = value / step
    rounded = math.floor(scaled + 1e-10) * step if direction == "down" else math.ceil(scaled - 1e-10) * step
    return round(rounded, 8)


def _format(value: float, instrument_id: str, lang: str) -> str:
    if instrument_id == "sp500":
        text = f"{value:,.0f}".replace(",", " ")
    elif instrument_id == "brent":
        text = f"{value:.2f} USD"
    else:
        text = f"{value:.2f}%"
    return text.replace(".", ",") if lang == "pl" else text


def _choose_primary(
    current: float,
    candidates: list[float],
    minimum_distance: float,
    direction: str,
    step: float,
) -> float:
    if direction == "down":
        valid = sorted((x for x in candidates if x <= current - minimum_distance), reverse=True)
        raw = valid[0] if valid else current - minimum_distance
    else:
        valid = sorted(x for x in candidates if x >= current + minimum_distance)
        raw = valid[0] if valid else current + minimum_distance
    value = _round_outward(raw, step, direction)
    while (current - value if direction == "down" else value - current) + 1e-9 < minimum_distance:
        value = round(value - step if direction == "down" else value + step, 8)
    return value


def _choose_target(
    trigger: float,
    candidates: list[float],
    minimum_extension: float,
    direction: str,
    step: float,
) -> float:
    if direction == "down":
        valid = sorted((x for x in candidates if x <= trigger - minimum_extension), reverse=True)
        raw = valid[0] if valid else trigger - minimum_extension
    else:
        valid = sorted(x for x in candidates if x >= trigger + minimum_extension)
        raw = valid[0] if valid else trigger + minimum_extension
    value = _round_outward(raw, step, direction)
    while (trigger - value if direction == "down" else value - trigger) + 1e-9 < minimum_extension:
        value = round(value - step if direction == "down" else value + step, 8)
    return value


def apply_materiality_levels(snapshots: list[Any]) -> list[Any]:
    """Mutate validated snapshots so displayed levels exclude trivial noise."""
    for snapshot in snapshots:
        instrument_id = str(getattr(snapshot, "instrument_id", ""))
        if instrument_id not in POLICY:
            raise MaterialityError(f"Unsupported alert instrument: {instrument_id}")
        current = _number(getattr(snapshot, "price", None))
        if current is None:
            raise MaterialityError(f"Missing current price for {instrument_id}")
        atr = _number(getattr(snapshot, "atr", None)) or 0.0
        trigger_floor, target_floor = _thresholds(instrument_id, current, atr)
        step = POLICY[instrument_id]["rounding_step"]

        old_support = _number(getattr(snapshot, "support", None))
        old_resistance = _number(getattr(snapshot, "resistance", None))
        old_next_support = _number(getattr(snapshot, "next_support", None))
        old_next_resistance = _number(getattr(snapshot, "next_resistance", None))
        lower_candidates = [x for x in (old_support, old_next_support) if x is not None]
        upper_candidates = [x for x in (old_resistance, old_next_resistance) if x is not None]

        support = _choose_primary(current, lower_candidates, trigger_floor, "down", step)
        resistance = _choose_primary(current, upper_candidates, trigger_floor, "up", step)
        next_support = _choose_target(support, lower_candidates, target_floor, "down", step)
        next_resistance = _choose_target(resistance, upper_candidates, target_floor, "up", step)

        snapshot.support = support
        snapshot.resistance = resistance
        snapshot.next_support = next_support
        snapshot.next_resistance = next_resistance
        snapshot.support_text = _format(support, instrument_id, "pl")
        snapshot.resistance_text = _format(resistance, instrument_id, "pl")
        snapshot.next_support_text = _format(next_support, instrument_id, "pl")
        snapshot.next_resistance_text = _format(next_resistance, instrument_id, "pl")
        snapshot.materiality = {
            "version": VERSION,
            "classification": "one_to_three_session_decision_levels",
            "minimum_trigger_distance": round(trigger_floor, 8),
            "minimum_target_extension": round(target_floor, 8),
            "noise_band_low": support,
            "noise_band_high": resistance,
            "downside_target": next_support,
            "upside_target": next_resistance,
        }
    return snapshots


def _base_case(instrument_id: str, snapshot: Any, lang: str) -> str:
    s = _format(float(snapshot.support), instrument_id, lang)
    r = _format(float(snapshot.resistance), instrument_id, lang)
    ns = _format(float(snapshot.next_support), instrument_id, lang)
    nr = _format(float(snapshot.next_resistance), instrument_id, lang)
    if lang == "pl":
        if instrument_id == "sp500":
            return (
                f"Ruch S&P 500 pomiędzy {s} a {r} traktujemy jako konsolidację, a nie sygnał kierunkowy. "
                f"Dopiero zamknięcie ponad {r} potwierdzi ruch większy niż zwykły szum w stronę {nr}; "
                f"zamknięcie poniżej {s} zwiększy ryzyko zejścia do {ns}."
            )
        if instrument_id == "brent":
            return (
                f"Wahania Brent między {s} a {r} pozostają zwykłym zakresem i nie uzasadniają prognozy kierunkowej. "
                f"Dopiero zamknięcie ponad {r} otworzy istotną przestrzeń do {nr}; zejście poniżej {s} "
                f"potwierdzi zmianę obrazu z kolejnym poziomem {ns}."
            )
        return (
            f"Zmiany US 10Y wewnątrz {s}–{r} traktujemy jako normalny szum rynku obligacji, a nie prognozę. "
            f"Dopiero zamknięcie ponad {r} nada znaczenie ruchowi w stronę {nr}; zamknięcie poniżej {s} "
            f"będzie istotnym sygnałem spadku rentowności z kolejnym poziomem {ns}."
        )
    if instrument_id == "sp500":
        return (
            f"An S&P 500 move between {s} and {r} is consolidation, not a directional signal. "
            f"Only a close above {r} would confirm a move beyond ordinary noise toward {nr}; "
            f"a close below {s} would raise downside risk toward {ns}."
        )
    if instrument_id == "brent":
        return (
            f"Brent fluctuations between {s} and {r} remain ordinary range trading and do not justify a directional forecast. "
            f"Only a close above {r} would open material room toward {nr}; a break below {s} "
            f"would confirm a changed setup with {ns} as the next level."
        )
    return (
        f"US 10Y moves inside {s}–{r} are treated as normal bond-market noise, not a forecast. "
        f"Only a close above {r} would make a move toward {nr} material; a close below {s} "
        f"would be a meaningful yield-down signal with {ns} as the next level."
    )


def rewrite_editorial(editorial: dict[str, Any], snapshots: list[Any]) -> dict[str, Any]:
    """Replace only the scenario field with deterministic materiality-aware copy."""
    by_id = {str(x.instrument_id): x for x in snapshots}
    for row in editorial.get("instruments", []):
        instrument_id = str(row.get("id") or "")
        snapshot = by_id.get(instrument_id)
        if snapshot is None:
            continue
        narrative = row.setdefault("narrative", {})
        for lang in ("pl", "en"):
            language_copy = narrative.setdefault(lang, {})
            language_copy["base_case"] = _base_case(instrument_id, snapshot, lang)
        row["reason"] = {
            lang: " ".join(str(narrative[lang].get(field) or "").strip() for field in ("what_changed", "why_it_matters", "base_case"))
            for lang in ("pl", "en")
        }
    return editorial


def enrich_payload(payload: dict[str, Any], snapshots: list[Any]) -> dict[str, Any]:
    by_id = {str(x.instrument_id): x for x in snapshots}
    contract_rows: dict[str, Any] = {}
    for item in payload.get("instruments", []):
        instrument_id = str(item.get("id") or "")
        snapshot = by_id.get(instrument_id)
        if snapshot is None:
            continue
        materiality = dict(getattr(snapshot, "materiality", {}) or {})
        item["materiality"] = materiality
        item["trigger"] = {
            "pl": _base_case(instrument_id, snapshot, "pl"),
            "en": _base_case(instrument_id, snapshot, "en"),
        }
        probabilities = item.get("scenario_probabilities") or {}
        item["scenarios"] = [
            {
                "probability": int(probabilities.get("range", 0)),
                "label": {
                    "pl": f"Brak sygnału kierunkowego wewnątrz {_format(snapshot.support, instrument_id, 'pl')}–{_format(snapshot.resistance, instrument_id, 'pl')}",
                    "en": f"No directional signal inside {_format(snapshot.support, instrument_id, 'en')}–{_format(snapshot.resistance, instrument_id, 'en')}",
                },
            },
            {
                "probability": int(probabilities.get("continuation", 0)),
                "label": {
                    "pl": f"Potwierdzone wybicie ponad {_format(snapshot.resistance, instrument_id, 'pl')} w stronę {_format(snapshot.next_resistance, instrument_id, 'pl')}",
                    "en": f"Confirmed break above {_format(snapshot.resistance, instrument_id, 'en')} toward {_format(snapshot.next_resistance, instrument_id, 'en')}",
                },
            },
            {
                "probability": int(probabilities.get("reversal", 0)),
                "label": {
                    "pl": f"Potwierdzone zejście poniżej {_format(snapshot.support, instrument_id, 'pl')} w stronę {_format(snapshot.next_support, instrument_id, 'pl')}",
                    "en": f"Confirmed break below {_format(snapshot.support, instrument_id, 'en')} toward {_format(snapshot.next_support, instrument_id, 'en')}",
                },
            },
        ]
        contract_rows[instrument_id] = materiality
    payload["materiality_contract"] = {
        "version": VERSION,
        "horizon": "one_to_three_sessions",
        "principle": "Moves inside the promoted support-resistance band are noise/range, not directional forecasts.",
        "instruments": contract_rows,
    }
    return payload


def validate_payload(payload: dict[str, Any]) -> None:
    contract = payload.get("materiality_contract") or {}
    if contract.get("version") != VERSION:
        raise MaterialityError("Missing current Daily Market Alert materiality contract")
    contract_rows = contract.get("instruments") or {}
    for item in payload.get("instruments", []):
        instrument_id = str(item.get("id") or "")
        if instrument_id not in POLICY:
            raise MaterialityError(f"Unexpected instrument in alert: {instrument_id}")
        row = contract_rows.get(instrument_id) or {}
        current = _number(item.get("price_value"))
        support = _number(item.get("support_value"))
        resistance = _number(item.get("resistance_value"))
        next_support = _number(item.get("next_support_value"))
        next_resistance = _number(item.get("next_resistance_value"))
        trigger_floor = _number(row.get("minimum_trigger_distance"))
        target_floor = _number(row.get("minimum_target_extension"))
        values = (current, support, resistance, next_support, next_resistance, trigger_floor, target_floor)
        if any(value is None for value in values):
            raise MaterialityError(f"Incomplete materiality values for {instrument_id}")
        tolerance = POLICY[instrument_id]["rounding_step"] * 0.02
        if current - support + tolerance < trigger_floor or resistance - current + tolerance < trigger_floor:
            raise MaterialityError(f"Noise-level trigger published for {instrument_id}")
        if support - next_support + tolerance < target_floor or next_resistance - resistance + tolerance < target_floor:
            raise MaterialityError(f"Trivial follow-through target published for {instrument_id}")
        for lang, marker in (("pl", ("szum", "nie uzasadniają prognozy", "nie sygnał")), ("en", ("noise", "do not justify", "not a directional"))):
            base_case = str((((item.get("narrative") or {}).get(lang) or {}).get("base_case") or "")).lower()
            if not any(value in base_case for value in marker):
                raise MaterialityError(f"Missing noise/materiality explanation for {lang}:{instrument_id}")
