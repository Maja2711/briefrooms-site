#!/usr/bin/env python3
"""Diplomacy coverage extensions for production Investment Event Intelligence.

This module widens recall for negotiated de-escalation without changing the core
collector architecture. It is intentionally idempotent and is installed by the
precision layer before collection.
"""
from __future__ import annotations

from typing import Any

import investment_event_intelligence as event

COVERAGE_VERSION = "diplomacy-coverage-v1"

DIPLOMACY_QUERIES = (
    '(Iran OR Israel OR Hormuz OR "Red Sea") ("foreign minister" OR "top diplomat" OR minister OR government) (talks OR negotiations OR dialogue OR consultations OR diplomacy) when:1d',
    '(Iran OR "United States" OR "U.S." OR Washington) ("revive talks" OR "resume talks" OR "return to talks" OR "restart talks" OR "reopen dialogue" OR negotiations OR consultations) when:1d',
    '(Russia OR Ukraine OR China OR Taiwan) ("foreign minister" OR "top diplomat" OR government) (talks OR negotiations OR dialogue OR consultations OR diplomacy) when:1d',
)

DEESCALATION_EXTENSIONS = {
    "revive talks": 0.72,
    "resume talks": 0.75,
    "return to talks": 0.72,
    "restart talks": 0.72,
    "renew talks": 0.70,
    "reopen talks": 0.70,
    "reopen dialogue": 0.68,
    "restore dialogue": 0.68,
    "resume dialogue": 0.70,
    "restart negotiations": 0.75,
    "resume negotiations": 0.78,
    "return to negotiations": 0.75,
    "revive negotiations": 0.75,
    "negotiation mechanism": 0.62,
    "substantive consultations": 0.58,
    "diplomatic consultations": 0.58,
}

RHETORIC_EXTENSIONS = (
    "urge", "urges", "urged", "calls for", "called for", "appeals for",
)


def install() -> dict[str, Any]:
    """Install diplomacy recall/classification extensions once per process."""
    existing_queries = list(event.QUERIES)
    for query in DIPLOMACY_QUERIES:
        if query not in existing_queries:
            existing_queries.append(query)
    event.QUERIES = tuple(existing_queries)

    merged_deescalation = dict(event.DEESCALATION_TERMS)
    merged_deescalation.update(DEESCALATION_EXTENSIONS)
    event.DEESCALATION_TERMS = merged_deescalation

    rhetoric = list(event.RHETORIC_TERMS)
    for term in RHETORIC_EXTENSIONS:
        if term not in rhetoric:
            rhetoric.append(term)
    event.RHETORIC_TERMS = tuple(rhetoric)

    return {
        "version": COVERAGE_VERSION,
        "diplomacy_queries": len(DIPLOMACY_QUERIES),
        "deescalation_extensions": len(DEESCALATION_EXTENSIONS),
        "rhetoric_extensions": len(RHETORIC_EXTENSIONS),
    }
