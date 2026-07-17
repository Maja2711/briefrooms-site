#!/usr/bin/env python3
"""Champion–challenger governance for BRACE historical validation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_backtest.json"

PROMOTION_THRESHOLDS = {
    "minimum_weeks": 260,
    "minimum_cagr_advantage": 0.005,
    "minimum_sharpe_advantage": 0.0,
    "maximum_drawdown_disadvantage": 0.02,
    "require_parameter_stability": True,
}


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def evaluate(backtest: Mapping[str, Any]) -> Dict[str, Any]:
    metrics = backtest.get("metrics") or {}
    baseline = metrics.get("baseline") or {}
    challenger = metrics.get("brace_standard") or {}
    robustness = backtest.get("robustness") or {}

    observed = {
        "weeks": int(finite(challenger.get("weeks"))),
        "cagr_advantage": finite(challenger.get("cagr")) - finite(baseline.get("cagr")),
        "sharpe_advantage": finite(challenger.get("sharpe_zero_rf")) - finite(baseline.get("sharpe_zero_rf")),
        "drawdown_disadvantage": abs(finite(challenger.get("max_drawdown"))) - abs(finite(baseline.get("max_drawdown"))),
        "parameter_stable": bool(robustness.get("stable_within_five_percentage_points")),
    }
    checks = {
        "minimum_history": observed["weeks"] >= PROMOTION_THRESHOLDS["minimum_weeks"],
        "cagr_advantage": observed["cagr_advantage"] >= PROMOTION_THRESHOLDS["minimum_cagr_advantage"],
        "sharpe_advantage": observed["sharpe_advantage"] >= PROMOTION_THRESHOLDS["minimum_sharpe_advantage"],
        "drawdown_control": observed["drawdown_disadvantage"] <= PROMOTION_THRESHOLDS["maximum_drawdown_disadvantage"],
        "parameter_stability": (
            observed["parameter_stable"]
            if PROMOTION_THRESHOLDS["require_parameter_stability"] else True
        ),
    }
    promoted = bool(checks) and all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    if promoted:
        reason_pl = "BRACE-Lite spełnił wszystkie z góry określone kryteria awansu. Model nadal wymaga potwierdzenia w trybie live-shadow przed zmianą modelu oficjalnego."
        reason_en = "BRACE-Lite passed every pre-defined promotion criterion. Live-shadow confirmation is still required before replacing the official model."
    else:
        reason_pl = (
            "BRACE pozostaje challengerem. Nie spełnił następujących kryteriów awansu: "
            + ", ".join(failed)
            + ". Model bazowy pozostaje championem; parametrów nie dopasowujemy po fakcie."
        )
        reason_en = (
            "BRACE remains the challenger. It failed these promotion criteria: "
            + ", ".join(failed)
            + ". The baseline remains champion; parameters are not retuned after seeing the result."
        )
    return {
        "status": "eligible_for_live_confirmation" if promoted else "not_promoted",
        "champion": "baseline" if not promoted else "brace_standard_candidate",
        "challenger": "brace_standard",
        "thresholds": PROMOTION_THRESHOLDS,
        "observed": {key: round(value, 6) if isinstance(value, float) else value for key, value in observed.items()},
        "checks": checks,
        "failed_checks": failed,
        "reason_pl": reason_pl,
        "reason_en": reason_en,
    }


def apply(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["promotion_gate"] = evaluate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", type=Path, default=BACKTEST_PATH)
    args = parser.parse_args()
    data = apply(args.backtest)
    gate = data["promotion_gate"]
    print(f"BRACE promotion gate: {gate['status']} ({', '.join(gate['failed_checks']) or 'all checks passed'})")


if __name__ == "__main__":
    main()
