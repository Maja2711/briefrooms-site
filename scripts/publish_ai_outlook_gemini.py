#!/usr/bin/env python3
"""Publish daily PL and EN AI Outlook editions after verified Gemini inference.

The English edition keeps the existing generation path. The Polish edition uses
an independent real-outcome methodology that rejects meta-forecasts about media
attention, articles, communications or future updates.
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
import ai_outlook_pl_methodology as pl_methodology  # noqa: E402
from ai_outlook_pl_methodology import generate_pl_edition  # noqa: E402
from ai_outlook_pl_quality import validate_pl_edition  # noqa: E402
from check_ai_provider import check_provider  # noqa: E402
from comment_quality import AiRuntime, get_ai_runtime  # noqa: E402

STATUS_PATH = ROOT / "data" / "ai_outlook_status.json"
PROVIDER_STATUS_PATH = ROOT / "data" / "ai_outlook_provider_status.json"
STATUS_SCHEMA = "ai-outlook-daily-status-v2"
PROVIDER_STATUS_SCHEMA = "ai-outlook-provider-status-v1"
REQUIRED_PROVIDER = "gemini"
PRIMARY_MODEL = "gemini-3.5-flash"
FALLBACK_MODEL = "gemini-3.5-flash-lite"
APPROVED_BUDGET_MODELS = frozenset({PRIMARY_MODEL, FALLBACK_MODEL})
FINAL_OUTPUT_TOKEN_FLOOR = 8192


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
    configured = {runtime.generation_model, runtime.review_model}
    if not configured.issubset(APPROVED_BUDGET_MODELS):
        raise RuntimeError(
            "AI Outlook budget policy allows only "
            f"{sorted(APPROVED_BUDGET_MODELS)}; got {sorted(configured)}"
        )


def validate_payload(payload: dict[str, Any], *, today: str) -> None:
    v3.validate_payload(payload)
    validate_pl_edition(payload.get("pl") or {})
    if payload.get("date") != today:
        raise RuntimeError(
            f"Gemini AI Outlook date mismatch: expected {today}, got {payload.get('date')!r}"
        )
    if payload.get("generation_mode") != "ai_primary":
        raise RuntimeError("AI Outlook must be generated in ai_primary mode")
    if payload.get("ai_provider") != REQUIRED_PROVIDER:
        raise RuntimeError("AI Outlook payload is not marked as Gemini-generated")
    if payload.get("ai_model") not in APPROVED_BUDGET_MODELS:
        raise RuntimeError("AI Outlook payload uses a model outside the budget allowlist")
    if payload.get("ai_model_role") not in {"primary", "fallback"}:
        raise RuntimeError("AI Outlook payload has an invalid model role")
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
    if status.get("generation_model") not in APPROVED_BUDGET_MODELS:
        raise RuntimeError("AI Outlook daily status uses a model outside the budget allowlist")
    if status.get("model_role") not in {"primary", "fallback"}:
        raise RuntimeError("AI Outlook daily status has an invalid model role")
    if provider_status.get("status") != "healthy":
        raise RuntimeError("Gemini provider status is not healthy")
    if provider_status.get("date") != today:
        raise RuntimeError("Gemini provider status is stale")
    if provider_status.get("provider") != REQUIRED_PROVIDER:
        raise RuntimeError("Provider status does not identify Gemini")
    if provider_status.get("generation_model") != status.get("generation_model"):
        raise RuntimeError("Provider and daily status model mismatch")


def build_daily_status(
    payload: dict[str, Any],
    runtime: AiRuntime,
    *,
    model_role: str = "primary",
    primary_error: str = "",
) -> dict[str, Any]:
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
        "model_role": model_role,
        "fallback_used": model_role == "fallback",
        "primary_error": primary_error,
        "pl_forecast_id": payload["pl"]["forecast_id"],
        "en_forecast_id": payload["en"]["forecast_id"],
        "freshness_policy": (
            "payload date must equal the current Europe/Warsaw calendar date"
        ),
        "budget_policy": (
            "approved free-tier-capable Gemini models only; no paid-provider fallback"
        ),
    }


def build_provider_status(
    payload: dict[str, Any],
    runtime: AiRuntime,
    health: dict[str, Any],
    *,
    model_role: str = "primary",
    primary_error: str = "",
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
        "model_role": model_role,
        "fallback_used": model_role == "fallback",
        "primary_error": primary_error,
        "preflight_status_code": health.get("status_code"),
        "publication_mode": "ai_primary",
        "budget_policy": "no paid-provider fallback",
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


def _runtime_for_model(base: AiRuntime, model: str) -> AiRuntime:
    if model not in APPROVED_BUDGET_MODELS:
        raise RuntimeError(f"model {model!r} is outside the AI Outlook budget allowlist")
    return AiRuntime(
        provider=REQUIRED_PROVIDER,
        api_key=base.api_key,
        endpoint=base.endpoint,
        generation_model=model,
        review_model=model,
    )


def _inflate_final_json_budget(original):
    def wrapped(**kwargs):
        requested = int(kwargs.get("max_tokens") or 0)
        # Candidate reasoning keeps the existing budget. Final PL/EN structured
        # copy gets more room so Gemini thinking tokens cannot truncate the JSON.
        if 0 < requested <= 2800:
            kwargs = dict(kwargs)
            kwargs["max_tokens"] = max(requested, FINAL_OUTPUT_TOKEN_FLOOR)
        return original(**kwargs)

    return wrapped


def generate_verified_payload(
    moment: datetime,
    runtime: AiRuntime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate PL with the new methodology and EN with its unchanged path."""
    pl_edition, pl_audit = generate_pl_edition(moment, runtime)
    en_edition, en_audit = v3.generate_language("en", moment, runtime)
    local = moment.astimezone(legacy.WARSAW)
    payload = {
        "schema_version": v3.SCHEMA_VERSION,
        "date": local.date().isoformat(),
        "generated_at": local.isoformat(timespec="seconds"),
        "edition_policy": "independent-per-language",
        "source_policy": dict(v3.SOURCE_POLICY),
        "pl": pl_edition,
        "en": en_edition,
    }
    audit = {
        "schema_version": "ai-outlook-audit-v3",
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "edition_policy": payload["edition_policy"],
        "source_policy": payload["source_policy"],
        "editions": {
            "pl": pl_audit,
            "en": en_audit,
        },
    }
    v3.validate_payload(payload)
    validate_pl_edition(pl_edition)
    return payload, audit


