#!/usr/bin/env python3
"""Launch the canonical Gemini publisher with strict PL governance."""
from __future__ import annotations

from datetime import date
from urllib.parse import unquote, urlparse

import sitecustomize  # noqa: F401 - activates Gemini transport adapter
from ai_outlook_candidate_contract_patch import install as install_candidate_contract
from ai_outlook_source_topic_contract import install as install_source_topic_contract
from ai_outlook_final_contract_normalizer import install as install_final_normalizer

install_candidate_contract()
install_source_topic_contract()
install_final_normalizer()

import ai_outlook_pl_methodology as pl_methodology  # noqa: E402
import ai_outlook_pl_quality as pl_quality  # noqa: E402
import update_ai_outlook as legacy_contract  # noqa: E402

legacy_contract.ALLOWED_HORIZONS["pl"].update({"do 1 miesiąca", "1–3 miesiące"})
legacy_contract.ALLOWED_HORIZONS["en"].update({"up to 1 month", "1–3 months"})

# Candidate generation and final validation must see the same source evidence.
_original_source_payload = pl_methodology._source_payload


def _source_payload_with_provenance(items):
    payloads = _original_source_payload(items)
    if not isinstance(payloads, list):
        return payloads

    item_map = {
        item.get("id"): item
        for item in (items or [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    output = []
    for payload in payloads:
        if not isinstance(payload, dict):
            output.append(payload)
            continue
        row = dict(payload)
        source = item_map.get(row.get("id"), {})
        if isinstance(source, dict):
            row["url"] = str(source.get("url") or "")
            row["source_name"] = str(
                source.get("source") or source.get("source_name") or ""
            )
        output.append(row)
    return output


pl_methodology._source_payload = _source_payload_with_provenance

# Preserve title and summary in the public source object so the final quality
# gate validates the actual article evidence, not only the URL slug.
def _source_rows_with_evidence(winner, items):
    rows = []
    for item in pl_methodology._candidate_sources(winner, items):
        rows.append(
            {
                "name": pl_methodology._compact(item.get("source"), 100) or "Źródło",
                "url": pl_methodology._compact(item.get("url"), 500),
                "title": pl_methodology._compact(item.get("title"), 220),
                "summary": pl_methodology._compact(item.get("summary"), 700),
                "provenance_id": pl_methodology._compact(item.get("provenance_id"), 100),
                "source_language": "pl",
            }
        )
    return rows


pl_methodology._source_rows = _source_rows_with_evidence


def _validate_source_coherence_with_evidence(edition, metric):
    sources = edition.get("sources")
    if not isinstance(sources, list) or not sources:
        raise pl_quality.PolishOutlookQualityError(
            "PL Outlook requires at least one source"
        )

    provenance_ids = []
    metric_stems = pl_quality._stems(metric)
    public_stems = pl_quality._stems(
        " ".join(
            str(edition.get(field) or "")
            for field in ("title", "thesis", "rationale")
        )
    )
    topic_stems = metric_stems | public_stems

    for source in sources:
        if not isinstance(source, dict):
            raise pl_quality.PolishOutlookQualityError(
                "PL Outlook source is not an object"
            )
        if source.get("source_language") != "pl":
            raise pl_quality.PolishOutlookQualityError(
                "PL Outlook contains a non-Polish source"
            )
        provenance_id = str(source.get("provenance_id") or "").strip()
        if not provenance_id:
            raise pl_quality.PolishOutlookQualityError(
                "PL Outlook source has no provenance ID"
            )
        provenance_ids.append(provenance_id)

        url = str(source.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise pl_quality.PolishOutlookQualityError(
                "PL Outlook source URL must use HTTPS"
            )

        evidence_stems = pl_quality._stems(
            " ".join(
                str(source.get(field) or "")
                for field in ("title", "summary")
            )
        )
        if len(evidence_stems) >= 4:
            if topic_stems and not (evidence_stems & topic_stems):
                raise pl_quality.PolishOutlookQualityError(
                    "PL Outlook contains a source unrelated to the forecast outcome"
                )
            continue

        # Legacy/fallback sources without evidence text are checked by URL path.
        path_text = unquote(parsed.path.replace("-", " ").replace("_", " "))
        path_stems = pl_quality._stems(path_text)
        if len(path_stems) >= 5 and topic_stems and not (path_stems & topic_stems):
            raise pl_quality.PolishOutlookQualityError(
                "PL Outlook contains a source unrelated to the forecast outcome"
            )

    if len(set(provenance_ids)) != len(provenance_ids):
        raise pl_quality.PolishOutlookQualityError(
            "PL Outlook counts duplicated provenance as independent evidence"
        )


pl_quality._validate_source_coherence = _validate_source_coherence_with_evidence

_original_filter_candidates = pl_methodology.filter_candidates


def _canonical_horizon(publication_date: str, resolution_date: str) -> tuple[str, str]:
    published = date.fromisoformat(publication_date)
    resolved = date.fromisoformat(resolution_date)
    days = (resolved - published).days
    if days <= 0:
        raise pl_methodology.PolishMethodologyError(
            "Polish AI Outlook resolution date must be in the future"
        )
    if days <= 31:
        return "do 1 miesiąca", "up to 1 month"
    if days <= 92:
        return "1–3 miesiące", "1–3 months"
    if days <= 183:
        return "3–6 miesięcy", "3–6 months"
    if days <= 366:
        return "6–12 miesięcy", "6–12 months"
    return "1–3 lata", "1–3 years"


def _filter_candidates_with_canonical_horizon(candidates, items, publication_date):
    accepted, rejected = _original_filter_candidates(candidates, items, publication_date)
    normalized = []
    for candidate in accepted:
        row = dict(candidate)
        resolution = row.get("resolution") if isinstance(row.get("resolution"), dict) else {}
        try:
            horizon_pl, horizon_en = _canonical_horizon(
                publication_date,
                str(resolution.get("resolution_date") or ""),
            )
        except (ValueError, pl_methodology.PolishMethodologyError):
            rejected.append(
                {
                    "candidate_id": row.get("candidate_id"),
                    "title": str(row.get("title") or "")[:120],
                    "reason": "resolution_horizon_not_publishable",
                }
            )
            continue
        row["horizon_pl"] = horizon_pl
        row["horizon_en"] = horizon_en
        normalized.append(row)
    return normalized, rejected


pl_methodology.filter_candidates = _filter_candidates_with_canonical_horizon

import publish_ai_outlook_gemini as publisher  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(publisher.main())
