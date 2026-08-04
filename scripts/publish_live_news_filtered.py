#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from . import publish_live_news as base
    from .news_quality import POLICY_VERSION, evaluate_story, public_policy
except ImportError:
    import publish_live_news as base
    from news_quality import POLICY_VERSION, evaluate_story, public_policy

ROOT = Path(__file__).resolve().parents[1]

_original_fetch_feed = base.fetch_feed
_original_load_previous = base.load_previous
_original_build_language = base.build_language
_original_validate = base.validate


def _filter_stories(stories: list[dict[str, Any]], context: str) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for story in stories:
        decision = evaluate_story(story.get("title"), story.get("summary"))
        if decision.accepted:
            accepted.append(story)
            continue
        print(f"EDITORIAL_FILTER {context} reason={decision.reason} title={story.get('title')!r}")
    return accepted


def fetch_feed(source: str, feed_url: str, section_id: str, now: Any) -> tuple[list[dict[str, Any]], str | None]:
    stories, error = _original_fetch_feed(source, feed_url, section_id, now)
    return _filter_stories(stories, f"fresh/{section_id}/{source}"), error


def load_previous(lang: str) -> dict[str, Any]:
    payload = _original_load_previous(lang)
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    for section_id, stories in list(sections.items()):
        if isinstance(stories, list):
            sections[section_id] = _filter_stories(stories, f"carried/{lang}/{section_id}")
    payload["sections"] = sections
    return payload


def build_language(lang: str, config: Any, marker: str, now: Any) -> dict[str, Any]:
    payload = _original_build_language(lang, config, marker, now)
    payload["editorial_policy"] = public_policy()
    payload.setdefault("health", {})["editorial_filter"] = {
        "status": "active",
        "version": POLICY_VERSION,
    }
    return payload


def validate(max_age_minutes: int = 30) -> None:
    _original_validate(max_age_minutes)
    for lang in ("pl", "en"):
        path = ROOT / "data" / "news" / f"{lang}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = payload.get("editorial_policy") or {}
        if policy.get("version") != POLICY_VERSION:
            raise RuntimeError(f"{lang} editorial policy missing or outdated")
        for section_id, stories in payload.get("sections", {}).items():
            for story in stories:
                decision = evaluate_story(story.get("title"), story.get("summary"))
                if not decision.accepted:
                    raise RuntimeError(
                        f"{lang}/{section_id} contains blocked story ({decision.reason}): {story.get('title')}"
                    )


base.fetch_feed = fetch_feed
base.load_previous = load_previous
base.build_language = build_language
base.validate = validate


if __name__ == "__main__":
    base.main()
