#!/usr/bin/env python3
"""CT-01 / IN-01 editorial guardian: evidence-based, bounded duplicate repair.

No independent publisher, trading authority, network calls or model-generated facts.
A preview becomes redundant once a source-backed report of that interview exists.
The general audit also checks exact/canonical identities and anchored paraphrases.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, parse_qsl

try:
    from .news_semantic_event import same_specific_event
    from .news_story_dedupe import same_story, event_tokens
except ImportError:
    from news_semantic_event import same_specific_event
    from news_story_dedupe import same_story, event_tokens

VERSION = "news-editorial-guardian-v1"
MAX_INTERVIEW_HOURS = 18
MAX_REPORTED_EVENT_HOURS = 36
QUOTES = r'["„“”«»]'
PREVIEW = re.compile(
    r"\bgosciem\b.{0,100}\bbedzie\b|\bbedzie gosciem\b|"
    r"\bogladaj od\b|\btransmisja\b.{0,100}\bod \d{1,2}[:.]\d{2}\b|"
    r"\bwill (?:be (?:a |the )?guest|appear|join)\b|\btune in\b|\bwatch live at\b"
)
ATTRIBUTION = re.compile(
    r"\b(?:mowil|mowila|powiedzial|powiedziala|pytal|pytala|"
    r"stwierdzil|stwierdzila|ocenil|ocenila|podkreslil|podkreslila|"
    r"przyznal|przyznala|said|told|asked|argued|explained|stated)\b"
)
PROGRAM = re.compile(
    r'(?:\bw programie\s+|\bon (?:the )?(?:program(?:me)?|show)\s+|\bin (?:the )?(?:program(?:me)?|show)\s+)'
    + QUOTES + r'([^"„“”«»]{2,70})' + QUOTES, re.I
)
NAME = re.compile(
    r"\b([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż'-]{2,}"
    r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż'-]{2,}){1,2})\b"
)
NAME_SKIP = {"minister", "prezydent", "premier", "president", "prime", "gość", "gosc",
             "program", "watch", "interview", "live", "polsat", "news", "today"}
TRACKING = {"source", "ref", "fbclid", "gclid", "mc_cid", "mc_eid"}


def plain(value: Any) -> str:
    s = unicodedata.normalize("NFKD", str(value or "").casefold())
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))
                  .replace("ł", "l")).strip()


def text(row: Mapping[str, Any]) -> str:
    return plain(" ".join(str(row.get(k) or "") for k in ("title", "summary", "details")))


def timestamp(row: Mapping[str, Any]) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(row.get("published_at") or "").replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def identity(row: Mapping[str, Any]) -> str:
    try:
        u = urlsplit(str(row.get("link") or ""))
        if u.scheme not in {"http", "https"} or not u.hostname:
            return ""
        query = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                       if k.casefold() not in TRACKING and not k.casefold().startswith("utm_"))
        from urllib.parse import urlencode
        return u.hostname.lower().removeprefix("www.") + u.path.rstrip("/") + (
            "?" + urlencode(query) if query else "")
    except (ValueError, TypeError):
        return ""


def _contains(haystack: str, needle: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack))


def preview_frame(row: Mapping[str, Any]) -> tuple[str, str] | None:
    title = str(row.get("title") or "")
    program = PROGRAM.search(title)
    if not program or not PREVIEW.search(text(row)) or ATTRIBUTION.search(text(row)):
        return None
    name_candidates = NAME.findall(title[:program.start()])
    for candidate in reversed(name_candidates):
        guest = plain(candidate)
        parts = guest.split()
        if len(parts) >= 2 and not (set(parts) & NAME_SKIP):
            return guest, plain(program.group(1))
    return None


def interview_relation(first: Mapping[str, Any], second: Mapping[str, Any]) -> str:
    """Return first_preview / second_preview, only when the later report is grounded."""
    for preview, report, answer in ((first, second, "first_preview"),
                                    (second, first, "second_preview")):
        frame = preview_frame(preview)
        t1, t2 = timestamp(preview), timestamp(report)
        if not frame or not t1 or not t2 or preview_frame(report):
            continue
        seconds = (t2 - t1).total_seconds()
        if not 0 <= seconds <= MAX_INTERVIEW_HOURS * 3600:
            continue
        guest, program = frame
        body = text(report)
        if (_contains(body, guest) and _contains(body, program)
                and ATTRIBUTION.search(body)
                and report.get("image") and identity(report)):
            return answer
    return ""


def _close(a: Mapping[str, Any], b: Mapping[str, Any], hours: int) -> bool:
    x, y = timestamp(a), timestamp(b)
    return bool(x and y and abs((x - y).total_seconds()) <= hours * 3600)


def _anchor_names(row: Mapping[str, Any]) -> set[str]:
    raw = str(row.get("title") or "") + " " + str(row.get("summary") or "")
    return {plain(n) for n in NAME.findall(raw)
            if len(plain(n).split()) >= 2 and not set(plain(n).split()) & NAME_SKIP}


def pair_reason(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    """Conservative confirmed duplicate, not merely a related subject."""
    ida, idb = identity(a), identity(b)
    if ida and ida == idb:
        return "same_source_article"
    ea, eb = a.get("canonical_event_id"), b.get("canonical_event_id")
    if ea and ea == eb:
        return "same_canonical_event"
    if interview_relation(a, b):
        return "interview_preview_superseded"
    if not _close(a, b, MAX_REPORTED_EVENT_HOURS):
        return ""
    if same_specific_event(a, b):
        return "same_factual_event"
    # Independent corroboration of a paraphrase: common named anchor, strong
    # shared headline content AND existing full-text matcher. Avoid merging
    # different decisions by the same person on the same day.
    ta, tb = event_tokens(str(a.get("title") or "")), event_tokens(str(b.get("title") or ""))
    ba, bb = event_tokens(text(a)), event_tokens(text(b))
    shared_title, shared_body = ta & tb, ba & bb
    numbers_a = {v for v in ta if v.isdigit()}
    numbers_b = {v for v in tb if v.isdigit()}
    if numbers_a and numbers_b and numbers_a.isdisjoint(numbers_b):
        return ""
    if (len(shared_title) >= 4 and len(shared_title) / max(1, len(ta | tb)) >= .48
            and len(shared_body) >= 6
            and len(shared_body) / max(1, len(ba | bb)) >= .42
            and bool(_anchor_names(a) & _anchor_names(b))
            and same_story(dict(a), dict(b))):
        return "anchored_paraphrase"
    return ""


def _rank(row: Mapping[str, Any]) -> tuple:
    # Content, not publisher popularity. Preserve existing rank when tied.
    return (not bool(preview_frame(row)), bool(row.get("image")),
            min(len(str(row.get("summary") or "")), 600),
            timestamp(row) or datetime.min.replace(tzinfo=timezone.utc))


def _finding(a: Mapping[str, Any], b: Mapping[str, Any], reason: str) -> dict:
    return {"reason": reason, "kept_title": str(a.get("title") or ""),
            "removed_title": str(b.get("title") or ""),
            "kept_link": str(a.get("link") or ""), "removed_link": str(b.get("link") or "")}


def suppress_previews(groups: Iterable[list[dict]]) -> tuple[set[str], list[dict]]:
    """Find superseded previews in qualified pools BEFORE existing ranking."""
    rows = [row for group in groups for row in group if isinstance(row, dict)]
    previews = [row for row in rows if preview_frame(row) and identity(row)]
    removed: set[str] = set()
    findings: list[dict] = []
    for preview in previews:
        reports = [row for row in rows if interview_relation(preview, row) == "first_preview"]
        if not reports or identity(preview) in removed:
            continue
        report = max(reports, key=_rank)
        removed.add(identity(preview))
        findings.append(_finding(report, preview, "interview_preview_superseded"))
    return removed, findings


def repair_sections(sections: Mapping[str, list[dict]]) -> tuple[dict[str, list[dict]], list[dict]]:
    """One event across desks. Repair only confirmed matches, keep original order."""
    rows = [(section, i, row) for section, group in sections.items()
            for i, row in enumerate(group) if isinstance(row, dict)]
    excluded, findings = suppress_previews(sections.values())
    kept: list[tuple[str, int, dict]] = []
    for section, i, row in rows:
        if identity(row) in excluded:
            continue
        match = next(((old, pair_reason(old[2], row)) for old in kept
                      if pair_reason(old[2], row)), None)
        if match is None:
            kept.append((section, i, row))
            continue
        old, reason = match
        # Preserve the original desk owner for ordinary duplicate articles;
        # interview previews were explicitly replaced by the actual report.
        findings.append(_finding(old[2], row, reason))
    result = {section: [] for section in sections}
    for section, _, row in kept:
        result[section].append(row)
    return result, findings


def audit_payload(payload: Mapping[str, Any]) -> dict:
    """Read-only independent audit of each actual reader-visible collection."""
    sections = payload.get("sections") or {}
    scopes = {
        "sections": [row for group in sections.values() if isinstance(group, list)
                     for row in group if isinstance(row, dict)],
        "homepage_plus_reserve": [row for key in ("home", "home_reserve")
                                 for row in (payload.get(key) or []) if isinstance(row, dict)],
    }
    findings = []
    reviewed = 0
    for scope, rows in scopes.items():
        for i, row in enumerate(rows):
            for old in rows[:i]:
                reviewed += 1
                reason = pair_reason(old, row)
                if reason:
                    relation = interview_relation(old, row)
                    first, second = (row, old) if relation == "first_preview" else (old, row)
                    findings.append({"scope": scope, **_finding(first, second, reason)})
    # A reserve preview can also be obsolete because the report exists in a
    # desk but is not selected for home. Identical intentional home/desk copies
    # do not count as duplicates, only a superseded preview.
    all_rows = scopes["sections"] + scopes["homepage_plus_reserve"]
    _, superseded = suppress_previews([all_rows])
    known = {(f["removed_link"], f["reason"]) for f in findings}
    for finding in superseded:
        if (finding["removed_link"], finding["reason"]) not in known:
            findings.append({"scope": "all_public_surfaces", **finding})
    return {"version": VERSION, "marker": payload.get("marker"),
            "status": "failed" if findings else "clean",
            "pairs_reviewed": reviewed, "duplicate_count": len(findings),
            "findings": findings}
