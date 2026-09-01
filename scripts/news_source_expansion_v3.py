#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

try:
    from .news_source_architecture import source_authority_bonus, source_profile
except ImportError:
    from news_source_architecture import source_authority_bonus, source_profile

SOURCE_EXPANSION_VERSION = "source-expansion-v3"
ORIGIN_DETECTION_VERSION = "source-origin-v1"
DISPATCH_DEDUPE_VERSION = "dispatch-dedupe-v1"
WIRE_ORIGINS = ("Reuters", "Associated Press", "PAP")

# Direct commercial access is configured at runtime. URLs may contain signed or
# customer-specific tokens, so no licensed endpoint is committed to the repository.
@dataclass(frozen=True)
class WireAdapter:
    canonical_source: str
    env_var: str
    mode: str
    documentation: str


WIRE_ADAPTERS = (
    WireAdapter(
        "Reuters",
        "BRIEFROOMS_REUTERS_FEEDS_JSON",
        "licensed_feed",
        "Reuters licensed/partner feed supplied by account configuration",
    ),
    WireAdapter(
        "Associated Press",
        "BRIEFROOMS_AP_FEEDS_JSON",
        "licensed_feed_or_media_api_bridge",
        "AP Media API or entitled RSS feed exposed through server-side configuration",
    ),
    WireAdapter(
        "PAP",
        "BRIEFROOMS_PAP_FEEDS_JSON",
        "subscriber_feed",
        "PAP subscriber service exposed through server-side configuration",
    ),
)


@dataclass(frozen=True)
class OriginEvidence:
    source: str
    confidence: float
    basis: str
    role: str


_STOPWORDS = {
    # English
    "about", "after", "against", "also", "among", "because", "been", "before",
    "being", "between", "could", "from", "have", "into", "more", "over", "said",
    "says", "that", "their", "there", "these", "they", "this", "through", "under",
    "with", "would", "will", "were", "when", "where", "which", "while", "than",
    # Polish
    "oraz", "który", "która", "które", "których", "jest", "było", "była", "były",
    "będzie", "mają", "przez", "przed", "między", "wobec", "według", "także", "tylko",
    "jego", "jej", "ich", "tego", "tej", "tych", "jako", "dla", "nad", "pod", "przy",
    "nie", "się", "sie", "czy", "już", "juz", "oraz", "może", "moze", "którym", "której",
}


_SPECIAL_FOLD = str.maketrans({"ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe"})


def _fold(value: Any) -> str:
    text = str(value or "").lower().translate(_SPECIAL_FOLD)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _normalize(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", _fold(value)))


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalize(value).split()
        if (len(token) >= 4 or token.isdigit()) and token not in _STOPWORDS
    }


def _story_text(story: Mapping[str, Any]) -> str:
    return " ".join(str(story.get(key) or "") for key in ("title", "summary"))


