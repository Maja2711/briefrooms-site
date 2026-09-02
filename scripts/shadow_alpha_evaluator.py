#!/usr/bin/env python3
"""P0 shadow-trading evaluator for current BriefRooms engines.

This module evaluates settled Experience Store records. It never labels raw
positive PnL as formal alpha unless benchmark-adjusted returns are present.
Without a benchmark it reports conservative evidence of a positive raw edge.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from experience_store import read_experiences
except ModuleNotFoundError:
    from scripts.experience_store import read_experiences

SCHEMA_VERSION = "briefrooms-shadow-alpha-report-v1"
DEFAULT_STORE = Path("data/research/experience_store.jsonl")
DEFAULT_REPORT = Path("data/research/shadow_alpha_report.json")
MIN_SAMPLE = 30
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 1337


def _number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * p
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    weight = index - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def bootstrap_mean_ci(values: list[float], *, samples: int = BOOTSTRAP_SAMPLES) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return (None, None)
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = [statistics.fmean(rng.choice(values) for _ in range(n)) for _ in range(samples)]
    return _percentile(means, 0.025), _percentile(means, 0.975)


def max_drawdown(returns: Iterable[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0:
            worst = min(worst, equity / peak - 1.0)
    return worst


def profit_factor(returns: list[float]) -> float | None:
    gains = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    if losses == 0:
        return None if gains == 0 else math.inf
    return gains / losses


def _series(rows: list[Mapping[str, Any]]) -> tuple[list[float], list[float]]:
    raw: list[float] = []
    excess: list[float] = []
    for row in rows:
        outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
        net = _number(outcome.get("net_return_fraction"))
        if net is None:
            continue
        raw.append(net)
        benchmark = _number(outcome.get("benchmark_return_fraction"))
        if benchmark is not None:
            excess.append(net - benchmark)
    return raw, excess


def _evidence_status(values: list[float], *, minimum: int) -> tuple[str, tuple[float | None, float | None]]:
    ci = bootstrap_mean_ci(values)
    if len(values) < minimum:
        return "INSUFFICIENT_DATA", ci
    mean = statistics.fmean(values)
    low = ci[0]
    if mean > 0 and low is not None and low > 0:
        return "POSITIVE_EVIDENCE", ci
    return "NO_POSITIVE_EVIDENCE", ci


def evaluate_group(rows: list[Mapping[str, Any]], *, minimum: int = MIN_SAMPLE) -> dict[str, Any]:
    settled = [row for row in rows if str(row.get("status") or "") == "SETTLED"]
    raw, excess = _series(settled)
    raw_status, raw_ci = _evidence_status(raw, minimum=minimum)
    formal_alpha_available = len(excess) == len(raw) and bool(raw)
    if formal_alpha_available:
        alpha_status, alpha_ci = _evidence_status(excess, minimum=minimum)
        assessment = alpha_status
        assessment_basis = "benchmark_adjusted_return"
    else:
        alpha_status, alpha_ci = "NOT_MEASURABLE", (None, None)
        assessment = raw_status
        assessment_basis = "raw_edge_only_no_complete_benchmark"

    mean = statistics.fmean(raw) if raw else None
    stdev = statistics.stdev(raw) if len(raw) > 1 else None
    win_rate = sum(1 for x in raw if x > 0) / len(raw) if raw else None
    pf = profit_factor(raw) if raw else None
    if pf is not None and math.isinf(pf):
        pf_value: float | str | None = "inf"
    else:
        pf_value = pf

    mae_values: list[float] = []
    mfe_values: list[float] = []
    for row in settled:
        outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
        mae = _number(outcome.get("mae_fraction"))
        mfe = _number(outcome.get("mfe_fraction"))
        if mae is not None:
            mae_values.append(mae)
        if mfe is not None:
            mfe_values.append(mfe)

    return {
        "assessment": assessment,
        "assessment_basis": assessment_basis,
        "formal_alpha": {
            "available": formal_alpha_available,
            "status": alpha_status,
            "sample_size": len(excess),
            "mean_excess_return_fraction": statistics.fmean(excess) if excess else None,
            "mean_95pct_bootstrap_ci": list(alpha_ci),
        },
        "raw_edge": {
            "status": raw_status,
            "sample_size": len(raw),
            "mean_net_return_fraction": mean,
            "mean_95pct_bootstrap_ci": list(raw_ci),
            "win_rate": win_rate,
            "profit_factor": pf_value,
            "return_signal_to_noise": (mean / stdev) if mean is not None and stdev not in (None, 0.0) else None,
            "max_drawdown_fraction": max_drawdown(raw) if raw else None,
            "cumulative_compounded_return_fraction": math.prod(1.0 + x for x in raw) - 1.0 if raw else None,
            "avg_mae_fraction": statistics.fmean(mae_values) if mae_values else None,
            "avg_mfe_fraction": statistics.fmean(mfe_values) if mfe_values else None,
        },
        "records": {
            "all_decisions": len(rows),
            "settled": len(settled),
            "settled_with_return": len(raw),
            "pending": sum(1 for row in rows if str(row.get("status") or "") == "PENDING"),
        },
        "minimum_sample_gate": minimum,
    }


def evaluate(experiences: list[Mapping[str, Any]], *, minimum: int = MIN_SAMPLE) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    instruments: dict[str, list[Mapping[str, Any]]] = {}
    for row in experiences:
        engine = str(row.get("engine") or "unknown")
        groups.setdefault(engine, []).append(row)
        instrument = str(row.get("instrument") or "unknown")
        instruments.setdefault(f"{engine}:{instrument}", []).append(row)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "purpose": "P0 shadow-trading evidence check; no execution or automatic promotion authority",
        "interpretation": {
            "POSITIVE_EVIDENCE": "95% bootstrap interval for mean return is above zero after the minimum sample gate.",
            "NO_POSITIVE_EVIDENCE": "The minimum sample exists, but current data do not support a positive mean return at this threshold.",
            "INSUFFICIENT_DATA": "Too few settled return observations to decide.",
            "formal_alpha_rule": "Formal alpha is assessed only when every evaluated return has a benchmark return; otherwise only raw edge is reported.",
        },
        "overall": evaluate_group(experiences, minimum=minimum),
        "by_engine": {key: evaluate_group(value, minimum=minimum) for key, value in sorted(groups.items())},
        "by_engine_instrument": {key: evaluate_group(value, minimum=minimum) for key, value in sorted(instruments.items())},
        "zero_authority": True,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate whether current shadow engines show evidence of alpha/raw edge")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--minimum-sample", type=int, default=MIN_SAMPLE)
    args = parser.parse_args()
    if args.minimum_sample < 2:
        parser.error("--minimum-sample must be >= 2")
    report = evaluate(read_experiences(args.store), minimum=args.minimum_sample)
    _atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
