#!/usr/bin/env python3
"""Generate one source-grounded AI Outlook forecast for the BriefRooms homepage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ai_outlook.json"
HISTORY_DIR = ROOT / "data" / "ai_outlook_history"
WARSAW = ZoneInfo("Europe/Warsaw")

sys.path.insert(0, str(ROOT / "scripts"))
from comment_quality import get_ai_runtime, request_json_completion  # noqa: E402

MAX_SOURCE_ITEMS = 18
MAX_SOURCE_SELECTION = 3
ALLOWED_HORIZONS = {
    "pl": {"3–6 miesięcy", "6–12 miesięcy", "1–3 lata", "3–10 lat"},
    "en": {"3–6 months", "6–12 months", "1–3 years", "3–10 years"},
}
REQUIRED_TEXT_FIELDS = (
    "category",
    "title",
    "thesis",
    "horizon",
    "rationale",
    "confirmation",
    "invalidation",
)


class OutlookValidationError(ValueError):
    pass


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip()


def source_items() -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for language in ("pl", "en"):
        payload = load_json(ROOT / language / "home_brief.json", {})
        for section in ("latest", "radar"):
            for item in payload.get(section, []) if isinstance(payload, dict) else []:
                link = compact(item.get("link"), 500)
                title = compact(item.get("title"), 240)
                if not link.startswith("https://") or not title or link in seen:
                    continue
                seen.add(link)
                candidates.append(
                    {
                        "language": language,
                        "category": compact(item.get("category"), 100),
                        "title": title,
                        "summary": compact(item.get("summary") or item.get("details"), 620),
                        "source": compact(item.get("source"), 100) or "Source",
                        "url": link,
                        "published_at": compact(item.get("published_at"), 60),
                    }
                )
                if len(candidates) >= MAX_SOURCE_ITEMS:
                    return candidates
    return candidates


def recent_titles(limit: int = 14) -> list[str]:
    if not HISTORY_DIR.exists():
        return []
    titles: list[str] = []
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]:
        payload = load_json(path, {})
        title = compact(((payload.get("pl") or {}).get("title")), 160)
        if title:
            titles.append(title)
    return titles


def date_labels(moment: datetime) -> tuple[str, str]:
    months = (
        "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
        "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
    )
    local = moment.astimezone(WARSAW)
    pl = f"{local.day} {months[local.month - 1]} {local.year}"
    en = local.strftime("%B %-d, %Y")
    return pl, en


def build_prompt(items: list[dict[str, str]], previous: list[str], local_date: str) -> list[dict[str, str]]:
    indexed = [{"id": index + 1, **item} for index, item in enumerate(items)]
    system = (
        "You are the BriefRooms AI Outlook editor. Produce one testable forecast, not a recap. "
        "Use only the supplied source items. Be analytical, calibrated and specific, but never present "
        "a forecast as a fact. Do not invent people, numbers, companies, events, URLs or source names. "
        "Avoid direct buy/sell advice and avoid predicting an exact security price. Return strict JSON only."
    )
    user = {
        "publication_date_europe_warsaw": local_date,
        "task": (
            "Select the strongest forward-looking signal from the source items and create one bilingual "
            "AI Outlook forecast for the homepage. It should concern the economy, business, technology, "
            "society, geopolitics, health or science. Prefer a structural forecast with a clear horizon."
        ),
        "rules": [
            "Probability must be an integer from 55 to 80.",
            "Choose 1 to 3 source_ids from the supplied items.",
            "Polish and English versions must express the same forecast.",
            "PL horizon must be one of: 3–6 miesięcy, 6–12 miesięcy, 1–3 lata, 3–10 lat.",
            "EN horizon must be one of: 3–6 months, 6–12 months, 1–3 years, 3–10 years.",
            "Title: max 100 characters. Thesis: max 520. Rationale: max 480.",
            "Confirmation and invalidation: max 260 characters each.",
            "Do not repeat a recent topic unless the new evidence materially changes the thesis.",
        ],
        "recent_polish_titles_to_avoid": previous,
        "source_items": indexed,
        "required_json_shape": {
            "probability": 68,
            "source_ids": [1],
            "pl": {
                "category": "...",
                "title": "...",
                "thesis": "...",
                "horizon": "6–12 miesięcy",
                "rationale": "...",
                "confirmation": "...",
                "invalidation": "...",
            },
            "en": {
                "category": "...",
                "title": "...",
                "thesis": "...",
                "horizon": "6–12 months",
                "rationale": "...",
                "confirmation": "...",
                "invalidation": "...",
            },
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def validate_generated(raw: dict[str, Any], items: list[dict[str, str]], moment: datetime) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise OutlookValidationError("AI response is not an object")

    try:
        probability = int(raw.get("probability"))
    except (TypeError, ValueError) as exc:
        raise OutlookValidationError("Probability is not an integer") from exc
    if probability < 55 or probability > 80:
        raise OutlookValidationError("Probability outside 55-80")

    ids = raw.get("source_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_SOURCE_SELECTION:
        raise OutlookValidationError("source_ids must contain 1-3 items")
    normalized_ids: list[int] = []
    for value in ids:
        try:
            source_id = int(value)
        except (TypeError, ValueError) as exc:
            raise OutlookValidationError("Invalid source id") from exc
        if source_id < 1 or source_id > len(items) or source_id in normalized_ids:
            raise OutlookValidationError("Unknown or duplicate source id")
        normalized_ids.append(source_id)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "date": moment.astimezone(WARSAW).date().isoformat(),
        "generated_at": moment.astimezone(WARSAW).isoformat(timespec="seconds"),
        "probability": probability,
        "source_policy": "local-source-grounded",
    }
    pl_date, en_date = date_labels(moment)

    sources = [
        {"name": items[source_id - 1]["source"], "url": items[source_id - 1]["url"]}
        for source_id in normalized_ids
    ]
    for language, date_label in (("pl", pl_date), ("en", en_date)):
        section = raw.get(language)
        if not isinstance(section, dict):
            raise OutlookValidationError(f"Missing {language} section")
        clean: dict[str, Any] = {}
        limits = {
            "category": 90,
            "title": 100,
            "thesis": 520,
            "horizon": 30,
            "rationale": 480,
            "confirmation": 260,
            "invalidation": 260,
        }
        for field in REQUIRED_TEXT_FIELDS:
            value = compact(section.get(field), limits[field])
            if not value:
                raise OutlookValidationError(f"Missing {language}.{field}")
            clean[field] = value
        if clean["horizon"] not in ALLOWED_HORIZONS[language]:
            raise OutlookValidationError(f"Unsupported {language} horizon")
        clean["date_label"] = date_label
        clean["probability"] = probability
        clean["sources"] = sources
        payload[language] = clean
    return payload


def validate_file(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise OutlookValidationError("Invalid schema_version")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(payload.get("date") or "")):
        raise OutlookValidationError("Invalid date")
    probability = payload.get("probability")
    if not isinstance(probability, int) or not 55 <= probability <= 80:
        raise OutlookValidationError("Invalid probability")
    for language in ("pl", "en"):
        section = payload.get(language)
        if not isinstance(section, dict):
            raise OutlookValidationError(f"Missing {language}")
        for field in REQUIRED_TEXT_FIELDS + ("date_label",):
            if not compact(section.get(field), 600):
                raise OutlookValidationError(f"Missing {language}.{field}")
        if section.get("horizon") not in ALLOWED_HORIZONS[language]:
            raise OutlookValidationError(f"Invalid {language} horizon")
        sources = section.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCE_SELECTION:
            raise OutlookValidationError(f"Invalid {language} sources")
        for source in sources:
            if not isinstance(source, dict) or not compact(source.get("name"), 100):
                raise OutlookValidationError("Invalid source name")
            if not compact(source.get("url"), 500).startswith("https://"):
                raise OutlookValidationError("Invalid source URL")


def generate(moment: datetime) -> dict[str, Any]:
    items = source_items()
    if len(items) < 4:
        raise RuntimeError("Not enough current source items for AI Outlook")
    runtime = get_ai_runtime()
    if not runtime.available:
        raise RuntimeError("AI provider is unavailable")
    raw = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=build_prompt(items, recent_titles(), moment.astimezone(WARSAW).date().isoformat()),
        max_tokens=1500,
        temperature=0.55,
        timeout=60,
    )
    return validate_generated(raw, items, moment)


def publish(payload: dict[str, Any]) -> None:
    validate_file(payload)
    if OUT.exists():
        current = load_json(OUT, {})
        current_date = compact(current.get("date"), 20)
        if current_date:
            archive = HISTORY_DIR / f"{current_date}.json"
            if not archive.exists():
                write_json(archive, current)
    write_json(OUT, payload)
    write_json(HISTORY_DIR / f"{payload['date']}.json", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        validate_file(load_json(OUT, {}))
        print(f"AI Outlook valid: {OUT}")
        return 0

    moment = datetime.now(WARSAW)
    current = load_json(OUT, {})
    today = moment.date().isoformat()
    if not args.force and current.get("date") == today:
        validate_file(current)
        print(f"AI Outlook already published for {today}")
        return 0

    try:
        payload = generate(moment)
    except Exception as exc:
        if OUT.exists():
            validate_file(current)
            print(f"AI Outlook generation skipped; keeping previous edition: {exc}", file=sys.stderr)
            return 0
        raise

    publish(payload)
    print(f"Published AI Outlook for {payload['date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
