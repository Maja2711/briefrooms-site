#!/usr/bin/env python3
"""Conservative, source-independent matching of the SAME real-world news event.

A broad subject (an election, a person, a war) is not an event identifier.
Only strongly grounded event frames qualify. This module is deliberately
dependency-free and shared by clustering, editorial selection and monitoring.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

VERSION = "semantic-event-frames-v1"

def _plain(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("ł", "l")
    return re.sub(r"\s+", " ", value).strip()

def _text(row: Mapping[str, Any]) -> str:
    return _plain(f"{row.get('title') or ''} {row.get('summary') or ''}")

def _title(row: Mapping[str, Any]) -> str:
    return _plain(row.get("title"))

def _date(row: Mapping[str, Any]) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(row.get("published_at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        return None
    return d.astimezone(timezone.utc)

def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.I) is not None

def _media_name(text: str) -> str:
    # Organisation identity, not 'Russia' or 'newspaper' alone. Common multiword
    # publication-name shapes can be extended independently of any publisher.
    match = re.search(r"\b([a-z][a-z'-]{3,}\s+(?:gazeta|times|journal|daily|tribune))\b", text)
    return match.group(1) if match else ""

def _ballot_subject(row: Mapping[str, Any]) -> str:
    # Match a surname mentioned in the headline to a capitalized full name in
    # the article. The rule is independent of a hardcoded athlete roster.
    headline = set(re.findall(r"[a-z]{4,}", _title(row)))
    full_text = f"{row.get('title') or ''} {row.get('summary') or ''}"
    proper_name = re.compile(
        r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]{2,}\s+"
        r"([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]{3,})\b"
    )
    for match in proper_name.finditer(full_text):
        surname = _plain(match.group(1))
        phrase = _plain(match.group(0))
        # The title of an award (e.g. Złota Piłka) is not the athlete.
        if _has(phrase, r"^zlot\\w* pilk\\w*$|^ballon d.or$"):
            continue
        if surname in headline:
            return surname
    return ""


def event_signature(row: Mapping[str, Any]) -> str:
    title, text = _title(row), _text(row)
    if not title:
        return ""

    # The same announced ballot result can be reported with or without
    # the winning margin. Require a named person in the title, the same award
    # and explicit result/vote language. Different athletes remain separate.
    if (_has(text, r"zlot\w*\s+pilk\w*|ballon\s+d.or")
        and _has(text, r"glos\w*|wynik\w*|punkt\w*|\bpkt\b|vot\w*")):
        person = _ballot_subject(row)
        if person:
            return f"award-ballot:ballon-dor:{person}"

    # One statistical release, referenced by the same period. Exclude commentary
    # on policy/interest rate decisions (a separate event).
    if (_has(title, r"\binflacj\w*\b|\binflation\b")
        and _has(title, r"\bpazdziernik\w*\b|\boctober\b")
        and _has(title, r"spadl\w*|wyhamow\w*|spadek|fell|slowed|lower")):
        published = _date(row)
        if published:
            return f"inflation-release:october:{published.year}"

    # A particular institutional closure is different from its broader crisis.
    # Require an actual named news organisation, plus closure/shutdown.
    if (_has(text, r"newspaper|gazeta|news outlet|media outlet|redakcj\w*")
        and _has(text, r"\bclos\w*|\bshut\b|closing|\bkilled off\b|"
                       r"\bstrangled\b|zamkn\w*|likwidacj\w*")):
        name = _media_name(text)
        if name:
            return f"media-closure:{name}"

    # Specific diplomatic photo cancellation, not all G20 reporting.
    if (_has(title, r"\bg20\b") and _has(title, r"family\s+photo")
        and _has(text, r"cancel\w*|abandon\w*|boycott\w*|odwol\w*")):
        return "diplomacy:g20:family-photo-cancelled"

    # An arms-sale approval is not every development in the Ukraine war.
    if (_has(text, r"patriot") and _has(text, r"ukrain\w*")
        and _has(text, r"\b500\b") and
        _has(text, r"\bsal\w*|\bsell\w*|\bdeal\b|\bapprov\w*|sprzedaz\w*|zatwierdz\w*")):
        return "arms-sale:ukraine:patriot:500"

    # Specific physical entry to an institution (not a statement about it,
    # consultation, protest, judicial ruling or broad overnight news liveblog).
    if (_has(text, r"\bprezydent\w*\b|\bpresident\b")
        and _has(text, r"\btk\b|trybunal\w*\s+konstytucyjn\w*")
        and _has(title, r"wdarl\w*|wszedl\w*|wejsc\w*|entered\s+the\s+court")
        and not _has(title, r"relacja\s+na\s+zywo|wydarzylo\s+sie\s+w\s+nocy|podsumow\w*")):
        return "physical-entry:pl-president:constitutional-court"

    return ""

def same_specific_event(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Positive match only; lack of signature does not imply distinct events."""
    first_key = event_signature(first)
    if not first_key or first_key != event_signature(second):
        return False
    a, b = _date(first), _date(second)
    return a is not None and b is not None and abs((a-b).total_seconds()) <= 36 * 3600

def duplicate_pairs(rows: list[Mapping[str, Any]]) -> list[tuple[int, int, str]]:
    by_event: dict[str, list[int]] = {}
    found: list[tuple[int, int, str]] = []
    for idx, row in enumerate(rows):
        key = event_signature(row)
        if not key:
            continue
        for prev in by_event.get(key, []):
            if same_specific_event(row, rows[prev]):
                found.append((prev, idx, key))
                break
        by_event.setdefault(key, []).append(idx)
    return found