def _published_at(story: Mapping[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(story.get("published_at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _valid_feed_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.netloc:
        return None
    return raw


def _iter_section_urls(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        candidate = _valid_feed_url(value)
        if candidate:
            yield candidate
        return
    if isinstance(value, list):
        for item in value:
            candidate = _valid_feed_url(item)
            if candidate:
                yield candidate


def configured_wire_feeds(
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, dict[str, list[tuple[str, str]]]], dict[str, dict[str, Any]]]:
    """Parse optional licensed wire-feed configuration from environment JSON.

    Expected per-provider JSON shape:
      {"en": {"world-news": "https://...", "business": ["https://..."]},
       "pl": {"polityka": "https://..."}}

    Invalid optional configuration is reported, not fatal to the publisher.
    """
    env = environ or os.environ
    feeds: dict[str, dict[str, list[tuple[str, str]]]] = {"pl": {}, "en": {}}
    diagnostics: dict[str, dict[str, Any]] = {}

    for adapter in WIRE_ADAPTERS:
        raw = str(env.get(adapter.env_var, "") or "").strip()
        status: dict[str, Any] = {
            "source": adapter.canonical_source,
            "mode": adapter.mode,
            "env_var": adapter.env_var,
            "configured": False,
            "feed_count": 0,
            "error": None,
        }
        diagnostics[adapter.canonical_source] = status
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            status["error"] = f"invalid_json: {exc.msg}"
            continue
        if not isinstance(payload, dict):
            status["error"] = "invalid_shape: top-level object required"
            continue

        for lang, sections in payload.items():
            if lang not in feeds or not isinstance(sections, dict):
                continue
            for section_id, configured in sections.items():
                if not isinstance(section_id, str) or not section_id.strip():
                    continue
                for url in _iter_section_urls(configured):
                    feeds[lang].setdefault(section_id, []).append((adapter.canonical_source, url))
                    status["feed_count"] += 1
        status["configured"] = status["feed_count"] > 0
        if not status["configured"] and status["error"] is None:
            status["error"] = "no_valid_feed_urls"

    return feeds, diagnostics


def extend_config_with_wire_adapters(
    config: Any,
    lang: str,
    adapter_feeds: Mapping[str, Mapping[str, list[tuple[str, str]]]] | None = None,
) -> list[Any]:
    mapping = adapter_feeds or {}
    additions_by_section = mapping.get(lang, {}) if isinstance(mapping, Mapping) else {}
    extended: list[Any] = []
    for section_id, label, section_feeds in config:
        merged = list(section_feeds)
        seen = {(str(source), str(url)) for source, url in merged}
        for source, url in additions_by_section.get(section_id, []):
            pair = (str(source), str(url))
            if pair not in seen:
                merged.append(pair)
                seen.add(pair)
        extended.append((section_id, label, merged))
    return extended


# Attribution patterns deliberately require attribution language. A bare mention of
# "Reuters" in a story about the company itself is not treated as source evidence.
_ORIGIN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "Reuters": (
        re.compile(r"\b(?:according to|reported by|as reported by)\s+(?:the\s+)?reuters\b", re.I),
        re.compile(r"\breuters\s+(?:reported|reports|said|says|wrote|writes)\b", re.I),
        re.compile(r"\b(?:jak\s+)?(?:poda(?:l|la)|informuje|poinformowal|donosi|podaje)\s+reuters\b", re.I),
        re.compile(r"\bwedlug\s+reuters(?:a)?\b", re.I),
        re.compile(r"^\s*(?:\(|\[)?reuters(?:\)|\])?\s*[-—:]", re.I),
    ),
    "Associated Press": (
        re.compile(r"\b(?:according to|reported by|as reported by)\s+(?:the\s+)?associated press\b", re.I),
        re.compile(r"\bassociated press\s+(?:reported|reports|said|says|wrote|writes)\b", re.I),
        re.compile(r"\b(?:according to|reported by)\s+ap\b", re.I),
        re.compile(r"\bap\s+(?:reported|reports|said|says)\b", re.I),
        re.compile(r"\b(?:jak\s+)?(?:podaje|poinformowala|informuje|donosi)\s+(?:agencja\s+)?ap\b", re.I),
        re.compile(r"^\s*(?:\(|\[)?ap(?:\)|\])?\s*[-—:]", re.I),
    ),
    "PAP": (
        re.compile(r"\b(?:polska agencja prasowa|agencja pap)\s+(?:poinformowala|podala|informuje|donosi)\b", re.I),
        re.compile(r"\b(?:jak\s+)?(?:podaje|podala|poinformowala|informuje|donosi)\s+(?:agencja\s+)?pap\b", re.I),
        re.compile(r"\bwedlug\s+(?:agencji\s+)?pap\b", re.I),
        re.compile(r"^\s*(?:\(|\[)?pap(?:\)|\])?\s*[-—:]", re.I),
    ),
}


def detect_origin(story: Mapping[str, Any]) -> OriginEvidence | None:
    publisher = source_profile(story.get("source"))
    canonical = publisher.canonical_name
    if canonical in WIRE_ORIGINS:
        return OriginEvidence(canonical, 1.0, "direct_wire_source", "original")
    if publisher.parent in WIRE_ORIGINS:
        return OriginEvidence(str(publisher.parent), 0.98, "first_party_wire_desk", "original_desk")
    if publisher.tier == "primary":
        return OriginEvidence(canonical, 1.0, "primary_source", "original")

    folded = _fold(_story_text(story))
    for source, patterns in _ORIGIN_PATTERNS.items():
        if any(pattern.search(folded) for pattern in patterns):
            return OriginEvidence(source, 0.93, "explicit_wire_attribution", "republication")
    return None


def annotate_story_provenance(story: Mapping[str, Any]) -> dict[str, Any]:
    copy = dict(story)
    publisher = str(copy.get("source") or "unknown").strip() or "unknown"
    copy["publisher_source"] = publisher
    evidence = detect_origin(copy)
    if evidence is None:
        copy["origin_source"] = None
        copy["origin_confidence"] = 0.0
        copy["origin_basis"] = "unverified"
        copy["provenance_role"] = "publisher_unverified"
        return copy
    copy["origin_source"] = evidence.source
    copy["origin_confidence"] = evidence.confidence
    copy["origin_basis"] = evidence.basis
    copy["provenance_role"] = evidence.role
    return copy


def originality_bonus(story: Mapping[str, Any]) -> float:
    role = str(story.get("provenance_role") or "")
    if role == "original":
        return 34.0
    if role == "original_desk":
        return 28.0
    if role == "republication":
        return -14.0
    return 0.0


def _jaccard(first: set[str], second: set[str]) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _number_tokens(story: Mapping[str, Any]) -> set[str]:
    return {token for token in _normalize(_story_text(story)).split() if token.isdigit()}


def dispatch_similarity(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    first_title = _normalize(first.get("title"))
    second_title = _normalize(second.get("title"))
    if not first_title or not second_title:
        return 0.0
    title_sequence = SequenceMatcher(None, first_title, second_title).ratio()
    title_jaccard = _jaccard(_tokens(first.get("title")), _tokens(second.get("title")))
    body_jaccard = _jaccard(_tokens(_story_text(first)), _tokens(_story_text(second)))
    return max(title_sequence * 0.92, title_jaccard, body_jaccard * 0.94)


def same_dispatch(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    first_time = _published_at(first)
    second_time = _published_at(second)
    if first_time is not None and second_time is not None:
        if abs((first_time - second_time).total_seconds()) > 36 * 3600:
            return False

    first_origin = str(first.get("origin_source") or "")
    second_origin = str(second.get("origin_source") or "")
    if first_origin and second_origin and first_origin != second_origin:
        return False

    title_jaccard = _jaccard(_tokens(first.get("title")), _tokens(second.get("title")))
    body_jaccard = _jaccard(_tokens(_story_text(first)), _tokens(_story_text(second)))
    sequence = SequenceMatcher(
        None,
        _normalize(first.get("title")),
        _normalize(second.get("title")),
    ).ratio()
    same_origin = bool(first_origin and first_origin == second_origin)
    shared_numbers = _number_tokens(first) & _number_tokens(second)

    if same_origin:
        shared_body_tokens = _tokens(_story_text(first)) & _tokens(_story_text(second))
        return (
            title_jaccard >= 0.34
            or sequence >= 0.70
            or (body_jaccard >= 0.44 and len(shared_body_tokens) >= 5)
            or (title_jaccard >= 0.26 and body_jaccard >= 0.32 and bool(shared_numbers))
        )
    return title_jaccard >= 0.60 or sequence >= 0.86 or body_jaccard >= 0.56


def _representative_score(story: Mapping[str, Any]) -> tuple[float, float, float]:
    renderable = 1.0 if story.get("image") else 0.0
    role = str(story.get("provenance_role") or "")
    provenance = {
        "original": 5.0,
        "original_desk": 4.5,
        "publisher_unverified": 2.5,
        "republication": 1.0,
    }.get(role, 0.0)
    authority = source_authority_bonus(story.get("source"))
    published = _published_at(story)
    recency = published.timestamp() if published else 0.0
    return (renderable * 1000.0 + provenance * 100.0 + authority, recency, float(len(_tokens(story.get("title")))))


def deduplicate_dispatches(
    stories: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    annotated = [annotate_story_provenance(story) for story in stories]
    clusters: list[list[dict[str, Any]]] = []

    for story in sorted(annotated, key=lambda item: (_published_at(item) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True):
        matched: list[dict[str, Any]] | None = None
        for cluster in clusters:
            if any(same_dispatch(story, member) for member in cluster):
                matched = cluster
                break
        if matched is None:
            clusters.append([story])
        else:
            matched.append(story)

    representatives: list[dict[str, Any]] = []
    suppressed = 0
    wire_clusters = 0
    direct_original_wins = 0
    for cluster in clusters:
        representative = max(cluster, key=_representative_score)
        copy = dict(representative)
        suppressed_members = [member for member in cluster if member is not representative]
        suppressed += len(suppressed_members)
        origins = {str(member.get("origin_source") or "") for member in cluster if member.get("origin_source")}
        if len(cluster) > 1 and any(origin in WIRE_ORIGINS for origin in origins):
            wire_clusters += 1
        if len(cluster) > 1 and copy.get("provenance_role") in {"original", "original_desk"}:
            direct_original_wins += 1
        copy["dispatch_cluster_size"] = len(cluster)
        copy["dispatch_dedupe_version"] = DISPATCH_DEDUPE_VERSION
        copy["suppressed_publishers"] = sorted(
            {
                str(member.get("publisher_source") or member.get("source") or "unknown")
                for member in suppressed_members
            }
        )
        representatives.append(copy)

    diagnostics = {
        "raw_count": len(annotated),
        "deduplicated_count": len(representatives),
        "suppressed_count": suppressed,
        "wire_dispatch_clusters": wire_clusters,
        "direct_original_wins": direct_original_wins,
        "version": DISPATCH_DEDUPE_VERSION,
    }
    return representatives, diagnostics


def origin_mix(stories: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for story in stories:
        origin = str(story.get("origin_source") or "unverified")
        result[origin] = result.get(origin, 0) + 1
    return result


def public_expansion_policy(adapter_diagnostics: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    diagnostics = adapter_diagnostics or {}
    return {
        "version": SOURCE_EXPANSION_VERSION,
        "origin_detection_version": ORIGIN_DETECTION_VERSION,
        "dedupe_version": DISPATCH_DEDUPE_VERSION,
        "original_source_preference": True,
        "wire_origins": list(WIRE_ORIGINS),
        "adapter_status": {
            source: {
                "configured": bool(status.get("configured")),
                "feed_count": int(status.get("feed_count") or 0),
                "mode": status.get("mode"),
                "error": status.get("error"),
            }
            for source, status in diagnostics.items()
        },
        "adapter_contract": "Licensed endpoints are supplied only through repository secrets/environment configuration; no commercial wire URL or key is committed.",
    }
