#!/usr/bin/env python3
"""PR32A builder from the existing Belief Epistemic projection to canonical v1.

This module is an adapter/assembler only. It does not recalculate Belief Core
probabilities, confidence, evidence reliability or contribution scores.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from canonical_epistemic_state import (
        CONTRACT_VERSION,
        UPSTREAM_CONTRACT_VERSION,
        BUILDER_ID,
        BUILDER_VERSION,
        CanonicalBeliefState,
        CanonicalContribution,
        CanonicalEpistemicState,
        CanonicalEpistemicStateError,
        CanonicalEvidenceRef,
        CanonicalObservationRef,
        EpistemicAuthority,
        belief_facts,
        bounded_probability,
        evidence_facts,
        iso_z,
        observation_facts,
        optional_delta,
        optional_probability,
        parse_aware,
        sha256_digest,
        state_facts,
        verify_state,
    )
except ModuleNotFoundError:
    from scripts.canonical_epistemic_state import (
        CONTRACT_VERSION,
        UPSTREAM_CONTRACT_VERSION,
        BUILDER_ID,
        BUILDER_VERSION,
        CanonicalBeliefState,
        CanonicalContribution,
        CanonicalEpistemicState,
        CanonicalEpistemicStateError,
        CanonicalEvidenceRef,
        CanonicalObservationRef,
        EpistemicAuthority,
        belief_facts,
        bounded_probability,
        evidence_facts,
        iso_z,
        observation_facts,
        optional_delta,
        optional_probability,
        parse_aware,
        sha256_digest,
        state_facts,
        verify_state,
    )

OUTPUT_FILENAME = "canonical_epistemic_state.json"


def _text(value: Any, *, field: str) -> str:
    out = str(value or "").strip()
    if not out:
        raise CanonicalEpistemicStateError(f"{field} is required")
    return out


def _tuple_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return tuple(sorted({str(x).strip() for x in values if str(x).strip()}))


def _unique_rows(rows: Iterable[Mapping[str, Any]], *, id_field: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    fingerprints: dict[str, str] = {}
    for raw in rows:
        row = dict(raw)
        row_id = _text(row.get(id_field), field=id_field)
        fingerprint = sha256_digest(row)
        if row_id in out:
            if fingerprints[row_id] != fingerprint:
                raise CanonicalEpistemicStateError(f"conflicting duplicate {id_field}: {row_id}")
            continue
        out[row_id] = row
        fingerprints[row_id] = fingerprint
    return out


def _normalized_projection_source(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(source)
    states_out: dict[str, Any] = {}
    for belief_id, raw_state in sorted((source.get("states") or {}).items()):
        state = dict(raw_state)
        for key in (
            "member_belief_ids",
            "dominant_support_evidence_ids",
            "dominant_opposition_evidence_ids",
            "drilldown_reasons",
        ):
            if key in state:
                state[key] = list(_tuple_ids(state.get(key)))
        provenance = dict(state.get("provenance_root") or {})
        for key in ("belief_ids", "representative_evidence_ids"):
            if key in provenance:
                provenance[key] = list(_tuple_ids(provenance.get(key)))
        if provenance:
            state["provenance_root"] = provenance
        contributions = [dict(row) for row in state.get("contributions") or []]
        contributions.sort(
            key=lambda row: (
                str(row.get("contributor_id") or ""),
                float(row.get("signed_probability_delta") or 0.0),
            )
        )
        if "contributions" in state:
            state["contributions"] = contributions
        states_out[str(belief_id)] = state
    payload["states"] = states_out
    return payload


def _normalized_belief_core_source(state: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(state)
    for key, id_field in (
        ("definitions", "belief_id"),
        ("beliefs", "belief_id"),
        ("evidence", "evidence_id"),
    ):
        rows = _unique_rows(payload.get(key, []), id_field=id_field)
        payload[key] = [rows[row_id] for row_id in sorted(rows)]
    return payload


def _normalized_observation_source(observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = _unique_rows(observations, id_field="observation_id")
    return [rows[row_id] for row_id in sorted(rows)]


def _observation_id_candidates(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    metadata = dict(evidence.get("metadata") or {})
    values: list[str] = []
    if metadata.get("observation_id"):
        values.append(str(metadata["observation_id"]))
    raw_many = metadata.get("observation_ids")
    if isinstance(raw_many, (list, tuple, set)):
        values.extend(str(x) for x in raw_many if x)
    return _tuple_ids(values)


def _derived_from(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    raw = evidence.get("derived_from")
    if raw is None:
        metadata = dict(evidence.get("metadata") or {})
        raw = metadata.get("derived_from")
    return _tuple_ids(raw)


def _resolve_observation_ids(
    evidence_id: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    *,
    stack: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if evidence_id in stack:
        raise CanonicalEpistemicStateError(
            "evidence lineage cycle: " + " -> ".join(stack + (evidence_id,))
        )
    if evidence_id not in evidence_by_id:
        raise CanonicalEpistemicStateError(f"missing derived evidence: {evidence_id}")
    row = evidence_by_id[evidence_id]
    direct = _observation_id_candidates(row)
    if direct:
        return direct
    resolved: list[str] = []
    for parent in _derived_from(row):
        resolved.extend(
            _resolve_observation_ids(parent, evidence_by_id, stack=stack + (evidence_id,))
        )
    result = _tuple_ids(resolved)
    if not result:
        raise CanonicalEpistemicStateError(
            f"evidence has no observation/source lineage: {evidence_id}"
        )
    return result


def _build_observation(row: Mapping[str, Any], *, cutoff: str) -> CanonicalObservationRef:
    observation_id = _text(row.get("observation_id"), field="observation_id")
    source = _text(row.get("source"), field=f"observation[{observation_id}].source")
    source_ref = _text(
        row.get("source_ref"), field=f"observation[{observation_id}].source_ref"
    )
    observed_at = iso_z(
        row.get("observed_at"), field=f"observation[{observation_id}].observed_at"
    )
    if parse_aware(observed_at, field="observed_at") > parse_aware(cutoff, field="as_of"):
        raise CanonicalEpistemicStateError(
            f"future observation exceeds EpistemicState as_of: {observation_id}"
        )
    draft = CanonicalObservationRef(
        observation_id=observation_id,
        observation_hash="",
        source=source,
        source_ref=source_ref,
        observed_at=observed_at,
        metric=str(row.get("metric")) if row.get("metric") is not None else None,
        entity=str(row.get("entity")) if row.get("entity") is not None else None,
        value=row.get("value"),
        unit=str(row.get("unit")) if row.get("unit") is not None else None,
        content_hash=sha256_digest(row),
    )
    return replace(draft, observation_hash=sha256_digest(observation_facts(draft)))


def _build_evidence(
    row: Mapping[str, Any],
    *,
    observation_ids: tuple[str, ...],
    cutoff: str,
) -> CanonicalEvidenceRef:
    evidence_id = _text(row.get("evidence_id"), field="evidence_id")
    belief_id = _text(row.get("belief_id"), field=f"evidence[{evidence_id}].belief_id")
    source = _text(row.get("source"), field=f"evidence[{evidence_id}].source")
    source_ref = _text(row.get("source_ref"), field=f"evidence[{evidence_id}].source_ref")
    observed_at = iso_z(row.get("observed_at"), field=f"evidence[{evidence_id}].observed_at")
    if parse_aware(observed_at, field="observed_at") > parse_aware(cutoff, field="as_of"):
        raise CanonicalEpistemicStateError(
            f"future evidence exceeds EpistemicState as_of: {evidence_id}"
        )
    try:
        direction = int(row.get("direction"))
    except (TypeError, ValueError) as exc:
        raise CanonicalEpistemicStateError(
            f"evidence direction must be -1 or 1: {evidence_id}"
        ) from exc
    if direction not in {-1, 1}:
        raise CanonicalEpistemicStateError(
            f"evidence direction must be -1 or 1: {evidence_id}"
        )
    draft = CanonicalEvidenceRef(
        evidence_id=evidence_id,
        evidence_hash="",
        belief_id=belief_id,
        source=source,
        source_ref=source_ref,
        observed_at=observed_at,
        direction=direction,
        strength=bounded_probability(
            row.get("strength"), field=f"evidence[{evidence_id}].strength"
        ),
        reliability=bounded_probability(
            row.get("reliability"), field=f"evidence[{evidence_id}].reliability"
        ),
        observation_ids=observation_ids,
        derived_from=_derived_from(row),
        evidence_type=(
            str(row.get("evidence_type")) if row.get("evidence_type") is not None else None
        ),
        independence_cluster=(
            str(row.get("independence_cluster"))
            if row.get("independence_cluster") is not None
            else None
        ),
    )
    return replace(draft, evidence_hash=sha256_digest(evidence_facts(draft)))


def _build_belief(
    belief_id: str,
    projection: Mapping[str, Any],
    belief_core_belief: Mapping[str, Any],
    definition: Mapping[str, Any],
    *,
    cutoff: str,
) -> CanonicalBeliefState:
    projection_state_id = _text(
        projection.get("state_id"), field=f"state[{belief_id}].state_id"
    )
    member_belief_ids = _tuple_ids(projection.get("member_belief_ids"))
    if belief_id not in member_belief_ids:
        raise CanonicalEpistemicStateError(f"projection/member belief mismatch: {belief_id}")
    provenance = dict(projection.get("provenance_root") or {})
    if provenance.get("path") != "state->belief->evidence->observation->source":
        raise CanonicalEpistemicStateError(f"non-reversible provenance path: {belief_id}")
    evidence_ids = _tuple_ids(provenance.get("representative_evidence_ids"))
    as_of = iso_z(
        belief_core_belief.get("last_updated") or projection.get("as_of") or cutoff,
        field=f"belief[{belief_id}].as_of",
    )
    if parse_aware(as_of, field="belief.as_of") > parse_aware(cutoff, field="as_of"):
        raise CanonicalEpistemicStateError(
            f"future belief exceeds EpistemicState as_of: {belief_id}"
        )

    importance = projection.get("importance")
    if importance is not None:
        importance = bounded_probability(importance, field=f"belief[{belief_id}].importance")

    contributions: list[CanonicalContribution] = []
    for row in projection.get("contributions") or []:
        try:
            signed_delta = round(float(row.get("signed_probability_delta")), 6)
            direction = int(row.get("direction"))
        except (TypeError, ValueError) as exc:
            raise CanonicalEpistemicStateError(
                f"invalid contribution for belief: {belief_id}"
            ) from exc
        if direction not in {-1, 1}:
            raise CanonicalEpistemicStateError(
                f"invalid contribution direction for belief: {belief_id}"
            )
        contributions.append(
            CanonicalContribution(
                contributor_type=_text(
                    row.get("contributor_type"),
                    field=f"belief[{belief_id}].contributor_type",
                ),
                contributor_id=_text(
                    row.get("contributor_id"), field=f"belief[{belief_id}].contributor_id"
                ),
                signed_probability_delta=signed_delta,
                direction=direction,
                source_ref=(
                    str(row.get("source_ref")) if row.get("source_ref") is not None else None
                ),
                observation_id=(
                    str(row.get("observation_id"))
                    if row.get("observation_id") is not None
                    else None
                ),
            )
        )
    contributions.sort(key=lambda row: (row.contributor_id, row.signed_probability_delta))

    draft = CanonicalBeliefState(
        belief_id=belief_id,
        belief_hash="",
        projection_state_id=projection_state_id,
        topic=_text(
            projection.get("topic")
            or definition.get("claim")
            or belief_core_belief.get("claim"),
            field=f"belief[{belief_id}].topic",
        ),
        entity=_text(
            projection.get("entity")
            or belief_core_belief.get("entity")
            or definition.get("entity")
            or "GLOBAL",
            field=f"belief[{belief_id}].entity",
        ),
        domain=_text(
            projection.get("domain")
            or belief_core_belief.get("domain")
            or definition.get("domain")
            or "general",
            field=f"belief[{belief_id}].domain",
        ),
        probability=bounded_probability(
            projection.get("probability"), field=f"belief[{belief_id}].probability"
        ),
        confidence=bounded_probability(
            projection.get("confidence"), field=f"belief[{belief_id}].confidence"
        ),
        previous_probability=optional_probability(
            projection.get("previous_probability"),
            field=f"belief[{belief_id}].previous_probability",
        ),
        delta_probability=optional_delta(
            projection.get("delta_probability"),
            field=f"belief[{belief_id}].delta_probability",
        ),
        contradiction=bounded_probability(
            projection.get("contradiction"), field=f"belief[{belief_id}].contradiction"
        ),
        freshness=bounded_probability(
            projection.get("freshness"), field=f"belief[{belief_id}].freshness"
        ),
        audit_status=_text(
            projection.get("audit_status"), field=f"belief[{belief_id}].audit_status"
        ),
        as_of=as_of,
        member_belief_ids=member_belief_ids,
        evidence_ids=evidence_ids,
        contributions=tuple(contributions),
        dominant_support_evidence_ids=_tuple_ids(
            projection.get("dominant_support_evidence_ids")
        ),
        dominant_opposition_evidence_ids=_tuple_ids(
            projection.get("dominant_opposition_evidence_ids")
        ),
        drilldown_required=bool(projection.get("drilldown_required")),
        drilldown_reasons=_tuple_ids(projection.get("drilldown_reasons")),
        importance=importance,
        verify_later=bool(projection.get("verify_later", False)),
        research_required=bool(projection.get("research_required", False)),
        expected_outcome=(
            str(projection.get("expected_outcome"))
            if projection.get("expected_outcome") is not None
            else None
        ),
    )
    return replace(draft, belief_hash=sha256_digest(belief_facts(draft)))


def build_canonical_epistemic_state(
    *,
    source_projection: Mapping[str, Any],
    belief_core_state: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> CanonicalEpistemicState:
    normalized_projection = _normalized_projection_source(source_projection)
    if normalized_projection.get("contract_version") != UPSTREAM_CONTRACT_VERSION:
        raise CanonicalEpistemicStateError(
            "source projection is not belief-epistemic-state-v1"
        )
    source_authority = dict(normalized_projection.get("authority") or {})
    source_controls = dict(normalized_projection.get("controls") or {})
    if source_authority.get("llm_may_override_probability") is not False:
        raise CanonicalEpistemicStateError("source aggregate authority is not fail-closed")
    for key in (
        "decision_writeback_enabled",
        "belief_core_writeback_enabled",
        "llm_override_enabled",
        "automatic_tuning_enabled",
    ):
        if source_controls.get(key) is not False:
            raise CanonicalEpistemicStateError(f"source control must remain false: {key}")

    cutoff = iso_z(
        normalized_projection.get("created_at"), field="source_projection.created_at"
    )
    normalized_core = _normalized_belief_core_source(belief_core_state)
    normalized_observations = _normalized_observation_source(observations)
    definitions = _unique_rows(normalized_core.get("definitions", []), id_field="belief_id")
    core_beliefs = _unique_rows(normalized_core.get("beliefs", []), id_field="belief_id")
    evidence_by_id = _unique_rows(normalized_core.get("evidence", []), id_field="evidence_id")
    observation_by_id = _unique_rows(
        normalized_observations, id_field="observation_id"
    )

    canonical_beliefs: list[CanonicalBeliefState] = []
    required_evidence_ids: set[str] = set()
    states = dict(normalized_projection.get("states") or {})
    for belief_id in sorted(states):
        if belief_id not in definitions or belief_id not in core_beliefs:
            raise CanonicalEpistemicStateError(
                f"projection belief missing from Belief Core source: {belief_id}"
            )
        belief = _build_belief(
            belief_id,
            dict(states[belief_id]),
            core_beliefs[belief_id],
            definitions[belief_id],
            cutoff=cutoff,
        )
        canonical_beliefs.append(belief)
        required_evidence_ids.update(belief.evidence_ids)

    canonical_evidence: list[CanonicalEvidenceRef] = []
    required_observation_ids: set[str] = set()
    for evidence_id in sorted(required_evidence_ids):
        if evidence_id not in evidence_by_id:
            raise CanonicalEpistemicStateError(
                f"projection evidence missing from Belief Core source: {evidence_id}"
            )
        observation_ids = _resolve_observation_ids(evidence_id, evidence_by_id)
        required_observation_ids.update(observation_ids)
        canonical_evidence.append(
            _build_evidence(
                evidence_by_id[evidence_id],
                observation_ids=observation_ids,
                cutoff=cutoff,
            )
        )

    canonical_observations: list[CanonicalObservationRef] = []
    for observation_id in sorted(required_observation_ids):
        if observation_id not in observation_by_id:
            raise CanonicalEpistemicStateError(
                f"missing observation referenced by evidence: {observation_id}"
            )
        canonical_observations.append(
            _build_observation(observation_by_id[observation_id], cutoff=cutoff)
        )

    draft = CanonicalEpistemicState(
        state_id="",
        state_hash="",
        as_of=cutoff,
        source_projection_hash=sha256_digest(normalized_projection),
        belief_core_state_hash=sha256_digest(normalized_core),
        observations_source_hash=sha256_digest(normalized_observations),
        beliefs=tuple(canonical_beliefs),
        evidence=tuple(canonical_evidence),
        observations=tuple(canonical_observations),
        authority=EpistemicAuthority(),
    )
    digest = sha256_digest(state_facts(draft))
    state = replace(draft, state_id="eps-" + digest[:24], state_hash=digest)
    verify_state(state)
    return state


def load_observations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, Mapping):
            raise CanonicalEpistemicStateError(
                f"observations.jsonl line {line_no} is not an object"
            )
        rows.append(dict(row))
    return rows


def build_from_state_dir(state_dir: Path) -> CanonicalEpistemicState:
    projection_path = state_dir / "epistemic_state.json"
    core_path = state_dir / "state.json"
    observations_path = state_dir / "observations.jsonl"
    for path in (projection_path, core_path, observations_path):
        if not path.exists():
            raise FileNotFoundError(path)
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    core_state = json.loads(core_path.read_text(encoding="utf-8"))
    observations = load_observations(observations_path)
    return build_canonical_epistemic_state(
        source_projection=projection,
        belief_core_state=core_state,
        observations=observations,
    )


def persist_canonical_state(state_dir: Path, state: CanonicalEpistemicState) -> Path:
    verify_state(state)
    output = state_dir / OUTPUT_FILENAME
    output.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    root = Path(args.state_dir)
    state = build_from_state_dir(root)
    persist_canonical_state(root, state)
    if args.print_summary:
        print(
            json.dumps(
                {
                    "contract_version": CONTRACT_VERSION,
                    "builder_id": BUILDER_ID,
                    "builder_version": BUILDER_VERSION,
                    "state_id": state.state_id,
                    "beliefs": len(state.beliefs),
                    "evidence": len(state.evidence),
                    "observations": len(state.observations),
                    "authority": state.to_dict()["authority"],
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
