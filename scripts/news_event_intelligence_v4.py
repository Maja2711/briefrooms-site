#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

try:
    from .news_source_architecture import source_profile, source_authority_bonus
    from .news_source_expansion_v3 import dispatch_similarity, same_dispatch
except ImportError:
    from news_source_architecture import source_profile, source_authority_bonus
    from news_source_expansion_v3 import dispatch_similarity, same_dispatch

EVENT_INTELLIGENCE_VERSION = "event-intelligence-v4"
EVIDENCE_MODEL_VERSION = "evidence-corroboration-v1"
CANONICAL_EVENT_VERSION = "canonical-event-v1"

_EVENT_STOPWORDS = {
    "about", "after", "against", "amid", "from", "into", "over", "said", "says",
    "that", "the", "their", "this", "with", "will", "would", "have", "has", "been",
    "oraz", "jest", "będzie", "bedzie", "przez", "przed", "wobec", "według", "wedlug",
    "tego", "tej", "tych", "który", "ktory", "która", "ktora", "które", "ktore",
    "nie", "się", "sie", "dla", "jako", "jego", "jej", "ich", "pod", "nad", "przy",
}


def _fold(value: Any) -> str:
    text = str(value or "").lower()
    replacements = str.maketrans("ąćęłńóśźż", "acelnoszz")
    return text.translate(replacements)


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _fold(value))
        if (len(token) >= 4 or token.isdigit()) and token not in _EVENT_STOPWORDS
    }


def _numbers(story: Mapping[str, Any]) -> set[str]:
    text = f"{story.get('title') or ''} {story.get('summary') or ''}"
    return set(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))


