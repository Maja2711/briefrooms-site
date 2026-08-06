#!/usr/bin/env python3
"""Publish the daily PL and EN AI Outlook only after verified Gemini inference.

This is the production entry point for AI Outlook. It is deliberately
fail-closed: missing credentials, an unhealthy Gemini endpoint, invalid model
output or a non-Gemini runtime stops the workflow before any public file is
changed. Deterministic continuity content is not published under the AI Outlook
label.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Import explicitly as well as through Python's site hook. This keeps the
# Gemini-first adapter active in local tests and in GitHub Actions.
import sitecustomize  # noqa: F401,E402
import update_ai_outlook as legacy  # noqa: E402
import update_ai_outlook_v3 as v3  # noqa: E402
from ai_outlook_pl_quality import validate_pl_edition  # noqa: E402
from check_ai_provider import check_provider  # noqa: E402
from comment_quality import AiRuntime, get_ai_runtime  # noqa: E402

STATUS_PATH = ROOT / "data" / "ai_outlook_status.json"
PROVIDER_STATUS_PATH = ROOT / "data" / "ai_outlook_provider_status.json"
STATUS_SCHEMA = "ai-outlook-daily-status-v2"
PROVIDER_STATUS_SCHEMA = "ai-outlook-provider-status-v1"
REQUIRED_PROVIDER = "gemini"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def require_gemini(runtime: AiRuntime) -> None:
    if not runtime.available:
        raise RuntimeError("GEMINI_API_KEY is missing or the Gemini runtime is incomplete")
    if runtime.provider != REQUIRED_PROVIDER:
        raise RuntimeError(
            f"AI Outlook requires Gemini; resolved provider is {runtime.provider!r}"
        )
    if not runtime.generation_model or not runtime.review_model:
        raise RuntimeError("Gemini generation and review models must be configured")


def validate_payload(payload: dict[str, Any], *, today: str) -> None:
    v3.validate_payload(payload)
    # The Polish edition has an additional fail-closed semantic gate. It rejects
    # mixed metrics, missing baselines, vague predictions and unrelated evidence
    # before v3.publish can touch any public file. The EN edition is unchanged.
    validate_pl_edition(payload.get("pl") or {})
    if payload.get("date") != today:
        raise RuntimeError(
            f"Gemini AI Outlook date mismatch: expected {today}, got {payload.get('date')!r}"
        )
    if payload.get("generation_mode") != "ai_primary":
        raise RuntimeError("AI Outlook must be generated in ai_primary mode")
    if payload.get("ai_provider") != REQUIRED_PROVIDER:
        raise RuntimeError("AI Outlook payload is not marked as Gemini-generated")
    if not str(payload.get("ai_model") or "").startswith("gemini-"):
        raise RuntimeError("AI Outlook payload has an invalid Gemini model")
    for language in ("pl", "en"):
        edition = payload.get(language) or {}
        if edition.get("source_language") != language:
            raise RuntimeError(f"{language} edition source-language mismatch")
        if not str(edition.get("forecast_id") or "").startswith(
            f"{today}-{language}-"
        ):
            raise RuntimeError(f"{language} edition forecast ID is stale")


def validate_status(
    status: dict[str, Any],
    provider_status: dict[str, Any],
    *,
    today: str,
) -> None:
    if status.get("status") != "fresh" or status.get("date") != today:
        raise RuntimeError("AI Outlook daily status is not fresh")
    if status.get("mode") != "ai_primary" or status.get("provider") != REQUIRED_PROVIDER:
        raise RuntimeError("AI Outlook daily status is not Gemini ai_primary")
    if provider_status.get("status") != "healthy":
        raise RuntimeError("Gemini provider status is not healthy")
    if provider_status.get("date") != today:
        raise RuntimeError("Gemini provider status is stale")
    if provider_status.get("provider") != REQUIRED_PROVIDER:
        raise RuntimeError("Provider status does not identify Gemini")


def build_daily_status(payload: dict[str, Any], runtime: AiRuntime) -> dict[str, Any]:
    return {
        "schema_version": STATUS_SCHEMA,
        "status": "fresh",
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "timezone": "Europe/Warsaw",
        "mode": "ai_primary",
        "provider": REQUIRED_PROVIDER,
        "generation_model": runtime.generation_model,
        "review_model": runtime.review_model,
        "primary_error": "",
        "pl_forecast_id": payload["pl"]["forecast_id"],
        "en_forecast_id": payload["en"]["forecast_id"],
        "freshness_policy": (
            "payload date must equal the current Europe/Warsaw calendar date"
        ),
    }


def build_provider_status(
    payload: dict[str, Any], runtime: AiRuntime, health: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": PROVIDER_STATUS_SCHEMA,
        "status": "healthy",
        "date": payload["date"],
        "checked_at": payload["generated_at"],
        "provider": REQUIRED_PROVIDER,
        "endpoint": health.get("endpoint"),
        "generation_model": runtime.generation_model,
        "review_model": runtime.review_model,
        "preflight_status_code": health.get("status_code"),
        "publication_mode": "ai_primary",
    }


def current_is_valid(today: str) -> bool:
    payload = v3.load_json(v3.OUT, {})
    status = v3.load_json(STATUS_PATH, {})
    provider_status = v3.load_json(PROVIDER_STATUS_PATH, {})
    try:
        validate_payload(payload, today=today)
        validate_status(status, provider_status, today=today)
    except Exception:
        return False
    return True


def publish(*, force: bool) -> dict[str, Any]:
    moment = datetime.now(legacy.WARSAW)
    today = moment.date().isoformat()
    if not force and current_is_valid(today):
        payload = v3.load_json(v3.OUT, {})
        print(f"Gemini AI Outlook already published and verified for {today}")
        return payload

    runtime = get_ai_runtime()
    require_gemini(runtime)
    health = check_provider(runtime=runtime)
    if health.get("status") != "healthy":
        raise RuntimeError(f"Gemini preflight did not pass: {health}")

    payload, audit = v3.generate(moment)
    payload.update(
        {
            "generation_mode": "ai_primary",
            "ai_provider": REQUIRED_PROVIDER,
            "ai_model": runtime.generation_model,
        }
    )
    audit.update(
        {
            "generation_mode": "ai_primary",
            "ai_provider": REQUIRED_PROVIDER,
            "generation_model": runtime.generation_model,
            "review_model": runtime.review_model,
            "provider_preflight": health,
        }
    )
    validate_payload(payload, today=today)

    daily_status = build_daily_status(payload, runtime)
    provider_status = build_provider_status(payload, runtime, health)
    validate_status(daily_status, provider_status, today=today)

    # Publish only after all provider, payload and status validations pass.
    v3.publish(payload, audit)
    write_json(STATUS_PATH, daily_status)
    write_json(PROVIDER_STATUS_PATH, provider_status)
    return payload


def validate_current(*, require_today: bool) -> dict[str, Any]:
    payload = v3.load_json(v3.OUT, {})
    status = v3.load_json(STATUS_PATH, {})
    provider_status = v3.load_json(PROVIDER_STATUS_PATH, {})
    today = datetime.now(legacy.WARSAW).date().isoformat()
    expected_date = today if require_today else str(payload.get("date") or "")
    if not expected_date:
        raise RuntimeError("AI Outlook has no publication date")
    validate_payload(payload, today=expected_date)
    validate_status(status, provider_status, today=expected_date)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--require-today", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        payload = validate_current(require_today=args.require_today)
        print(
            "Gemini AI Outlook is valid: "
            f"date={payload['date']} model={payload['ai_model']} "
            f"PL={payload['pl']['forecast_id']} EN={payload['en']['forecast_id']}"
        )
        return 0

    payload = publish(force=args.force)
    validate_current(require_today=True)
    print(
        "Published verified Gemini AI Outlook: "
        f"date={payload['date']} model={payload['ai_model']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
