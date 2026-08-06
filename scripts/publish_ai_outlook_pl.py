#!/usr/bin/env python3
"""Regenerate only the Polish AI Outlook while preserving EN byte-for-byte."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from typing import Any

import update_ai_outlook as legacy
import update_ai_outlook_v3 as v3
from ai_outlook_pl_methodology import generate_pl_edition
from check_ai_provider import check_provider
from comment_quality import get_ai_runtime
import publish_ai_outlook_gemini as publisher


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def publish_pl(*, force: bool) -> dict[str, Any]:
    moment = datetime.now(legacy.WARSAW)
    today = moment.date().isoformat()
    current = v3.load_json(v3.OUT, {})
    v3.validate_payload(current)

    if current.get("date") != today:
        raise RuntimeError(
            "PL-only regeneration requires a fresh EN edition for the same Warsaw date"
        )
    before_en = copy.deepcopy(current.get("en"))
    before_en_hash = _stable_hash(before_en)

    if (
        not force
        and current.get("pl", {}).get("engine", {}).get("methodology_version")
        == "pl-outcome-forecast-v2"
    ):
        publisher.validate_current(require_today=True)
        print("Polish AI Outlook already uses pl-outcome-forecast-v2")
        return current

    runtime = get_ai_runtime()
    publisher.require_gemini(runtime)
    health = check_provider(runtime=runtime)
    if health.get("status") != "healthy":
        raise RuntimeError(f"Gemini preflight did not pass: {health}")

    pl_edition, pl_audit = generate_pl_edition(moment, runtime)
    payload = copy.deepcopy(current)
    payload.update(
        {
            "date": today,
            "generated_at": moment.astimezone(legacy.WARSAW).isoformat(
                timespec="seconds"
            ),
            "pl": pl_edition,
            "generation_mode": "ai_primary",
            "ai_provider": publisher.REQUIRED_PROVIDER,
            "ai_model": runtime.generation_model,
        }
    )

    if _stable_hash(payload.get("en")) != before_en_hash:
        raise RuntimeError("EN edition changed during PL-only regeneration")

    audit = {
        "schema_version": "ai-outlook-pl-only-publication-v2",
        "date": today,
        "generated_at": payload["generated_at"],
        "edition_policy": payload.get("edition_policy"),
        "source_policy": payload.get("source_policy"),
        "generation_mode": "ai_primary",
        "ai_provider": publisher.REQUIRED_PROVIDER,
        "generation_model": runtime.generation_model,
        "review_model": runtime.review_model,
        "provider_preflight": health,
        "en_preserved_sha256": before_en_hash,
        "editions": {
            "pl": pl_audit,
            "en": {
                "mode": "preserved_without_regeneration",
                "forecast_id": before_en.get("forecast_id"),
                "sha256": before_en_hash,
            },
        },
    }

    publisher.validate_payload(payload, today=today)
    daily_status = publisher.build_daily_status(payload, runtime)
    provider_status = publisher.build_provider_status(payload, runtime, health)
    publisher.validate_status(daily_status, provider_status, today=today)

    v3.publish(payload, audit)
    publisher.write_json(publisher.STATUS_PATH, daily_status)
    publisher.write_json(publisher.PROVIDER_STATUS_PATH, provider_status)

    reloaded = v3.load_json(v3.OUT, {})
    if _stable_hash(reloaded.get("en")) != before_en_hash:
        raise RuntimeError("EN edition changed after PL-only publication")
    publisher.validate_payload(reloaded, today=today)
    return reloaded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--require-today", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        payload = publisher.validate_current(require_today=args.require_today)
        print(
            "Polish AI Outlook is valid: "
            f"{payload['pl']['forecast_id']} "
            f"methodology={payload['pl']['engine']['methodology_version']}"
        )
        return 0

    payload = publish_pl(force=args.force)
    print(
        "Published PL-only AI Outlook: "
        f"{payload['pl']['forecast_id']} "
        f"EN preserved={payload['en']['forecast_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
