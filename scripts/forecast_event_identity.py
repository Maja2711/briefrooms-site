"""P0: immutable forecast revision identity and event-level evaluation.

An event is the same ONLY if the frozen, executable outcome condition and
settlement target coincide. For unresolved/legacy contracts the identity is
conservatively unique per forecast (never merge on title or target alone).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Mapping


def _digest(prefix: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def identity(forecast: Mapping[str, Any]) -> dict[str, str]:
    meta = forecast.get("metadata") or {}
    hypothesis_id = str(forecast.get("hypothesis_id") or forecast.get("belief_id") or "")
    version = str(forecast.get("hypothesis_version") or meta.get("hypothesis_version")
                  or meta.get("model_freeze_version") or "1")
    fid = str(forecast.get("forecast_id") or "")
    spec = meta.get("outcome_spec")
    rule = str(forecast.get("outcome_rule") or "")
    # A relative outcome rule uses its frozen reference; 'value_below'
    # is instead wholly determined by the bound threshold and symbol.
    if isinstance(spec, dict) and spec.get("kind") == "value_below" and "threshold" in spec:
        contract = {"kind": "value_below", "symbol": spec.get("symbol"),
                    "threshold": spec["threshold"]}
    elif isinstance(spec, dict) and spec:
        contract = spec
    else:
        # Legacy contract may not expose its threshold / settlement criteria.
        # It cannot be proven to be the same event as any other forecast.
        contract = {"unverifiable_contract_forecast_id": fid}
    payload = {"hypothesis_id": hypothesis_id, "hypothesis_version": version,
               "target_at": forecast.get("target_at"), "outcome_rule": rule,
               "contract": contract}
    return {"event_id": _digest("event-", payload),
            "hypothesis_id": hypothesis_id,
            "hypothesis_version": version,
            "forecast_revision_id": _digest("revision-", {"forecast_id": fid})}


def canonical_event_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One FIRST frozen forecast per verifiable event for headline Brier.

    Historical revisions are retained outside this scoring view. Conflicting
    outcomes for one event are quarantined rather than arbitrarily resolved.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        f = row.get("f", row)
        event = str(f.get("event_id") or identity(f)["event_id"])
        groups[event].append(row)
    chosen = []
    conflicts = []
    for event, group in groups.items():
        outcomes = {str(x.get("y", x.get("outcome"))) for x in group}
        if len(outcomes) > 1:
            conflicts.append(event)
            continue
        chosen.append(min(group, key=lambda x: (str(x.get("f", x).get("forecast_at") or ""),
                                                   str(x.get("f", x).get("forecast_id") or ""))))
    chosen.sort(key=lambda x: (str(x.get("f", x).get("forecast_at") or ""),
                               str(x.get("f", x).get("forecast_id") or "")))
    return chosen, {"policy": "first_frozen_revision_per_event_v1",
                    "raw_forecast_count": len(rows), "independent_event_count": len(chosen),
                    "excluded_revisions": len(rows) - len(chosen) - sum(len(groups[k]) for k in conflicts),
                    "conflict_event_count": len(conflicts),
                    "quarantined_forecasts": sum(len(groups[k]) for k in conflicts),
                    "conflict_event_ids": sorted(conflicts)}