def _published_at(story: Mapping[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(story.get("published_at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evidence_root(story: Mapping[str, Any]) -> str:
    """Return the independent evidence lineage root.

    Five publishers repeating Reuters still count as one Reuters evidence path.
    A direct institution, Reuters/AP/PAP, or a genuinely independent publisher gets
    its own root.
    """
    origin = str(story.get("origin_source") or "").strip()
    confidence = float(story.get("origin_confidence") or 0.0)
    if origin and confidence >= 0.80:
        return source_profile(origin).canonical_name
    publisher = str(story.get("publisher_source") or story.get("source") or "unknown").strip()
    return source_profile(publisher).canonical_name


def _event_similarity(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    if same_dispatch(first, second):
        return 1.0
    first_time, second_time = _published_at(first), _published_at(second)
    if first_time and second_time and abs((first_time - second_time).total_seconds()) > 72 * 3600:
        return 0.0
    title_a = _tokens(first.get("title"))
    title_b = _tokens(second.get("title"))
    body_a = _tokens(f"{first.get('title') or ''} {first.get('summary') or ''}")
    body_b = _tokens(f"{second.get('title') or ''} {second.get('summary') or ''}")
    if not title_a or not title_b:
        return 0.0
    title_j = len(title_a & title_b) / max(1, len(title_a | title_b))
    body_j = len(body_a & body_b) / max(1, len(body_a | body_b))
    shared_numbers = bool(_numbers(first) & _numbers(second))
    score = max(dispatch_similarity(first, second), title_j * 0.95 + body_j * 0.35)
    if shared_numbers:
        score += 0.08
    return min(score, 1.0)


def same_event(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Broader than dispatch dedupe: cluster independent coverage of one event."""
    similarity = _event_similarity(first, second)
    roots_differ = evidence_root(first) != evidence_root(second)
    if roots_differ:
        return similarity >= 0.49
    return similarity >= 0.58


def _event_key(stories: Iterable[Mapping[str, Any]]) -> str:
    rows = list(stories)
    token_counts: dict[str, int] = {}
    for story in rows:
        for token in _tokens(story.get("title")):
            token_counts[token] = token_counts.get(token, 0) + 1
    ranked = sorted(token_counts, key=lambda token: (-token_counts[token], token))[:8]
    roots = sorted({evidence_root(story) for story in rows})
    day = min((_published_at(story) for story in rows if _published_at(story)), default=None)
    day_key = day.strftime("%Y-%m-%d") if day else "undated"
    return "|".join([day_key, " ".join(ranked), ",".join(roots[:3])])


def canonical_event_id(stories: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256(_event_key(stories).encode("utf-8")).hexdigest()[:20]
    return f"evt_{digest}"


def extract_claims(story: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Cheap deterministic claim atoms used for evidence diagnostics.

    We deliberately avoid pretending to perform full semantic extraction. Numeric
    facts and the normalized headline proposition are enough for a reliable v1
    evidence graph and can later be replaced by a richer semantic extractor.
    """
    title = " ".join(str(story.get("title") or "").split())
    claims: list[dict[str, Any]] = []
    if title:
        claims.append({"type": "headline_proposition", "value": title[:240]})
    for number in sorted(_numbers(story))[:8]:
        claims.append({"type": "numeric_fact", "value": number})
    return claims


def _root_weight(root: str) -> float:
    profile = source_profile(root)
    return {
        "primary": 1.00,
        "wire": 0.92,
        "premium": 0.78,
        "quality": 0.64,
        "broad": 0.45,
        "unknown": 0.35,
    }.get(profile.tier, 0.35)


def corroboration(stories: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(stories)
    roots: dict[str, dict[str, Any]] = {}
    for story in rows:
        root = evidence_root(story)
        item = roots.setdefault(
            root,
            {
                "root": root,
                "tier": source_profile(root).tier,
                "publishers": set(),
                "direct": False,
            },
        )
        item["publishers"].add(str(story.get("publisher_source") or story.get("source") or "unknown"))
        if str(story.get("provenance_role") or "") in {"original", "original_desk"}:
            item["direct"] = True

    weights = sorted((_root_weight(root) for root in roots), reverse=True)
    if not weights:
        score = 0.0
    else:
        # First path establishes the report. Extra independent roots add confidence
        # with diminishing returns. A primary/direct path receives a modest bonus.
        score = 35.0 * weights[0]
        for index, weight in enumerate(weights[1:5], start=1):
            score += (25.0 / index) * weight
        if any(source_profile(root).tier == "primary" for root in roots):
            score += 12.0
        elif any(data["direct"] for data in roots.values()):
            score += 6.0
        score = min(100.0, score)

    independent = len(roots)
    if independent >= 3 and score >= 65:
        status = "strong"
    elif independent >= 2 and score >= 45:
        status = "corroborated"
    elif independent == 1:
        status = "single_lineage"
    else:
        status = "unverified"

    return {
        "score": round(score, 1),
        "status": status,
        "independent_evidence_paths": independent,
        "evidence_roots": [
            {
                "root": root,
                "tier": data["tier"],
                "publishers": sorted(data["publishers"]),
                "direct": bool(data["direct"]),
            }
            for root, data in sorted(roots.items())
        ],
    }


def _representative_score(story: Mapping[str, Any]) -> tuple[float, float, float, float]:
    role = str(story.get("provenance_role") or "")
    original = {"original": 3.0, "original_desk": 2.7, "publisher_unverified": 1.5, "republication": 0.5}.get(role, 0.0)
    image = 1.0 if story.get("image") else 0.0
    authority = source_authority_bonus(story.get("source"))
    published = _published_at(story)
    recency = published.timestamp() if published else 0.0
    return (image, original, authority, recency)


def cluster_events(stories: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [dict(story) for story in stories]
    clusters: list[list[dict[str, Any]]] = []
    for story in rows:
        target: list[dict[str, Any]] | None = None
        best = 0.0
        for cluster in clusters:
            similarity = max(_event_similarity(story, other) for other in cluster)
            if similarity > best and any(same_event(story, other) for other in cluster):
                best = similarity
                target = cluster
        if target is None:
            clusters.append([story])
        else:
            target.append(story)

    representatives: list[dict[str, Any]] = []
    multi_source_events = 0
    corroborated_events = 0
    suppressed = 0
    for cluster in clusters:
        event_id = canonical_event_id(cluster)
        evidence = corroboration(cluster)
        if evidence["independent_evidence_paths"] >= 2:
            multi_source_events += 1
        if evidence["status"] in {"corroborated", "strong"}:
            corroborated_events += 1
        representative = max(cluster, key=_representative_score)
        copy = dict(representative)
        copy.update(
            {
                "canonical_event_id": event_id,
                "canonical_event_version": CANONICAL_EVENT_VERSION,
                "event_cluster_size": len(cluster),
                "event_publishers": sorted({str(item.get("publisher_source") or item.get("source") or "unknown") for item in cluster}),
                "corroboration_score": evidence["score"],
                "corroboration_status": evidence["status"],
                "independent_evidence_paths": evidence["independent_evidence_paths"],
                "evidence_roots": evidence["evidence_roots"],
                "claims": extract_claims(representative),
                "event_intelligence_version": EVENT_INTELLIGENCE_VERSION,
                "evidence_model_version": EVIDENCE_MODEL_VERSION,
            }
        )
        representatives.append(copy)
        suppressed += max(0, len(cluster) - 1)

    diagnostics = {
        "raw_story_count": len(rows),
        "canonical_event_count": len(clusters),
        "event_duplicates_suppressed": suppressed,
        "multi_source_events": multi_source_events,
        "corroborated_events": corroborated_events,
    }
    return representatives, diagnostics


def corroboration_bonus(story: Mapping[str, Any]) -> float:
    score = float(story.get("corroboration_score") or 0.0)
    paths = int(story.get("independent_evidence_paths") or 0)
    return min(32.0, score * 0.22 + max(0, paths - 1) * 4.0)


def public_event_policy() -> dict[str, Any]:
    return {
        "version": EVENT_INTELLIGENCE_VERSION,
        "canonical_event_version": CANONICAL_EVENT_VERSION,
        "evidence_model_version": EVIDENCE_MODEL_VERSION,
        "principles": [
            "One real-world event should occupy at most one card per section.",
            "Multiple republications of one wire dispatch count as one evidence lineage.",
            "Independent primary/wire/editorial roots increase corroboration.",
            "Corroboration improves ranking but never bypasses editorial quality filters.",
        ],
    }
