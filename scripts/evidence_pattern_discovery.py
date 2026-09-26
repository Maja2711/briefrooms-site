#!/usr/bin/env python3
"""BRIEFROOMS-PATTERN-2: prospective association-pattern discovery for BriefRooms Decision LAB.

The miner reads only frozen Belief Core forecast/verification history. Candidate
patterns are discovered on an earlier discovery slice and evaluated on a later
holdout slice. Results are research-shadow diagnostics only and never write back
to Belief Core or any decision engine.

ARIS principle used here:
    shorter explanation = model description + residual outcome code

A candidate pattern is kept only when it yields positive MDL gain on discovery.
Correlation/association is never promoted to a causal claim.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "briefrooms-evidence-pattern-v2"
MODE = "research_shadow"
CAUSAL_STATUS = "ASSOCIATION_ONLY"

AUTHORITY = {
    "decision_influence": False,
    "production_decision_influence": False,
    "belief_core_writeback_enabled": False,
    "consumer_contract_export_enabled": False,
    "trade_execution_enabled": False,
    "automatic_promotion_enabled": False,
    "automatic_tuning_enabled": False,
    "causal_edge_writeback_enabled": False,
}

MIN_GROUP_ROWS = 6
MIN_DISCOVERY_SUPPORT = 3
MIN_HOLDOUT_SUPPORT = 2
MAX_ATOMS_PER_GROUP = 18
MAX_PATTERN_SIZE = 4
MAX_PATTERNS_PUBLIC = 30
DISCOVERY_FRACTION = 0.70
MIN_DISCOVERY_LIFT = 0.05
MIN_HOLDOUT_LIFT = 0.02
MAX_DISCOVERY_FALSE_DISCOVERY_RATE = 0.10


def _records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [x for x in value.values() if isinstance(x, Mapping)]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, Mapping)]
    return []


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _horizon_bucket(hours: Any) -> str:
    h = max(0.0, _safe_float(hours))
    if h <= 6:
        return "0-6h"
    if h <= 24:
        return "6-24h"
    if h <= 120:
        return "1-5d"
    return "5d+"


def _atom_token(evidence: Mapping[str, Any]) -> str | None:
    kind = str(evidence.get("evidence_type") or "").strip().lower()
    if not kind:
        return None
    direction = -1 if int(evidence.get("direction", 1)) < 0 else 1
    return f"e::{kind}::{direction:+d}"


def atom_label(token: str) -> str:
    if token.startswith("e::"):
        _, kind, direction = token.split("::", 2)
        text = kind.replace("_", " ").replace("-", " ").strip()
        arrow = "↓" if direction.startswith("-") else "↑"
        return f"{text} {arrow}"
    if token.startswith("r::"):
        return "regime: " + token[3:].replace("_", " ")
    return token


def _row_atoms(row: Mapping[str, Any]) -> frozenset[str]:
    atoms: set[str] = set()
    for evidence in row.get("evidence_snapshot") or []:
        if not isinstance(evidence, Mapping):
            continue
        if _safe_float(evidence.get("effective_mass"), 0.0) < 0.05:
            continue
        token = _atom_token(evidence)
        if token:
            atoms.add(token)
    regime = str(row.get("regime") or "unknown").strip().lower()
    if regime not in {"", "unknown"}:
        atoms.add(f"r::{regime}")
    return frozenset(atoms)


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _bernoulli_universal_bits(successes: int, failures: int) -> float:
    """Jeffreys/Beta(1/2,1/2) mixture code length for a Bernoulli sequence."""
    return -(
        _log_beta(successes + 0.5, failures + 0.5) - _log_beta(0.5, 0.5)
    ) / math.log(2.0)


def _model_bits(vocabulary_size: int, pattern_size: int) -> float:
    if vocabulary_size <= 0 or pattern_size <= 0 or vocabulary_size < pattern_size:
        return 0.0
    return 1.0 + math.log2(pattern_size) + pattern_size * math.log2(vocabulary_size)


def _rate(rows: Sequence[Mapping[str, Any]], expected: bool = True) -> float | None:
    if not rows:
        return None
    successes = sum(bool(x.get("outcome")) is expected for x in rows)
    return successes / len(rows)


def _posterior_mean(successes: int, total: int) -> float | None:
    if total <= 0:
        return None
    return (successes + 1.0) / (total + 2.0)


def _stable_pattern_identity(
    belief_id: str, horizon_bucket: str, atoms: Sequence[str]
) -> tuple[str, str]:
    canonical = json.dumps([belief_id, horizon_bucket, sorted(atoms)], separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    display_num = int(digest[:12], 16) % 1_000_000
    return f"BRP-{display_num:06d}", digest[:16]


def _candidate_atom_vocabulary(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    n = len(rows)
    counts: Counter[str] = Counter(atom for row in rows for atom in row["atoms"])
    baseline = _rate(rows, True) or 0.0
    ranked: list[tuple[float, int, str]] = []
    for atom, count in counts.items():
        if count < 2 or count >= n:
            continue
        matched = [row for row in rows if atom in row["atoms"]]
        p = _rate(matched, True)
        lift = abs((p if p is not None else baseline) - baseline)
        ranked.append((lift * math.sqrt(count), count, atom))
    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [x[2] for x in ranked[:MAX_ATOMS_PER_GROUP]]


def _possible_modifier(
    pattern_atoms: frozenset[str],
    matched: Sequence[Mapping[str, Any]],
    expected: bool,
) -> dict[str, Any] | None:
    good = [x for x in matched if bool(x.get("outcome")) is expected]
    bad = [x for x in matched if bool(x.get("outcome")) is not expected]
    if len(bad) < 2:
        return None
    candidates = set().union(*(x["atoms"] for x in bad)) - set(pattern_atoms)
    best: tuple[float, int, str, float, float] | None = None
    for atom in candidates:
        bad_count = sum(atom in x["atoms"] for x in bad)
        good_count = sum(atom in x["atoms"] for x in good)
        if bad_count < 2:
            continue
        bad_rate = bad_count / len(bad)
        good_rate = good_count / len(good) if good else 0.0
        delta = bad_rate - good_rate
        if delta < 0.20:
            continue
        key = (delta, bad_count, atom, bad_rate, good_rate)
        if best is None or key[:2] > best[:2]:
            best = key
    if best is None:
        return None
    delta, support, atom, bad_rate, good_rate = best
    return {
        "atom": atom,
        "label": atom_label(atom),
        "exception_support": support,
        "exception_prevalence": round(bad_rate, 6),
        "success_prevalence": round(good_rate, 6),
        "enrichment": round(delta, 6),
    }


def _status(
    discovery_lift: float,
    holdout_lift: float | None,
    holdout_n: int,
    discovery_n: int,
) -> str:
    if holdout_n < MIN_HOLDOUT_SUPPORT or holdout_lift is None:
        return "DISCOVERY"
    if holdout_lift < MIN_HOLDOUT_LIFT:
        return "UNSTABLE"
    if holdout_n >= 5 and discovery_n >= 6 and holdout_lift >= 0.05:
        return "REPLICATED"
    return "OOS PASS"


def _median(values: Sequence[float]) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def _group_by(rows: Sequence[Mapping[str, Any]], key_fn) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        out[str(key_fn(row))].append(row)
    return out


def _mine_group(
    rows: Sequence[Mapping[str, Any]], belief_id: str, horizon_bucket: str
) -> list[dict[str, Any]]:
    n = len(rows)
    if n < MIN_GROUP_ROWS:
        return []
    split = max(4, min(n - 2, int(math.ceil(n * DISCOVERY_FRACTION))))
    discovery = list(rows[:split])
    holdout = list(rows[split:])
    if len(discovery) < 4:
        return []

    vocabulary = _candidate_atom_vocabulary(discovery)
    if len(vocabulary) < 2:
        return []

    d_true = sum(bool(x.get("outcome")) for x in discovery)
    base_code = _bernoulli_universal_bits(d_true, len(discovery) - d_true)
    baseline_true = d_true / len(discovery)
    holdout_baseline_true = _rate(holdout, True)
    patterns: list[dict[str, Any]] = []

    max_k = min(MAX_PATTERN_SIZE, len(vocabulary))
    for k in range(2, max_k + 1):
        for atoms_tuple in itertools.combinations(vocabulary, k):
            atoms = frozenset(atoms_tuple)
            matched_d = [x for x in discovery if atoms.issubset(x["atoms"])]
            if len(matched_d) < MIN_DISCOVERY_SUPPORT or len(matched_d) >= len(discovery):
                continue
            rest_d = [x for x in discovery if x not in matched_d]
            ms = sum(bool(x.get("outcome")) for x in matched_d)
            rs = sum(bool(x.get("outcome")) for x in rest_d)
            split_code = (
                _bernoulli_universal_bits(ms, len(matched_d) - ms)
                + _bernoulli_universal_bits(rs, len(rest_d) - rs)
                + _model_bits(len(vocabulary), k)
            )
            mdl_gain = base_code - split_code
            if mdl_gain <= 0:
                continue

            matched_true_rate = ms / len(matched_d)
            expected = matched_true_rate >= baseline_true
            d_successes = ms if expected else len(matched_d) - ms
            d_success_rate = d_successes / len(matched_d)
            d_baseline_success = baseline_true if expected else 1.0 - baseline_true
            d_lift = d_success_rate - d_baseline_success
            if d_lift < MIN_DISCOVERY_LIFT:
                continue

            matched_h = [x for x in holdout if atoms.issubset(x["atoms"])]
            h_successes = sum(bool(x.get("outcome")) is expected for x in matched_h)
            h_success_rate = h_successes / len(matched_h) if matched_h else None
            h_baseline_success = (
                holdout_baseline_true
                if expected
                else (None if holdout_baseline_true is None else 1.0 - holdout_baseline_true)
            )
            h_lift = (
                None
                if h_success_rate is None or h_baseline_success is None
                else h_success_rate - h_baseline_success
            )

            matched_all = [x for x in rows if atoms.issubset(x["atoms"])]
            regimes: dict[str, dict[str, Any]] = {}
            for regime, rr in sorted(
                _group_by(matched_all, lambda x: str(x.get("regime") or "unknown")).items()
            ):
                succ = sum(bool(x.get("outcome")) is expected for x in rr)
                regimes[regime] = {
                    "n": len(rr),
                    "success_rate": round(succ / len(rr), 6),
                }

            residual = sum(bool(x.get("outcome")) is not expected for x in matched_all)
            modifier = _possible_modifier(atoms, matched_all, expected)
            pattern_id, pattern_key = _stable_pattern_identity(
                belief_id, horizon_bucket, atoms_tuple
            )
            forecast_example = discovery[-1]
            patterns.append(
                {
                    "pattern_id": pattern_id,
                    "pattern_key": pattern_key,
                    "belief_id": belief_id,
                    "entity": forecast_example.get("entity"),
                    "horizon_bucket": horizon_bucket,
                    "horizon_hours_median": round(
                        _median([_safe_float(x.get("horizon_hours")) for x in rows]), 6
                    ),
                    "atoms": sorted(atoms),
                    "atom_labels": [atom_label(x) for x in sorted(atoms)],
                    "expected_outcome": expected,
                    "causal_status": CAUSAL_STATUS,
                    "discovery": {
                        "n": len(matched_d),
                        "successes": d_successes,
                        "success_rate": round(d_success_rate, 6),
                        "posterior_mean": round(
                            _posterior_mean(d_successes, len(matched_d)) or 0.0, 6
                        ),
                        "baseline": round(d_baseline_success, 6),
                        "lift": round(d_lift, 6),
                        "mdl_gain_bits": round(mdl_gain, 6),
                    },
                    "holdout": {
                        "n": len(matched_h),
                        "successes": h_successes,
                        "success_rate": (
                            None if h_success_rate is None else round(h_success_rate, 6)
                        ),
                        "baseline": (
                            None
                            if h_baseline_success is None
                            else round(h_baseline_success, 6)
                        ),
                        "lift": None if h_lift is None else round(h_lift, 6),
                    },
                    "regimes": regimes,
                    "residual_exceptions": residual,
                    "matched_total": len(matched_all),
                    "possible_modifier": modifier,
                    "status": _status(d_lift, h_lift, len(matched_h), len(matched_d)),
                    "discovery_window": {
                        "start": discovery[0].get("forecast_at"),
                        "end": discovery[-1].get("forecast_at"),
                    },
                    "holdout_window": {
                        "start": holdout[0].get("forecast_at") if holdout else None,
                        "end": holdout[-1].get("forecast_at") if holdout else None,
                    },
                }
            )

    priority = {"REPLICATED": 4, "OOS PASS": 3, "DISCOVERY": 2, "UNSTABLE": 1}
    out = list({p["pattern_id"]: p for p in patterns}.values())
    out.sort(
        key=lambda p: (
            -priority.get(p["status"], 0),
            -p["discovery"]["mdl_gain_bits"],
            -p["discovery"]["n"],
            len(p["atoms"]),
            p["pattern_id"],
        )
    )
    return out


def _eligible_rows(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for v in _records(state.get("verifications")):
        fid = str(v.get("forecast_id") or "")
        if not fid or fid in seen:
            continue
        if bool(v.get("legacy", False)) or v.get("calibration_eligible") is False:
            continue
        snapshot = [
            dict(x)
            for x in (v.get("evidence_snapshot") or [])
            if isinstance(x, Mapping)
        ]
        if not snapshot:
            continue
        row = dict(v)
        row["evidence_snapshot"] = snapshot
        row["atoms"] = _row_atoms(row)
        if len(row["atoms"]) < 2:
            continue
        rows.append(row)
        seen.add(fid)
    rows.sort(key=lambda x: str(x.get("forecast_at") or ""))
    return rows


def build_pattern_report(state: Mapping[str, Any]) -> dict[str, Any]:
    rows = _eligible_rows(state)
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        belief_id = str(row.get("belief_id") or "")
        if not belief_id:
            continue
        groups[(belief_id, _horizon_bucket(row.get("horizon_hours")))].append(row)

    patterns: list[dict[str, Any]] = []
    groups_mined = 0
    for (belief_id, horizon), group_rows in sorted(groups.items()):
        if len(group_rows) < MIN_GROUP_ROWS:
            continue
        groups_mined += 1
        patterns.extend(_mine_group(group_rows, belief_id, horizon))

    priority = {"REPLICATED": 4, "OOS PASS": 3, "DISCOVERY": 2, "UNSTABLE": 1}
    patterns.sort(
        key=lambda p: (
            -priority.get(p["status"], 0),
            -p["discovery"]["mdl_gain_bits"],
            -p["discovery"]["n"],
            p["pattern_id"],
        )
    )
    patterns = patterns[:MAX_PATTERNS_PUBLIC]
    counts = Counter(p["status"] for p in patterns)

    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "authority": dict(AUTHORITY),
        "causal_status": CAUSAL_STATUS,
        "methodology": {
            "source": "prospective_frozen_forecast_verifications",
            "candidate_pattern_size": "2-4 atoms",
            "selection": "discovery_only_positive_mdl_gain_plus_minimum_predictive_lift",
            "minimum_discovery_lift": MIN_DISCOVERY_LIFT,
            "minimum_holdout_lift": MIN_HOLDOUT_LIFT,
            "multiple_testing_policy": "bounded vocabulary + MDL model-cost penalty; FDR gate reserved for promotion-stage inference",
            "holdout": "later_time_slice_not_used_for_selection",
            "mdl": "Jeffreys Bernoulli universal code + pattern vocabulary description cost",
            "residual": "exceptions remain explicit and are mined only as diagnostics",
        },
        "sample": {
            "eligible_verified_forecasts": len(rows),
            "eligible_groups": len(groups),
            "groups_mined": groups_mined,
            "patterns_published": len(patterns),
            "replicated": counts.get("REPLICATED", 0),
            "oos_pass": counts.get("OOS PASS", 0),
            "discovery": counts.get("DISCOVERY", 0),
            "unstable": counts.get("UNSTABLE", 0),
        },
        "patterns": patterns,
    }
    validate_pattern_report(report)
    return report


def validate_pattern_report(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get("mode") != MODE:
        raise ValueError("invalid Evidence pattern report contract")
    if report.get("causal_status") != CAUSAL_STATUS:
        raise ValueError("Evidence patterns must remain association-only")
    authority = report.get("authority") or {}
    violations = [
        k for k, expected in AUTHORITY.items() if authority.get(k) is not expected
    ]
    if violations:
        raise ValueError(
            "Evidence pattern authority invariant failed: " + ",".join(sorted(violations))
        )
    for p in report.get("patterns") or []:
        if p.get("causal_status") != CAUSAL_STATUS:
            raise ValueError("pattern escaped association-only boundary")
        if float((p.get("discovery") or {}).get("mdl_gain_bits") or 0.0) <= 0:
            raise ValueError("published pattern must have positive discovery MDL gain")


def build_from_state_dir(state_dir: Path, output_dir: Path) -> dict[str, Any]:
    state_path = Path(state_dir) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    report = build_pattern_report(state)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "aris_pattern_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build BRIEFROOMS-PATTERN-2 shadow report")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    report = build_from_state_dir(Path(args.state_dir), Path(args.output_dir))
    if args.print_summary:
        print(json.dumps(report["sample"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
