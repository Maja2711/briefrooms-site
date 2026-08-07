#!/usr/bin/env python3
"""Launch the canonical Gemini publisher with strict PL governance."""
from __future__ import annotations

from datetime import date

import sitecustomize  # noqa: F401 - activates Gemini transport adapter
from ai_outlook_candidate_contract_patch import install as install_candidate_contract
from ai_outlook_final_contract_normalizer import install as install_final_normalizer

install_candidate_contract()
install_final_normalizer()

import ai_outlook_pl_methodology as pl_methodology  # noqa: E402
import update_ai_outlook as legacy_contract  # noqa: E402

# AI Outlook may forecast a real scheduled event sooner than three months.
# These labels extend only the presentation contract; the exact deadline lives
# in resolution.resolution_date and remains the source of truth.
legacy_contract.ALLOWED_HORIZONS["pl"].update({"do 1 miesiąca", "1–3 miesiące"})
legacy_contract.ALLOWED_HORIZONS["en"].update({"up to 1 month", "1–3 months"})

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
