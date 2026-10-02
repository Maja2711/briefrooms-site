from __future__ import annotations

"""Fetch an explicitly configured, sourced macro-expectations bundle.

The collector is intentionally provider-agnostic. It does not scrape search
results and does not ask an LLM to invent or discover forecasts. A production
feed must provide institution-level forecasts with source references and
publication timestamps. Missing or invalid feeds fail closed to an empty bundle.
"""

import argparse
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from belief_core import iso_z
from belief_macro_forecaster_skill import internal_historical_mae, load_state

EXPECTATIONS_URL_ENV = "BELIEF_MACRO_EXPECTATIONS_URL"
EXPECTATIONS_TOKEN_ENV = "BELIEF_MACRO_EXPECTATIONS_TOKEN"
USER_AGENT = "BriefRooms-MacroExpectations/1.0 research https://briefrooms.com"


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


def _http_url(value: Any) -> str | None:
    text = str(value or "").strip()
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def normalize_payload(
    payload: Mapping[str, Any],
    *,
    now: datetime,
    skill_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider = str(payload.get("provider") or "").strip()
    if not provider:
        raise ValueError("macro expectations feed is missing provider")

    releases: list[dict[str, Any]] = []
    for raw_release in payload.get("releases") or []:
        if not isinstance(raw_release, Mapping):
            continue
        region = str(raw_release.get("region") or "").strip().upper()
        indicator = str(raw_release.get("indicator") or "").strip()
        period = str(raw_release.get("period") or "").strip()
        event_at = _dt(raw_release.get("event_at"))
        if region not in {"US", "EU"} or not indicator or not period or event_at is None:
            continue

        forecasts: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw_forecast in raw_release.get("forecasts") or []:
            if not isinstance(raw_forecast, Mapping):
                continue
            institution = str(raw_forecast.get("institution") or "").strip()
            published_at = _dt(raw_forecast.get("published_at"))
            source_ref = _http_url(raw_forecast.get("source_ref"))
            try:
                forecast = float(raw_forecast.get("forecast"))
            except (TypeError, ValueError):
                continue
            if not institution or published_at is None or source_ref is None or not math.isfinite(forecast):
                continue
            if published_at > now.astimezone(timezone.utc):
                continue
            key = (institution.casefold(), source_ref)
            if key in seen:
                continue
            seen.add(key)

            row: dict[str, Any] = {
                "institution": institution,
                "forecast": forecast,
                "published_at": iso_z(published_at),
                "source_ref": source_ref,
            }
            internal = internal_historical_mae(
                skill_ledger,
                institution=institution,
                indicator=indicator,
            )
            if internal is not None:
                historical_mae, sample_size = internal
                row["historical_mae"] = historical_mae
                row["historical_mae_source"] = "briefrooms_prospective_ledger"
                row["historical_sample_size"] = sample_size
            else:
                external_mae_ref = _http_url(raw_forecast.get("historical_mae_source_ref"))
                try:
                    historical_mae = float(raw_forecast.get("historical_mae"))
                    if (
                        math.isfinite(historical_mae)
                        and historical_mae > 0
                        and external_mae_ref is not None
                    ):
                        row["historical_mae"] = historical_mae
                        row["historical_mae_source"] = "sourced_external_history"
                        row["historical_mae_source_ref"] = external_mae_ref
                except (TypeError, ValueError):
                    pass

            rank_ref = _http_url(raw_forecast.get("external_rank_source_ref"))
            try:
                external_rank = float(raw_forecast.get("external_rank"))
                if math.isfinite(external_rank) and external_rank > 0 and rank_ref is not None:
                    row["external_rank"] = external_rank
                    row["external_rank_source_ref"] = rank_ref
            except (TypeError, ValueError):
                pass
            forecasts.append(row)

        # The downstream distribution requires at least two sourced forecasts.
        if len(forecasts) < 2:
            continue

        release: dict[str, Any] = {
            "region": region,
            "indicator": indicator,
            "period": period,
            "event_at": iso_z(event_at),
            "unit": str(raw_release.get("unit") or "unknown"),
            "forecasts": forecasts,
        }
        try:
            consensus = float(raw_release.get("market_consensus"))
            if math.isfinite(consensus):
                release["market_consensus"] = consensus
        except (TypeError, ValueError):
            pass
        releases.append(release)

    return {
        "schema_version": "briefrooms-macro-expectations-v1",
        "provider": provider,
        "status": "ready" if releases else "no_valid_releases",
        "collected_at": iso_z(now),
        "releases": releases,
    }


def fetch_payload(url: str, *, token: str = "", timeout: int = 20) -> Mapping[str, Any]:
    valid_url = _http_url(url)
    if valid_url is None:
        raise ValueError("BELIEF_MACRO_EXPECTATIONS_URL must be http(s)")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(valid_url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("macro expectations feed must return a JSON object")
    return payload


def collect(
    now: datetime,
    *,
    url: str,
    token: str = "",
    skill_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    if not str(url or "").strip():
        return {
            "schema_version": "briefrooms-macro-expectations-v1",
            "provider": "unconfigured",
            "status": "unconfigured",
            "collected_at": iso_z(now),
            "releases": [],
        }
    try:
        return normalize_payload(
            fetch_payload(url, token=token),
            now=now,
            skill_ledger=skill_ledger,
        )
    except Exception as exc:
        return {
            "schema_version": "briefrooms-macro-expectations-v1",
            "provider": "configured-feed",
            "status": "fetch_or_validation_error",
            "error_type": type(exc).__name__,
            "collected_at": iso_z(now),
            "releases": [],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--now")
    parser.add_argument("--skill-ledger")
    args = parser.parse_args()

    now = _dt(args.now) if args.now else datetime.now(timezone.utc)
    assert now is not None
    skill_ledger = load_state(Path(args.skill_ledger)) if args.skill_ledger else None
    payload = collect(
        now,
        url=os.environ.get(EXPECTATIONS_URL_ENV, ""),
        token=os.environ.get(EXPECTATIONS_TOKEN_ENV, ""),
        skill_ledger=skill_ledger,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload.get("status"),
        "provider": payload.get("provider"),
        "release_count": len(payload.get("releases") or []),
        "output": str(output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
