#!/usr/bin/env python3
"""Conservative same-event detection for PL and EN news feeds.

Different publishers frequently describe one event with different wording.
URL and near-identical-title checks do not catch that case, so this module
compares normalized event anchors (entities, numbers and meaningful words).
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

COMMON = {
    "about", "after", "against", "amid", "and", "are", "been", "being",
    "dla", "jest", "juz", "ktory", "maja", "oraz", "przed", "przez", "sie",
    "that", "the", "their", "this", "with", "wobec", "zostal", "zostala",
    "news", "says", "said", "report", "reports", "decyzja", "nowy", "nowa",
}

PL_SUFFIXES = (
    "owie", "ami", "ach", "ego", "emu", "owa", "owe", "owi", "owych",
    "anie", "enie", "ami", "om", "ow", "ie", "y", "a", "u",
)
EN_SUFFIXES = ("ingly", "ments", "ment", "ation", "ions", "ing", "ers", "ies", "ed", "es", "s")


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


def event_tokens(title: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", _ascii(title))
    return {
        _stem(token)
        for token in raw
        if (token.isdigit() or len(token) >= 3) and token not in COMMON
    }


def same_story(first: dict, second: dict) -> bool:
    """Return True only when two cards very likely describe the same event."""
    first_url = str(first.get("link") or "").split("?", 1)[0].rstrip("/")
    second_url = str(second.get("link") or "").split("?", 1)[0].rstrip("/")
    if first_url and first_url == second_url:
        return True

    first_title = str(first.get("title") or "")
    second_title = str(second.get("title") or "")
    a = event_tokens(first_title)
    b = event_tokens(second_title)
    if not a or not b:
        return False

    shared = a & b
    overlap = len(shared) / min(len(a), len(b))
    union = len(a | b)
    jaccard = len(shared) / union if union else 0.0
    sequence = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    numbers_a = {token for token in a if token.isdigit()}
    numbers_b = {token for token in b if token.isdigit()}
    matching_number = bool(numbers_a & numbers_b)
    if numbers_a and numbers_b and not matching_number:
        return False

    return (
        sequence >= 0.78
        or (len(shared) >= 4 and overlap >= 0.55 and jaccard >= 0.34)
        or (matching_number and len(shared) >= 3 and overlap >= 0.45)
    )


def assert_no_duplicate_stories(sections: dict[str, list[dict]]) -> None:
    """Fail publication if a same-event duplicate survives the selection pass."""
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
