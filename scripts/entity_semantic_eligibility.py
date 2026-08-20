#!/usr/bin/env python3
"""PR #19.1 — shared Entity Semantic Eligibility contract.

Sector classification is not a business-model contract. In particular,
``Financials`` must never imply that an issuer is a deposit-taking bank.

This module provides a deterministic, point-in-time semantic profile from the
existing governed Entity metadata. Business-model-specific Belief dimensions
fail closed when the required archetype is not established.

The v1 contract intentionally uses the existing ``exposure_key`` taxonomy from
``data/portfolio10k/universe.json``. It does not infer an archetype from ticker,
company name, market price behaviour, filings after the fact, or sector alone.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

CONTRACT_VERSION = "entity-semantic-eligibility-v1"

ARCHETYPE_BANK = "bank"
ARCHETYPE_PAYMENT_NETWORK = "payment_network"
ARCHETYPE_FINANCIAL_DATA_RATINGS = "financial_data_ratings"
ARCHETYPE_FINANCIALS_UNRESOLVED = "financials_unresolved"
ARCHETYPE_GENERAL = "general_corporate"

STATUS_ELIGIBLE = "eligible"
STATUS_UNRESOLVED_FAIL_CLOSED = "unresolved_fail_closed"
STATUS_RESOLVED_MISMATCH = "resolved_mismatch"
STATUS_NOT_RESTRICTED = "not_restricted"

EXPOSURE_KEY_ARCHETYPES: Mapping[str, str] = {
    "diversified_banking": ARCHETYPE_BANK,
    "commercial_banking": ARCHETYPE_BANK,
    "retail_banking": ARCHETYPE_BANK,
    "universal_banking": ARCHETYPE_BANK,
    "payments_network": ARCHETYPE_PAYMENT_NETWORK,
    "financial_data_ratings": ARCHETYPE_FINANCIAL_DATA_RATINGS,
}

BANK_SPECIFIC_DIMENSIONS: Tuple[str, ...] = (
    "net_interest_income_durability",
    "credit_quality",
    "deposit_funding",
    "capital_strength",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def resolve_entity_archetype(entity: Mapping[str, Any]) -> Tuple[str, str]:
    """Return ``(archetype, source)`` without sector->bank inference.

    An explicitly reviewed ``entity_archetype`` wins. Otherwise the governed
    ``exposure_key`` is mapped through the frozen v1 registry. Unknown
    Financials remain unresolved and therefore cannot activate bank-specific
    dimensions.
    """
    explicit = _text(entity.get("entity_archetype")).lower()
    if explicit and explicit not in {"unknown", "unresolved", ARCHETYPE_FINANCIALS_UNRESOLVED}:
        return explicit, "explicit_entity_archetype"

    exposure_key = _text(entity.get("exposure_key")).lower()
    mapped = EXPOSURE_KEY_ARCHETYPES.get(exposure_key)
    if mapped:
        return mapped, "exposure_key_registry"

    sector = _text(entity.get("sector"))
    if sector == "Financials":
        return ARCHETYPE_FINANCIALS_UNRESOLVED, "financials_sector_fail_closed"
    return ARCHETYPE_GENERAL, "non_financials_general"


def semantic_profile(entity: Mapping[str, Any]) -> Dict[str, Any]:
    archetype, source = resolve_entity_archetype(entity)
    exposure_key = _text(entity.get("exposure_key")).lower() or None
    return {
        "semantic_eligibility_contract_version": CONTRACT_VERSION,
        "entity_archetype": archetype,
        "entity_archetype_source": source,
        "exposure_key": exposure_key,
        "bank_specific_dimensions_eligible": archetype == ARCHETYPE_BANK,
    }


def dimension_eligibility(entity: Mapping[str, Any], dimension: str) -> Dict[str, Any]:
    profile = semantic_profile(entity)
    dimension = _text(dimension)
    if dimension in BANK_SPECIFIC_DIMENSIONS:
        archetype = profile["entity_archetype"]
        if archetype == ARCHETYPE_BANK:
            status = STATUS_ELIGIBLE
            eligible = True
            reason = "bank_archetype_confirmed"
        elif archetype == ARCHETYPE_FINANCIALS_UNRESOLVED:
            status = STATUS_UNRESOLVED_FAIL_CLOSED
            eligible = False
            reason = "bank_specific_dimension_waits_for_resolved_archetype"
        else:
            status = STATUS_RESOLVED_MISMATCH
            eligible = False
            reason = "resolved_nonbank_archetype_rejects_bank_specific_dimension"
        return {
            **profile,
            "dimension": dimension,
            "eligible": eligible,
            "status": status,
            "eligibility_scope": "entity_archetype:bank",
            "reason": reason,
        }
    return {
        **profile,
        "dimension": dimension,
        "eligible": True,
        "status": STATUS_NOT_RESTRICTED,
        "eligibility_scope": "not_bank_specific",
        "reason": "dimension_not_restricted_by_bank_archetype_contract",
    }


def annotate_entity(entity: Mapping[str, Any]) -> Dict[str, Any]:
    return {**dict(entity), **semantic_profile(entity)}


def is_semantically_ineligible(entity: Mapping[str, Any], dimension: str) -> bool:
    return not bool(dimension_eligibility(entity, dimension)["eligible"])


def is_resolved_semantic_mismatch(entity: Mapping[str, Any], dimension: str) -> bool:
    """True only when an established archetype contradicts the dimension.

    This distinction is critical for append-only migration: an unresolved
    Financials archetype must fail closed for *new* use, but it must not create
    an irreversible deprecation event until the non-bank classification is
    actually established.
    """
    return dimension_eligibility(entity, dimension)["status"] == STATUS_RESOLVED_MISMATCH
