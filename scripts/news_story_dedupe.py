#!/usr/bin/env python3
"""Event-level deduplication shared by the Polish and English newsrooms."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

HISTORY_HOURS = 72
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY = ROOT / "data" / "news_story_history.json"

COMMON = {
    "about", "after", "against", "amid", "and", "are", "been", "being",
    "dla", "jest", "juz", "ktory", "maja", "oraz", "przed", "przez", "sie",
    "that", "the", "their", "this", "with", "wobec", "zostal", "zostala",
    "news", "says", "said", "report", "reports", "decyzja", "nowy", "nowa",
}
PL_SUFFIXES = (
    "owie", "ami", "ach", "ego", "emu", "owa", "owe", "owi", "owych",
    "anie", "enie", "om", "ow", "ie", "y", "a", "u",
)
EN_SUFFIXES = ("ingly", "ments", "ment", "ation", "ions", "ing", "ers", "ies", "ed", "es", "s")
ALIASES = {
    "ambasador": "envoy", "ambassador": "envoy", "envoys": "envoy",
    "unia": "eu", "european": "eu", "europejsk": "eu",
    "rosja": "russia", "rosji": "russia", "russi": "russia", "russian": "russia",
    "sankcj": "sanction", "sankcji": "sanction", "sanct": "sanction", "sanctions": "sanction",
    "pakiet": "package",
    "zatwierdz": "approve", "zatwierdzili": "approve", "przyj": "approve", "uzgodn": "approve",
    "agre": "approve", "agreed": "approve", "approv": "approve", "approved": "approve",
}
UPDATE_MARKERS = {
    "appeal", "court", "damage", "died", "effective", "final", "formal",
    "implemented", "injured", "investigation", "lawsuit", "podpis", "published",
    "ratified", "released", "resigned", "result", "skutek", "trial", "verdict",
    "wesz", "wyrok",
}


def _ascii(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


def _stem(token: str) -> str:
    if token.isdigit():
        return token
    for suffix in PL_SUFFIXES + EN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def event_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", _ascii(text))
    result = set()
    for token in tokens:
        ordinal = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", token)
        if ordinal:
            token = ordinal.group(1)
        if not (token.isdigit() or len(token) >= 3) or token in COMMON:
            continue
        stem = _stem(token)
        result.add(ALIASES.get(stem, stem))
    return result


def story_text(item: dict) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "summary_raw", "summary", "ai_summary", "full_brief")
    )


def event_signature(item: dict) -> str:
    """Stable language-neutral fingerprint stored in the rolling history."""
    return " ".join(sorted(event_tokens(story_text(item)))[:24])


def _canonical_url(item: dict) -> str:
    return str(item.get("link") or "").split("?", 1)[0].rstrip("/")


def _material_update(first: set[str], second: set[str]) -> bool:
    return bool((first ^ second) & UPDATE_MARKERS)


def same_story(first: dict, second: dict) -> bool:
    """True for the same event; false for a genuinely new event stage."""
    first_url, second_url = _canonical_url(first), _canonical_url(second)
    if first_url and first_url == second_url:
        return True

    a, b = event_tokens(story_text(first)), event_tokens(story_text(second))
    if not a or not b:
        return False
    shared = a & b
    overlap = len(shared) / min(len(a), len(b))
    jaccard = len(shared) / len(a | b)
    sequence = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    numbers_a = {token for token in a if token.isdigit()}
    numbers_b = {token for token in b if token.isdigit()}
    matching_number = bool(numbers_a & numbers_b)
    if numbers_a and numbers_b and not matching_number:
        return False
    duplicate = (
        sequence >= 0.76
        or (len(shared) >= 4 and overlap >= 0.52 and jaccard >= 0.30)
        # Two reports can describe the same transfer with very different
        # newsroom wording. Three shared event anchors (for example club,
        # action and player) are sufficient when they cover at least half of
        # the shorter report and a meaningful part of both reports.
        or (len(shared) >= 3 and overlap >= 0.50 and jaccard >= 0.30)
        or (matching_number and len(shared) >= 3 and overlap >= 0.42)
    )
    return duplicate and not _material_update(a, b)


def _published_at(item: dict, fallback: datetime) -> datetime:
    value = item.get("published_at") or item.get("timestamp")
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    parsed = item.get("published_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return fallback


def load_recent_history(path: Path = DEFAULT_HISTORY, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=HISTORY_HOURS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return [item for item in payload.get("stories", []) if _published_at(item, now) >= cutoff]


def deduplicate_sections(
    sections: dict[str, list[dict]],
    history: list[dict] | None = None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Keep one card per event across sections and the previous 72 hours."""
    history, selected, rejected = history or [], [], []
    result = {key: [] for key in sections}
    for section, items in sections.items():
        for item in items:
            duplicate = next(
                (
                    old for old in history + selected
                    if _canonical_url(item) != _canonical_url(old) and same_story(item, old)
                ),
                None,
            )
            if duplicate:
                rejected.append({
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "duplicate_of": duplicate.get("title", ""),
                    "reason": "same_event_within_72h",
                })
                continue
            result[section].append(item)
            selected.append(item)
    return result, rejected


def save_history(
    sections: dict[str, list[dict]],
    path: Path = DEFAULT_HISTORY,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    recent = load_recent_history(path, now)
    current = [{
        "title": item.get("title", ""),
        "summary": item.get("ai_summary") or item.get("summary") or "",
        "link": item.get("link", ""),
        "source": item.get("source", ""),
        "published_at": _published_at(item, now).isoformat(),
        "event_signature": event_signature(item),
    } for items in sections.values() for item in items]
    merged, _ = deduplicate_sections({"stories": recent + current})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"window_hours": HISTORY_HOURS, "stories": merged["stories"]},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assert_no_duplicate_stories(sections: dict[str, list[dict]]) -> None:
    """Fail publication if a same-event duplicate survives selection."""
    seen: list[dict] = []
    for items in sections.values():
        for item in items:
            for previous in seen:
                if same_story(item, previous):
                    raise RuntimeError(
                        "News quality audit blocked duplicate event: "
                        f"{previous.get('title', '')[:90]} <> {item.get('title', '')[:90]}"
                    )
            seen.append(item)


def audit_html(html: str) -> None:
    """Audit the actual rendered card set, not only generator objects."""
    cards = re.findall(r"<li\b[^>]*>(.*?)</li>", html, re.S | re.I)
    stories = []
    for card in cards:
        heading = re.search(r'class="news-text"[^>]*>(.*?)</span>', card, re.S | re.I)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", heading.group(1))).strip() if heading else ""
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card)).strip()
        if title:
            stories.append({"title": title, "summary": summary})
    assert_no_duplicate_stories({"rendered_html": stories})
