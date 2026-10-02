from __future__ import annotations

"""Prospective institution/indicator forecast-skill ledger for EUR/USD macro events.

The ledger stores only derived forecast-error statistics and opaque settlement
IDs. Raw licensed forecast bundles remain outside the repository/runtime state
owned by the expectations collector.
"""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from belief_core import iso_z

SCHEMA_VERSION = "briefrooms-macro-forecaster-skill-v1"
MIN_INTERNAL_OBSERVATIONS = 3
MAX_SETTLED_IDS = 5000


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "skills": {},
        "settled_ids": [],
    }


def load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
        return empty_state()
    state = dict(payload)
    if not isinstance(state.get("skills"), Mapping):
        state["skills"] = {}
    if not isinstance(state.get("settled_ids"), list):
        state["settled_ids"] = []
    return state


def _key(institution: str, indicator: str) -> str:
    return f"{institution.strip().casefold()}|{indicator.strip().casefold()}"


def _settlement_id(
    *,
    provider: str,
    institution: str,
    indicator: str,
    period: str,
    event_at: str,
    published_at: str,
    forecast: float,
) -> str:
    raw = "|".join([
        provider.strip().casefold(),
        institution.strip().casefold(),
        indicator.strip().casefold(),
        period.strip(),
        event_at.strip(),
        published_at.strip(),
        f"{forecast:.12g}",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def skill_for(
    state: Mapping[str, Any] | None,
    *,
    institution: str,
    indicator: str,
) -> dict[str, Any] | None:
    if not isinstance(state, Mapping):
        return None
    skills = state.get("skills")
    if not isinstance(skills, Mapping):
        return None
    row = skills.get(_key(institution, indicator))
    if not isinstance(row, Mapping):
        return None
    return dict(row)


def internal_historical_mae(
    state: Mapping[str, Any] | None,
    *,
    institution: str,
    indicator: str,
) -> tuple[float, int] | None:
    row = skill_for(state, institution=institution, indicator=indicator)
    if row is None:
        return None
    try:
        count = int(row.get("settled_count") or 0)
        mae = float(row.get("mae"))
    except (TypeError, ValueError):
        return None
    if count < MIN_INTERNAL_OBSERVATIONS or not math.isfinite(mae) or mae <= 0:
        return None
    return mae, count


def _actual_map(context: Mapping[str, Any]) -> dict[str, float]:
    actuals: dict[str, float] = {}
    for row in context.get("actuals") or []:
        if not isinstance(row, Mapping):
            continue
        metric = str(row.get("comparable_metric") or row.get("metric") or "").strip()
        value = row.get("comparable_value")
        if value is None:
            value = row.get("value")
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if metric and math.isfinite(number):
            actuals[metric] = number
    return actuals


def settle(
    state: Mapping[str, Any] | None,
    expectations: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any], int]:
    out = empty_state()
    if isinstance(state, Mapping) and state.get("schema_version") == SCHEMA_VERSION:
        out.update({
            "schema_version": SCHEMA_VERSION,
            "updated_at": state.get("updated_at"),
            "skills": dict(state.get("skills") or {}),
            "settled_ids": list(state.get("settled_ids") or []),
        })

    if str(context.get("status") or "").upper() != "POST_RELEASE":
        return out, 0
    event = context.get("event") if isinstance(context.get("event"), Mapping) else {}
    event_at = _dt(event.get("event_at"))
    if event_at is None:
        return out, 0
    actuals = _actual_map(context)
    if not actuals:
        return out, 0

    provider = str(expectations.get("provider") or "").strip()
    settled_list = [str(x) for x in out.get("settled_ids") or []]
    settled = set(settled_list)
    changed = 0

    for release in expectations.get("releases") or []:
        if not isinstance(release, Mapping):
            continue
        indicator = str(release.get("indicator") or "").strip()
        period = str(release.get("period") or "").strip()
        release_at = _dt(release.get("event_at"))
        if not indicator or indicator not in actuals or release_at is None:
            continue
        if abs((release_at - event_at).total_seconds()) > 2 * 3600:
            continue

        actual = float(actuals[indicator])
        for forecast_row in release.get("forecasts") or []:
            if not isinstance(forecast_row, Mapping):
                continue
            institution = str(forecast_row.get("institution") or "").strip()
            published_at = str(forecast_row.get("published_at") or "").strip()
            try:
                forecast = float(forecast_row.get("forecast"))
            except (TypeError, ValueError):
                continue
            if not institution or not published_at or not math.isfinite(forecast):
                continue

            sid = _settlement_id(
                provider=provider,
                institution=institution,
                indicator=indicator,
                period=period,
                event_at=iso_z(release_at),
                published_at=published_at,
                forecast=forecast,
            )
            if sid in settled:
                continue

            error = forecast - actual
            abs_error = abs(error)
            sq_error = error * error
            key = _key(institution, indicator)
            prior = out["skills"].get(key)
            row = dict(prior) if isinstance(prior, Mapping) else {}
            count = int(row.get("settled_count") or 0) + 1
            sum_abs = float(row.get("sum_abs_error") or 0.0) + abs_error
            sum_sq = float(row.get("sum_sq_error") or 0.0) + sq_error
            sum_err = float(row.get("sum_error") or 0.0) + error
            prior_ewma = row.get("ewma_abs_error")
            ewma = abs_error if prior_ewma is None else 0.25 * abs_error + 0.75 * float(prior_ewma)

            out["skills"][key] = {
                "institution": institution,
                "indicator": indicator,
                "settled_count": count,
                "sum_abs_error": round(sum_abs, 10),
                "sum_sq_error": round(sum_sq, 10),
                "sum_error": round(sum_err, 10),
                "mae": round(sum_abs / count, 10),
                "rmse": round(math.sqrt(sum_sq / count), 10),
                "bias": round(sum_err / count, 10),
                "ewma_abs_error": round(ewma, 10),
                "last_error": round(error, 10),
                "last_actual": round(actual, 10),
                "last_period": period,
                "last_settled_at": iso_z(now),
                "weight_ready": count >= MIN_INTERNAL_OBSERVATIONS,
                "minimum_internal_observations": MIN_INTERNAL_OBSERVATIONS,
            }
            settled.add(sid)
            settled_list.append(sid)
            changed += 1

    out["settled_ids"] = settled_list[-MAX_SETTLED_IDS:]
    if changed:
        out["updated_at"] = iso_z(now)
    return out, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--expectations", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    state_path = Path(args.state)
    expectations_path = Path(args.expectations)
    context_path = Path(args.context)
    now = _dt(args.now) if args.now else datetime.now(timezone.utc)
    assert now is not None

    state = load_state(state_path)
    try:
        expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        expectations = {}
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        context = {}

    updated, changed = settle(state, expectations, context, now=now)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "changed_forecasts": changed,
        "skill_count": len(updated.get("skills") or {}),
        "state": str(state_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
