#!/usr/bin/env python3
"""Publish a fresh PL and EN AI Outlook every Warsaw calendar day.

The primary path uses the governed AI Outlook v3 generator. When the configured
AI provider is unavailable, rate-limited or returns invalid output, a strictly
source-grounded deterministic fallback publishes a new measurable outlook from
the current same-language canonical news feeds. A stale previous edition is
never treated as a successful daily publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import update_ai_outlook as legacy  # noqa: E402
import update_ai_outlook_v3 as v3  # noqa: E402
from ai_outlook_engine import (  # noqa: E402
    AREA_LABELS,
    DISCLAIMER_CATALOG,
    ENGINE_VERSION,
    GOVERNANCE_SCHEMA_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    RESOLUTION_SCHEMA_VERSION,
    SCORING_POLICY_VERSION,
    WEIGHTS_VERSION,
    weights_snapshot,
)

STATUS_PATH = ROOT / "data" / "ai_outlook_status.json"
FALLBACK_VERSION = "ai-outlook-daily-fallback-v1"
STATUS_SCHEMA = "ai-outlook-daily-status-v1"

AREA_CONTENT_CATEGORY = {
    "economy": "macro",
    "geopolitics": "geopolitics",
    "health": "public_health",
    "science": "science_research",
}
AREA_PRIORITY = {
    "economy": 18,
    "geopolitics": 15,
    "science": 12,
    "health": 11,
}
POSITIVE_KEYWORDS = {
    "pl": (
        "inflacja", "ceny", "stopy", "rynek", "gospodarka", "budżet", "podatek",
        "reforma", "regulac", "decyzj", "plan", "wprowad", "wzrost", "spadek",
        "produkcj", "energia", "inwestyc", "badani", "naukow", "technolog",
        "zdrow", "lek", "klinicz", "nato", "sankcj", "wybor", "dyplomac",
    ),
    "en": (
        "inflation", "prices", "rates", "market", "economy", "budget", "tax",
        "reform", "regulat", "decision", "plan", "introduc", "growth", "fall",
        "production", "energy", "investment", "research", "scient", "technology",
        "health", "drug", "clinical", "nato", "sanction", "election", "diplomac",
    ),
}
NEGATIVE_KEYWORDS = {
    "pl": (
        "nie żyje", "zmarł", "pogrzeb", "wypadek", "utknęli", "akcja strażaków",
        "lotto", "eurojackpot", "kumulacja", "do wygrania", "oglądaj", "wywiad z",
        "gościu wydarzeń", "wynik meczu", "tabela ligi",
    ),
    "en": (
        "has died", "obituary", "funeral", "accident", "rescue operation",
        "lottery", "jackpot", "winning numbers", "watch interview", "interview with",
        "match result", "league table",
    ),
}


def _compact(value: Any, limit: int) -> str:
    return legacy.compact(value, limit)


def _normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _recent_source_urls(language: str, limit: int = 7) -> set[str]:
    used: set[str] = set()
    if not v3.HISTORY_DIR.exists():
        return used
    for path in sorted(v3.HISTORY_DIR.glob("*.json"), reverse=True)[:limit]:
        payload = v3.load_json(path, {})
        edition = payload.get(language) if isinstance(payload, dict) else None
        if not isinstance(edition, dict):
            continue
        for source in edition.get("sources") or []:
            if isinstance(source, dict):
                url = _compact(source.get("url"), 500)
                if url:
                    used.add(url)
    return used


def _parse_datetime(value: Any) -> datetime | None:
    text = _compact(value, 80)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _source_score(item: dict[str, Any], language: str, moment: datetime, used_urls: set[str]) -> int:
    text = _normal(" ".join(str(item.get(field) or "") for field in ("title", "summary", "category")))
    score = int(item.get("source_quality") or 50)
    score += AREA_PRIORITY.get(str(item.get("area") or ""), 0)
    score += sum(4 for keyword in POSITIVE_KEYWORDS[language] if keyword in text)
    score -= sum(35 for keyword in NEGATIVE_KEYWORDS[language] if keyword in text)
    if _compact(item.get("url"), 500) in used_urls:
        score -= 28
    published = _parse_datetime(item.get("published_at"))
    if published is not None:
        try:
            age_hours = (moment.astimezone(published.tzinfo) - published).total_seconds() / 3600
        except Exception:
            age_hours = 999
        if age_hours <= 30:
            score += 18
        elif age_hours <= 72:
            score += 8
        elif age_hours > 168:
            score -= 20
    return score


def _clean_topic(title: Any, limit: int = 64) -> str:
    value = _compact(title, limit + 20).strip(" .,:;!?–—-\"'„”")
    if len(value) <= limit:
        return value
    shortened = value[:limit].rsplit(" ", 1)[0].rstrip(" .,:;!?–—-")
    return shortened or value[:limit].rstrip()


def _select_sources(
    language: str,
    moment: datetime,
    pool: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    used_urls = _recent_source_urls(language)
    ranked = sorted(
        (dict(item) for item in pool if isinstance(item, dict) and item.get("area") in AREA_LABELS),
        key=lambda item: (
            _source_score(item, language, moment, used_urls),
            int(item.get("source_quality") or 0),
            _compact(item.get("published_at"), 80),
        ),
        reverse=True,
    )
    if not ranked:
        raise RuntimeError(f"no current {language} sources for deterministic AI Outlook fallback")

    area = str(ranked[0]["area"])
    selected: list[dict[str, Any]] = []
    seen_provenance: set[str] = set()
    for item in ranked:
        if item.get("area") != area:
            continue
        provenance_id = _compact(item.get("provenance_id"), 100)
        url = _compact(item.get("url"), 500)
        if not provenance_id or not url.startswith("https://") or provenance_id in seen_provenance:
            continue
        seen_provenance.add(provenance_id)
        selected.append(item)
        if len(selected) == 3:
            break
    if not selected:
        raise RuntimeError(f"no valid {language} fallback evidence")
    return area, selected, ranked


def _governance(area: str) -> dict[str, Any]:
    disclaimer_id = "medical_forecast_v1" if area == "health" else "general_forecast_v1"
    return {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "content_category": AREA_CONTENT_CATEGORY[area],
        "risk_class": "medical_information" if area == "health" else "general_forecast",
        "disclaimer_required": True,
        "disclaimer_id": disclaimer_id,
        "disclaimers": dict(DISCLAIMER_CATALOG[disclaimer_id]),
    }


def _fallback_copy(
    language: str,
    area: str,
    topic: str,
    summary: str,
    resolution_date: str,
) -> dict[str, str]:
    if language == "pl":
        title = _compact(f"{topic}: pojawią się kolejne mierzalne potwierdzenia", 100)
        thesis = _compact(
            f"W ciągu 3–6 miesięcy temat „{topic}” przyniesie co najmniej dwa nowe, "
            "publiczne i konkretne komunikaty, decyzje albo odczyty danych, które pozwolą "
            "ocenić, czy kierunek opisany w dzisiejszym źródle rzeczywiście się utrzymuje.",
            520,
        )
        rationale = _compact(
            f"Dzisiejszy punkt wyjścia brzmi: {summary} Model awaryjny wybiera świeże tematy "
            "o możliwym dalszym ciągu i wymaga późniejszej weryfikacji w publicznych źródłach.",
            480,
        )
        confirmation = _compact(
            f"Do {resolution_date} pojawią się co najmniej dwa niezależne komunikaty, decyzje "
            "lub odczyty danych odnoszące się bezpośrednio do tego samego zjawiska.",
            260,
        )
        invalidation = _compact(
            f"Do {resolution_date} pojawi się najwyżej jedna taka aktualizacja albo sprawa zostanie "
            "oficjalnie zamknięta bez dalszych mierzalnych skutków.",
            260,
        )
        resolution_summary = _compact(
            f"Prognoza jest trafna, gdy do {resolution_date} wystąpią minimum dwie niezależne, "
            "publicznie weryfikowalne aktualizacje dotyczące wskazanego tematu.",
            320,
        )
    else:
        title = _compact(f"{topic}: further measurable confirmation is likely", 100)
        thesis = _compact(
            f"Within 3–6 months, the issue described as “{topic}” will produce at least two new "
            "public and concrete updates, decisions or data releases that allow the direction in "
            "today's source to be tested rather than merely repeated.",
            520,
        )
        rationale = _compact(
            f"Today's starting signal is: {summary} The continuity fallback selects a fresh issue "
            "with a plausible follow-up path and requires later verification in public sources.",
            480,
        )
        confirmation = _compact(
            f"By {resolution_date}, at least two independent public updates, decisions or data "
            "releases directly addressing the same underlying issue are published.",
            260,
        )
        invalidation = _compact(
            f"By {resolution_date}, no more than one qualifying update appears, or the matter is "
            "officially closed without further measurable consequences.",
            260,
        )
        resolution_summary = _compact(
            f"The forecast resolves true if at least two independent, publicly verifiable updates "
            f"on the selected issue appear by {resolution_date}.",
            320,
        )
    return {
        "title": title,
        "thesis": thesis,
        "rationale": rationale,
        "confirmation": confirmation,
        "invalidation": invalidation,
        "resolution_summary": resolution_summary,
    }


def build_fallback(
    moment: datetime,
    pools: dict[str, list[dict[str, Any]]] | None = None,
    primary_error: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    local = moment.astimezone(legacy.WARSAW)
    publication_date = local.date().isoformat()
    resolution_date = (local.date() + timedelta(days=120)).isoformat()
    pl_date, en_date = legacy.date_labels(moment)
    date_labels = {"pl": pl_date, "en": en_date}
    horizons = {"pl": "3–6 miesięcy", "en": "3–6 months"}
    editions: dict[str, Any] = {}
    audit_editions: dict[str, Any] = {}

    for language in ("pl", "en"):
        pool = (pools or {}).get(language) if pools is not None else v3.source_items(language)
        if not isinstance(pool, list):
            raise RuntimeError(f"invalid {language} source pool")
        area, selected, ranked = _select_sources(language, moment, pool)
        lead = selected[0]
        topic = _clean_topic(lead.get("title"))
        summary = _compact(lead.get("summary") or lead.get("title"), 220)
        copy = _fallback_copy(language, area, topic, summary, resolution_date)
        governance = _governance(area)
        probability = min(70, 61 + len(selected) * 2 + int(lead.get("source_quality") or 50) // 30)
        source_rows = [
            {
                "name": _compact(item.get("source"), 100) or ("Źródło" if language == "pl" else "Source"),
                "url": _compact(item.get("url"), 500),
                "provenance_id": _compact(item.get("provenance_id"), 100),
                "source_language": language,
            }
            for item in selected
        ]
        digest = hashlib.sha256(source_rows[0]["url"].encode("utf-8")).hexdigest()[:10]
        resolution = {
            "schema_version": RESOLUTION_SCHEMA_VERSION,
            "metric": (
                "Liczba niezależnych publicznych aktualizacji dotyczących wybranego tematu"
                if language == "pl"
                else "Number of independent public follow-up updates on the selected issue"
            ),
            "comparison_operator": ">=",
            "threshold": 2.0,
            "unit": "updates",
            "baseline_date": publication_date,
            "baseline_value": 0.0,
            "data_source_for_verification": (
                "Bieżący kanoniczny feed BriefRooms oraz podlinkowane źródła pierwotne i redakcyjne"
                if language == "pl"
                else "Current BriefRooms canonical feed and the linked primary or editorial sources"
            ),
            "verification_url": f"https://briefrooms.com/data/news/{language}.json",
            "resolution_date": resolution_date,
            "geography": "Polska lub obszar wskazany w źródle" if language == "pl" else "Geography identified in the source",
            "status": "open",
        }
        edition = {
            "category": AREA_LABELS[area][language],
            **copy,
            "horizon": horizons[language],
            "forecast_id": f"{publication_date}-{language}-fallback-{digest}",
            "date_label": date_labels[language],
            "probability": probability,
            "source_language": language,
            "source_policy": v3.SOURCE_POLICY[language],
            "sources": source_rows,
            "selection_reason": (
                "Codzienny tryb ciągłości wybrał najwyżej oceniony świeży temat z kanonicznych źródeł PL."
                if language == "pl"
                else "The daily continuity path selected the highest-scoring fresh issue from canonical EN sources."
            ),
            "resolution_criteria": copy["resolution_summary"],
            "resolution": resolution,
            "governance": governance,
            "engine": {
                "version": ENGINE_VERSION,
                "edition_language": language,
                "weights_version": WEIGHTS_VERSION,
                "weights_snapshot": weights_snapshot(),
                "scoring_policy_version": SCORING_POLICY_VERSION,
                "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
                "resolution_schema_version": RESOLUTION_SCHEMA_VERSION,
                "governance_schema_version": GOVERNANCE_SCHEMA_VERSION,
                "selected_area": area,
                "selection_mode": "deterministic_daily_fallback",
                "engine_score": 66.0,
                "probability_method": "source_grounded_continuity_fallback_not_historically_calibrated",
                "statistical_layer_enabled": False,
                "model_layers": ["deterministic_rules", "source_grounded_fallback"],
                "candidate_count": len(ranked),
                "top_candidates": [
                    {
                        "candidate_id": f"source-{index + 1}",
                        "area": item.get("area"),
                        "title": _compact(item.get("title"), 100),
                        "engine_score": float(_source_score(item, language, moment, set())),
                        "passed_safety_gate": True,
                    }
                    for index, item in enumerate(ranked[:5])
                ],
                "fallback_version": FALLBACK_VERSION,
            },
            "disclaimer": governance["disclaimers"][language],
        }
        v3.validate_edition(language, edition)
        editions[language] = edition
        audit_editions[language] = {
            "mode": "deterministic_daily_fallback",
            "selected_area": area,
            "selected_sources": source_rows,
            "candidate_count": len(ranked),
        }

    payload = {
        "schema_version": v3.SCHEMA_VERSION,
        "date": publication_date,
        "generated_at": local.isoformat(timespec="seconds"),
        "edition_policy": "independent-per-language",
        "source_policy": dict(v3.SOURCE_POLICY),
        "generation_mode": "deterministic_daily_fallback",
        "fallback_version": FALLBACK_VERSION,
        "pl": editions["pl"],
        "en": editions["en"],
    }
    audit = {
        "schema_version": "ai-outlook-audit-v2",
        "date": publication_date,
        "generated_at": payload["generated_at"],
        "edition_policy": payload["edition_policy"],
        "source_policy": payload["source_policy"],
        "generation_mode": payload["generation_mode"],
        "fallback_version": FALLBACK_VERSION,
        "primary_error": _compact(primary_error, 1200),
        "editions": audit_editions,
    }
    v3.validate_payload(payload)
    return payload, audit


def _status(payload: dict[str, Any], mode: str, primary_error: str = "") -> dict[str, Any]:
    return {
        "schema_version": STATUS_SCHEMA,
        "status": "fresh",
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "timezone": "Europe/Warsaw",
        "mode": mode,
        "primary_error": _compact(primary_error, 1200),
        "pl_forecast_id": payload["pl"]["forecast_id"],
        "en_forecast_id": payload["en"]["forecast_id"],
        "freshness_policy": "payload date must equal the current Europe/Warsaw calendar date",
    }


def validate_current(*, require_today: bool) -> dict[str, Any]:
    payload = v3.load_json(v3.OUT, {})
    v3.validate_payload(payload)
    if require_today:
        today = datetime.now(legacy.WARSAW).date().isoformat()
        if payload.get("date") != today:
            raise RuntimeError(f"AI Outlook is stale: expected {today}, got {payload.get('date')!r}")
    for language in ("pl", "en"):
        if not str(payload[language].get("forecast_id") or "").startswith(payload["date"] + "-"):
            raise RuntimeError(f"{language} forecast_id does not match publication date")
    return payload


def publish_daily(*, force: bool, force_fallback: bool = False) -> dict[str, Any]:
    moment = datetime.now(legacy.WARSAW)
    today = moment.date().isoformat()
    current = v3.load_json(v3.OUT, {})

    if not force and current.get("date") == today:
        v3.validate_payload(current)
        _write_json(STATUS_PATH, _status(current, str(current.get("generation_mode") or "ai_primary")))
        return current

    primary_error = ""
    if not force_fallback:
        try:
            payload, audit = v3.generate(moment)
            payload["generation_mode"] = "ai_primary"
            v3.validate_payload(payload)
            v3.publish(payload, audit)
            _write_json(STATUS_PATH, _status(payload, "ai_primary"))
            return payload
        except Exception as exc:  # The continuity path must catch provider and output failures.
            primary_error = f"{type(exc).__name__}: {exc}"
            print(f"Primary AI Outlook generation failed; using deterministic fallback: {primary_error}")

    payload, audit = build_fallback(moment, primary_error=primary_error or "forced fallback")
    v3.publish(payload, audit)
    _write_json(STATUS_PATH, _status(payload, "deterministic_daily_fallback", primary_error))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-fallback", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--require-today", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        payload = validate_current(require_today=args.require_today)
        print(
            f"AI Outlook is fresh: {payload['date']} "
            f"PL={payload['pl']['forecast_id']} EN={payload['en']['forecast_id']}"
        )
        return 0

    payload = publish_daily(force=args.force, force_fallback=args.force_fallback)
    validate_current(require_today=True)
    print(
        f"Published daily AI Outlook for {payload['date']} in mode "
        f"{payload.get('generation_mode', 'ai_primary')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
