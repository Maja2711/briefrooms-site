#!/usr/bin/env python3
"""Publish independent PL and EN AI Outlook editions from same-language news only."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_ai_outlook as legacy  # noqa: E402
import update_ai_outlook_v2 as v2  # noqa: E402
from ai_outlook_engine import (  # noqa: E402
    ENGINE_VERSION,
    GOVERNANCE_SCHEMA_VERSION,
    RESOLUTION_SCHEMA_VERSION,
    balanced_source_pool,
    probability_from_score,
    select_candidate,
    weights_snapshot,
)
from comment_quality import get_ai_runtime, request_json_completion  # noqa: E402
from ai_outlook_analysis_contract import validate_public_analysis  # noqa: E402

OUT = ROOT / "data" / "ai_outlook.json"
HISTORY_DIR = ROOT / "data" / "ai_outlook_history"
AUDIT_DIR = ROOT / "data" / "internal" / "ai_outlook_audit"
SCHEMA_VERSION = 2
MIN_SOURCES = 4
MAX_RAW_SOURCES = 80
AREA_BY_SECTION = {
    "pl": {
        "polityka": "geopolitics",
        "ekonomia": "economy",
        "zdrowie": "health",
        "nauka": "science",
    },
    "en": {
        "world-news": "geopolitics",
        "asia-pacific": "geopolitics",
        "europe": "geopolitics",
        "middle-east": "geopolitics",
        "business": "economy",
        "science": "science",
        "health": "health",
    },
}
SOURCE_POLICY = {
    "pl": "polish-language-canonical-news-only",
    "en": "english-language-canonical-news-only",
}


class OutlookV3ValidationError(ValueError):
    pass


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def source_items(language: str) -> list[dict[str, Any]]:
    """Build a forecast source pool from exactly one canonical language feed."""
    if language not in {"pl", "en"}:
        raise ValueError(f"unsupported language: {language}")
    payload = load_json(ROOT / "data" / "news" / f"{language}.json", {})
    if payload.get("language") != language:
        raise RuntimeError(f"canonical news language mismatch for {language}")
    if not str(payload.get("schema_version") or "").startswith("news-live-v"):
        raise RuntimeError(f"unsupported canonical news schema for {language}")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise RuntimeError(f"missing canonical news sections for {language}")

    raw: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section_id, area in AREA_BY_SECTION[language].items():
        entries = sections.get(section_id, [])
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            url = legacy.compact(item.get("link"), 500)
            title = legacy.compact(item.get("title"), 240)
            summary = legacy.compact(item.get("summary"), 700)
            source = legacy.compact(item.get("source"), 100)
            if not url.startswith("https://") or not title or not summary or not source:
                continue
            if url in seen:
                continue
            seen.add(url)
            raw.append(
                {
                    "language": language,
                    "source_language": language,
                    "category": area,
                    "title": title,
                    "summary": summary,
                    "source": source,
                    "url": url,
                    "published_at": legacy.compact(item.get("published_at"), 60),
                    "provenance_id": legacy.compact(item.get("provenance_id"), 100),
                    "origin_organization": legacy.compact(item.get("origin_organization"), 120),
                    "origin_document_url": legacy.compact(item.get("origin_document_url"), 500),
                    "origin_published_at": legacy.compact(item.get("origin_published_at"), 60),
                }
            )
            if len(raw) >= MAX_RAW_SOURCES:
                break
        if len(raw) >= MAX_RAW_SOURCES:
            break

    selected = balanced_source_pool(raw)
    if any(item.get("source_language") != language for item in selected):
        raise RuntimeError(f"cross-language source leaked into {language} pool")
    return selected


def recent_titles(language: str, limit: int = 20) -> list[str]:
    if not HISTORY_DIR.exists():
        return []
    titles: list[str] = []
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]:
        payload = load_json(path, {})
        section = payload.get(language) if isinstance(payload, dict) else None
        title = legacy.compact((section or {}).get("title"), 160) if isinstance(section, dict) else ""
        if title:
            titles.append(title)
    return titles


def generate_language(
    language: str,
    moment: datetime,
    runtime: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    items = source_items(language)
    if len(items) < MIN_SOURCES:
        raise RuntimeError(f"not enough current {language} source items")
    recent = recent_titles(language)
    publication_date = moment.astimezone(legacy.WARSAW).date().isoformat()

    candidate_raw = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=v2.candidate_messages(items, recent, publication_date),
        max_tokens=3200,
        temperature=0.30,
        timeout=90,
    )
    winner, ranked, decision_log = select_candidate(
        v2.parse_candidates(candidate_raw),
        items,
        recent,
        publication_date,
    )
    probability = probability_from_score(winner["engine_score"])
    final_raw = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=v2.final_messages(winner, items, probability, publication_date),
        max_tokens=2600,
        temperature=0.18,
        timeout=90,
    )
    full_payload, audit = v2.build_payload(
        final_raw,
        winner,
        ranked,
        decision_log,
        items,
        moment,
        probability,
    )

    edition = dict(full_payload[language])
    edition.update(
        {
            "forecast_id": f"{publication_date}-{language}-{winner['candidate_id']}",
            "source_language": language,
            "source_policy": SOURCE_POLICY[language],
            "resolution": full_payload["resolution"],
            "governance": full_payload["governance"],
            "engine": {**full_payload["engine"], "edition_language": language},
        }
    )
    edition["sources"] = [
        {**source, "source_language": language}
        for source in edition.get("sources", [])
    ]
    validate_edition(language, edition)
    audit = {
        **audit,
        "edition_language": language,
        "source_policy": SOURCE_POLICY[language],
    }
    return edition, audit


def validate_edition(language: str, edition: dict[str, Any]) -> None:
    if not isinstance(edition, dict):
        raise OutlookV3ValidationError(f"missing {language} edition")
    for field in v2.REQUIRED_FINAL_FIELDS + ("date_label", "forecast_id", "disclaimer"):
        if not legacy.compact(edition.get(field), 700):
            raise OutlookV3ValidationError(f"missing {language}.{field}")
    probability = edition.get("probability")
    if not isinstance(probability, int) or not 55 <= probability <= 80:
        raise OutlookV3ValidationError(f"invalid {language} probability")
    if edition.get("horizon") not in legacy.ALLOWED_HORIZONS[language]:
        raise OutlookV3ValidationError(f"invalid {language} horizon")
    if edition.get("source_language") != language:
        raise OutlookV3ValidationError(f"invalid {language} source language")
    if edition.get("source_policy") != SOURCE_POLICY[language]:
        raise OutlookV3ValidationError(f"invalid {language} source policy")

    sources = edition.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 3:
        raise OutlookV3ValidationError(f"invalid {language} sources")
    for source in sources:
        if not isinstance(source, dict) or source.get("source_language") != language:
            raise OutlookV3ValidationError(f"cross-language source in {language} edition")
        if not legacy.compact(source.get("name"), 100):
            raise OutlookV3ValidationError(f"invalid {language} source name")
        if not legacy.compact(source.get("url"), 500).startswith("https://"):
            raise OutlookV3ValidationError(f"invalid {language} source URL")
        if not legacy.compact(source.get("provenance_id"), 100):
            raise OutlookV3ValidationError(f"missing {language} source provenance")

    resolution = edition.get("resolution")
    if not isinstance(resolution, dict) or resolution.get("schema_version") != RESOLUTION_SCHEMA_VERSION:
        raise OutlookV3ValidationError(f"invalid {language} resolution")
    governance = edition.get("governance")
    if not isinstance(governance, dict) or governance.get("schema_version") != GOVERNANCE_SCHEMA_VERSION:
        raise OutlookV3ValidationError(f"invalid {language} governance")
    disclaimers = governance.get("disclaimers")
    if governance.get("disclaimer_required") and (
        not isinstance(disclaimers, dict)
        or edition.get("disclaimer") != disclaimers.get(language)
    ):
        raise OutlookV3ValidationError(f"invalid {language} disclaimer")
    engine = edition.get("engine")
    if not isinstance(engine, dict) or engine.get("version") != ENGINE_VERSION:
        raise OutlookV3ValidationError(f"invalid {language} engine")
    if engine.get("edition_language") != language:
        raise OutlookV3ValidationError(f"invalid {language} engine language")
    if engine.get("weights_snapshot") != weights_snapshot():
        raise OutlookV3ValidationError(f"invalid {language} weights snapshot")
    validate_public_analysis(edition, language)


def validate_payload(payload: dict[str, Any], *, require_v2: bool = True) -> None:
    if not isinstance(payload, dict):
        raise OutlookV3ValidationError("AI Outlook payload is not an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        if not require_v2 and payload.get("schema_version") == 1:
            legacy.validate_file(payload)
            return
        raise OutlookV3ValidationError("invalid AI Outlook schema version")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(payload.get("date") or "")):
        raise OutlookV3ValidationError("invalid AI Outlook date")
    if payload.get("edition_policy") != "independent-per-language":
        raise OutlookV3ValidationError("invalid edition policy")
    if payload.get("source_policy") != SOURCE_POLICY:
        raise OutlookV3ValidationError("invalid source policy map")
    for language in ("pl", "en"):
        validate_edition(language, payload.get(language))


def generate(moment: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = get_ai_runtime()
    if not runtime.available:
        raise RuntimeError("AI provider is unavailable")
    editions: dict[str, Any] = {}
    audits: dict[str, Any] = {}
    for language in ("pl", "en"):
        edition, audit = generate_language(language, moment, runtime)
        editions[language] = edition
        audits[language] = audit
    local = moment.astimezone(legacy.WARSAW)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": local.date().isoformat(),
        "generated_at": local.isoformat(timespec="seconds"),
        "edition_policy": "independent-per-language",
        "source_policy": dict(SOURCE_POLICY),
        "pl": editions["pl"],
        "en": editions["en"],
    }
    audit = {
        "schema_version": "ai-outlook-audit-v2",
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "edition_policy": payload["edition_policy"],
        "source_policy": payload["source_policy"],
        "editions": audits,
    }
    validate_payload(payload)
    return payload, audit


def publish(payload: dict[str, Any], audit: dict[str, Any]) -> None:
    validate_payload(payload)
    if OUT.exists():
        current = load_json(OUT, {})
        current_date = legacy.compact(current.get("date"), 20) if isinstance(current, dict) else ""
        if current_date and current_date != payload["date"]:
            archive = HISTORY_DIR / f"{current_date}.json"
            if not archive.exists():
                write_json(archive, current)
    write_json(OUT, payload)
    write_json(HISTORY_DIR / f"{payload['date']}.json", payload)
    write_json(AUDIT_DIR / f"{payload['date']}.json", audit)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--require-v2", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    current = load_json(OUT, {})
    if args.validate_only:
        validate_payload(current, require_v2=args.require_v2)
        print(f"AI Outlook valid: {OUT}")
        return 0

    moment = datetime.now(legacy.WARSAW)
    today = moment.date().isoformat()
    if not args.force and current.get("date") == today and current.get("schema_version") == SCHEMA_VERSION:
        validate_payload(current)
        print(f"Independent PL and EN AI Outlook already published for {today}")
        return 0

    payload, audit = generate(moment)
    publish(payload, audit)
    print(
        f"Published independent AI Outlook for {payload['date']}: "
        f"PL={payload['pl']['engine']['selected_area']} "
        f"EN={payload['en']['engine']['selected_area']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