def _generate_verified_payload_with_budget(
    moment: datetime,
    runtime: AiRuntime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    original_pl = pl_methodology.request_json_completion
    original_en = v3.request_json_completion
    pl_methodology.request_json_completion = _inflate_final_json_budget(original_pl)
    v3.request_json_completion = _inflate_final_json_budget(original_en)
    try:
        return generate_verified_payload(moment, runtime)
    finally:
        pl_methodology.request_json_completion = original_pl
        v3.request_json_completion = original_en


def publish(*, force: bool) -> dict[str, Any]:
    moment = datetime.now(legacy.WARSAW)
    today = moment.date().isoformat()
    if not force and current_is_valid(today):
        payload = v3.load_json(v3.OUT, {})
        print(f"Gemini AI Outlook already published and verified for {today}")
        return payload

    base_runtime = get_ai_runtime()
    require_gemini(base_runtime)

    attempts: list[dict[str, str]] = []
    primary_error = ""
    for model_role, model in (("primary", PRIMARY_MODEL), ("fallback", FALLBACK_MODEL)):
        runtime = _runtime_for_model(base_runtime, model)
        try:
            health = check_provider(runtime=runtime)
            if health.get("status") != "healthy":
                raise RuntimeError(f"Gemini preflight did not pass: {health}")

            payload, audit = _generate_verified_payload_with_budget(moment, runtime)
            payload.update(
                {
                    "generation_mode": "ai_primary",
                    "ai_provider": REQUIRED_PROVIDER,
                    "ai_model": runtime.generation_model,
                    "ai_model_role": model_role,
                }
            )
            success_attempt = {
                "model": model,
                "role": model_role,
                "status": "passed",
            }
            audit.update(
                {
                    "generation_mode": "ai_primary",
                    "ai_provider": REQUIRED_PROVIDER,
                    "generation_model": runtime.generation_model,
                    "review_model": runtime.review_model,
                    "model_role": model_role,
                    "fallback_used": model_role == "fallback",
                    "primary_error": primary_error,
                    "model_attempts": [*attempts, success_attempt],
                    "provider_preflight": health,
                    "budget_policy": (
                        "full Flash first, Flash-Lite only after failure; "
                        "no paid-provider fallback"
                    ),
                }
            )
            validate_payload(payload, today=today)

            daily_status = build_daily_status(
                payload,
                runtime,
                model_role=model_role,
                primary_error=primary_error,
            )
            provider_status = build_provider_status(
                payload,
                runtime,
                health,
                model_role=model_role,
                primary_error=primary_error,
            )
            validate_status(daily_status, provider_status, today=today)

            # Nothing is written before every structural and semantic gate passes.
            v3.publish(payload, audit)
            write_json(STATUS_PATH, daily_status)
            write_json(PROVIDER_STATUS_PATH, provider_status)
            return payload
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            attempts.append(
                {
                    "model": model,
                    "role": model_role,
                    "status": "failed",
                    "error": error,
                }
            )
            if model_role == "primary":
                primary_error = error
            print(
                f"AI Outlook {model_role} model {model} failed closed: {error}",
                file=sys.stderr,
            )

    details = "; ".join(
        f"{row['model']}: {row.get('error', 'failed')}" for row in attempts
    )
    raise RuntimeError(
        "AI Outlook not published: neither approved budget Gemini model passed "
        f"all quality gates. {details}"
    )


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
            f"role={payload['ai_model_role']} "
            f"PL={payload['pl']['forecast_id']} EN={payload['en']['forecast_id']}"
        )
        return 0

    payload = publish(force=args.force)
    validate_current(require_today=True)
    print(
        "Published verified Gemini AI Outlook: "
        f"date={payload['date']} model={payload['ai_model']} "
        f"role={payload['ai_model_role']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
