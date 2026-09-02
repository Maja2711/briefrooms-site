#!/usr/bin/env python3
"""Build the canonical BriefRooms Experiment Registry.

The registry is a read-only research inventory. It summarizes logical experiments
and challengers from their existing public/research state files. It does not
change production decisions, tune models, promote challengers, or infer alpha
from raw positive returns.

Market benchmarks are comparison metadata, not an experiment category. They are
used only where they are economically meaningful (for example broad equity
indices/ETFs for equity strategies). FX and crypto strategies are evaluated on
absolute/net edge and risk metrics without inventing a benchmark.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "briefrooms-experiment-registry-v1"
DEFAULT_OUTPUT = Path("data/investments/experiment_registry.json")
ALLOWED_STATUSES = {
    "RUNNING",
    "INSUFFICIENT_DATA",
    "CONTINUE",
    "PROMOTE",
    "PARK",
    "KILL",
    "ERROR",
}
ALLOWED_CATEGORIES = {"trading", "forecasting", "belief", "learning"}


def _load(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except ValueError:
        return None


def _metric(label: str, value: Any, unit: str | None = None, interpretation: str | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "interpretation": interpretation,
    }


def _base(
    *,
    experiment_id: str,
    name: str,
    category: str,
    family: str,
    version: str,
    started_at: str | None,
    minimum_sample: int | None,
    purpose: str,
    source: str,
) -> dict[str, Any]:
    return {
        "id": experiment_id,
        "name": name,
        "category": category,
        "family": family,
        "system_class": "LAB",
        "stage": "SHADOW",
        "version": version,
        "started_at": started_at,
        "minimum_sample": minimum_sample,
        "sample_count": None,
        "sample_unit": None,
        "status": "INSUFFICIENT_DATA",
        "production_impact": False,
        "automatic_promotion": False,
        "primary_metric": _metric("Brak wystarczających danych", None),
        "benchmark": None,
        "delta_vs_benchmark": None,
        "max_drawdown": None,
        "last_updated": None,
        "source": source,
        "purpose": purpose,
        "details": {},
        "notes": [],
    }


def _research_lab(root: Path) -> dict[str, Any]:
    src = "data/investments/research_lab_report.json"
    data = _load(root, src)
    row = _base(
        experiment_id="strategy-research-lab",
        name="Autonomous Strategy Research Lab",
        category="learning",
        family="Weekly Investments",
        version=str(data.get("version") or "1.0.0"),
        started_at=None,
        minimum_sample=30,
        purpose="Autonomiczne generowanie i filtrowanie kandydatów strategii bez prawa do produkcyjnej aktywacji.",
        source=src,
    )
    cycles = _int(data.get("cycle"))
    row["sample_count"] = cycles
    row["sample_unit"] = "research_cycles"
    row["last_updated"] = _iso(data.get("generated_at"))
    row["status"] = "RUNNING" if cycles is not None and cycles >= 30 else "INSUFFICIENT_DATA"
    row["primary_metric"] = _metric(
        "Kandydaci wygenerowani w cyklu",
        _int(data.get("generated_this_cycle")),
        "candidates",
        "To miara aktywności badawczej, nie wynik inwestycyjny ani alpha.",
    )
    row["details"] = {
        "promotion_registry_count": _int(data.get("promotion_registry_count")),
        "governance": data.get("governance"),
    }
    row["notes"] = [
        "Brak automatycznej promocji do produkcji.",
        "Wynik kandydata wymaga holdout, walk-forward, kosztów i stabilności reżimowej.",
    ]
    return row


def _eurusd_abc(root: Path) -> dict[str, Any]:
    src = "data/investments/eurusd_abc_public_pl.json"
    data = _load(root, src)
    row = _base(
        experiment_id="eurusd-abc-live-shadow",
        name="EURUSD A/B/C Live Shadow",
        category="trading",
        family="EURUSD",
        version=str(data.get("engine_version") or "eurusd-daily-abc"),
        started_at=None,
        minimum_sample=40,
        purpose="Prospektywne porównanie trzech wariantów: technicznego, Belief-only i hybrydowego.",
        source=src,
    )
    sample = data.get("sample") if isinstance(data.get("sample"), Mapping) else {}
    captures = _int(sample.get("captures"))
    arm_stats: dict[str, dict[str, Any]] = {}
    for arm in ("A", "B", "C"):
        correct = 0
        resolved = 0
        signed_bps: list[float] = []
        for item in data.get("history", []) if isinstance(data.get("history"), list) else []:
            if not isinstance(item, Mapping):
                continue
            horizons = item.get("horizons") if isinstance(item.get("horizons"), Mapping) else {}
            horizon = horizons.get("60m") if isinstance(horizons.get("60m"), Mapping) else horizons.get("1h")
            if not isinstance(horizon, Mapping):
                continue
            arms = horizon.get("arms") if isinstance(horizon.get("arms"), Mapping) else {}
            payload = arms.get(arm) if isinstance(arms.get(arm), Mapping) else {}
            flag = payload.get("directional_correct")
            if isinstance(flag, bool):
                resolved += 1
                correct += int(flag)
            bps = _float(payload.get("signed_return_bps"))
            if bps is not None:
                signed_bps.append(bps)
        arm_stats[arm] = {
            "resolved_1h": resolved,
            "directional_accuracy_1h": (correct / resolved) if resolved else None,
            "mean_signed_return_bps_1h": (sum(signed_bps) / len(signed_bps)) if signed_bps else None,
        }
    effective = min((v["resolved_1h"] for v in arm_stats.values()), default=0)
    row["sample_count"] = effective
    row["sample_unit"] = "resolved_1h_comparisons_per_arm"
    row["last_updated"] = _iso(data.get("generated_at"))
    row["status"] = "RUNNING" if effective >= 40 else "INSUFFICIENT_DATA"
    best = max(
        (v.get("directional_accuracy_1h") for v in arm_stats.values() if v.get("directional_accuracy_1h") is not None),
        default=None,
    )
    row["primary_metric"] = _metric(
        "Najlepsza trafność kierunku 1h",
        best,
        "fraction",
        "Forecast skill; nie jest automatycznie formalną alpha ani wynikiem transakcyjnym po kosztach.",
    )
    row["details"] = {"captures": captures, "arms": arm_stats, "mode": data.get("mode")}
    row["notes"] = [
        "FLAT i nierozstrzygnięte obserwacje nie są sztucznie liczone jako trafienia.",
        "EUR/USD nie ma naturalnego benchmarku rynkowego; ocena handlowa pochodzi z absolutnego wyniku po kosztach i metryk ryzyka.",
        "Ocena handlowa powinna docelowo pochodzić z Experience Store po kosztach.",
    ]
    return row


def _timesfm(root: Path) -> dict[str, Any]:
    src = "data/investments/timesfm_shadow_public_pl.json"
    data = _load(root, src)
    experiment = data.get("experiment") if isinstance(data.get("experiment"), Mapping) else {}
    row = _base(
        experiment_id="timesfm-shadow",
        name="TimesFM Shadow Forecaster",
        category="forecasting",
        family="EURUSD",
        version=str(experiment.get("model_id") or "TimesFM"),
        started_at=_iso(experiment.get("activated_at")),
        minimum_sample=60,
        purpose="Sprawdzenie, czy TimesFM daje prospektywną przewagę prognostyczną względem prostych baseline'ów.",
        source=src,
    )
    resolved = 0
    correct = 0
    for item in data.get("history", []) if isinstance(data.get("history"), list) else []:
        if not isinstance(item, Mapping):
            continue
        horizons = item.get("horizons") if isinstance(item.get("horizons"), Mapping) else {}
        one_h = horizons.get("1h") if isinstance(horizons.get("1h"), Mapping) else {}
        flag = one_h.get("direction_correct")
        if isinstance(flag, bool):
            resolved += 1
            correct += int(flag)
    accuracy = correct / resolved if resolved else None
    row["sample_count"] = resolved
    row["sample_unit"] = "resolved_1h_forecasts"
    row["last_updated"] = _iso(data.get("generated_at"))
    row["status"] = "RUNNING" if resolved >= 60 else "INSUFFICIENT_DATA"
    row["primary_metric"] = _metric("Trafność kierunku 1h", accuracy, "fraction", "Miara forecast skill; nie formalna alpha.")
    row["benchmark"] = _metric("Losowy kierunek", 0.5, "fraction", "Baseline prognostyczny, nie benchmark rynkowy/PnL.")
    row["delta_vs_benchmark"] = (accuracy - 0.5) if accuracy is not None else None
    row["details"] = {"research_only": experiment.get("research_only"), "decision_influence": experiment.get("decision_influence")}
    row["notes"] = ["Dla EUR/USD nie przypisujemy sztucznego benchmarku rynkowego."]
    return row


def _gse(root: Path) -> dict[str, Any]:
    src = "data/gse/gse_v2_lab_public.json"
    data = _load(root, src)
    engine = data.get("engine") if isinstance(data.get("engine"), Mapping) else {}
    best = data.get("best_horizon") if isinstance(data.get("best_horizon"), Mapping) else {}
    challenger = data.get("challenger") if isinstance(data.get("challenger"), Mapping) else {}
    row = _base(
        experiment_id="gse-v2-learning-lab",
        name="GSE v2 Learning Lab",
        category="forecasting",
        family="Geopolitics",
        version="GSE v2",
        started_at=None,
        minimum_sample=60,
        purpose="Uczenie i kalibracja Geopolitical Scenario Engine na zweryfikowanych historycznych analogach i holdoutach.",
        source=src,
    )
    n = _int(best.get("n"))
    improvement = _float(best.get("brier_improvement_pct"))
    row["sample_count"] = n
    row["sample_unit"] = "evaluated_events_best_horizon"
    activity = data.get("activity") if isinstance(data.get("activity"), Mapping) else {}
    row["last_updated"] = _iso(activity.get("projection_generated_at") or activity.get("last_learning_at"))
    challenger_status = str(challenger.get("status") or "")
    if n is None or n < 60:
        row["status"] = "INSUFFICIENT_DATA"
    elif challenger_status == "eligible_for_human_shadow_review":
        row["status"] = "CONTINUE"
    else:
        row["status"] = "RUNNING"
    row["primary_metric"] = _metric(
        "Poprawa Brier score vs baseline",
        improvement,
        "percent",
        "Niższy Brier jest lepszy; wartość pokazuje poprawę względem zamrożonego baseline'u.",
    )
    row["benchmark"] = _metric("Baseline Brier", _float(best.get("baseline_brier")), "score")
    row["delta_vs_benchmark"] = improvement
    row["details"] = {
        "best_horizon": best.get("label"),
        "hit_rate": _float(best.get("hit_rate")),
        "challenger_status": challenger_status or None,
        "automatic_promotion": challenger.get("automatically_applied"),
        "decision_influence": engine.get("decision_influence"),
    }
    return row


def _brace_spx(root: Path) -> dict[str, Any]:
    src = "data/public/brace_spx_generation6_public.json"
    data = _load(root, src)
    shadow = data.get("shadow") if isinstance(data.get("shadow"), Mapping) else {}
    development = data.get("development") if isinstance(data.get("development"), Mapping) else {}
    row = _base(
        experiment_id="brace-spx-generation6",
        name="BRACE-SPX Generation 6",
        category="trading",
        family="SPX",
        version=str(data.get("generation_id") or "Generation 6"),
        started_at=_iso(shadow.get("start")),
        minimum_sample=_int(shadow.get("warmup_required")) or 70,
        purpose="Zamrożony challenger BRACE-SPX z ortogonalnymi rodzinami sygnałów, działający bez zleceń live.",
        source=src,
    )
    n = _int(shadow.get("observations_collected"))
    minimum = row["minimum_sample"] or 70
    row["sample_count"] = n
    row["sample_unit"] = "shadow_sessions"
    row["last_updated"] = _iso(shadow.get("updated_at") or data.get("generated_at"))
    if n is None or n < minimum:
        row["status"] = "INSUFFICIENT_DATA"
    elif bool(development.get("single_champion_authorized")):
        row["status"] = "PROMOTE"
    else:
        row["status"] = "RUNNING"
    row["primary_metric"] = _metric(
        "Postęp warm-up",
        (n / minimum) if n is not None and minimum else None,
        "fraction",
        "Nie jest to wynik PnL; pełna ocena następuje po wymaganej liczbie obserwacji.",
    )
    row["details"] = {
        "observations_remaining": _int(shadow.get("observations_remaining")),
        "shadow_status": shadow.get("status"),
        "strict_gate_passed": development.get("strict_gate_passed"),
        "live_orders": shadow.get("live_orders"),
    }
    row["notes"] = ["Dla strategii akcyjnych benchmark rynkowy może być szerokim indeksem lub ETF-em, np. S&P 500 / SPY, dobranym przed eksperymentem."]
    return row


def _wes(root: Path) -> dict[str, Any]:
    src = "data/investments/wes_incremental_alpha_report.json"
    data = _load(root, src)
    overall = data.get("overall") if isinstance(data.get("overall"), Mapping) else {}
    sample = data.get("sample") if isinstance(data.get("sample"), Mapping) else {}
    minimum = _int(sample.get("minimum_before_descriptive_analysis")) or 12
    row = _base(
        experiment_id="wes-incremental-alpha",
        name="WES Incremental Learning",
        category="learning",
        family="WES",
        version=str(data.get("schema_version") or "WES"),
        started_at=None,
        minimum_sample=minimum,
        purpose="Prospektywne sprawdzenie, czy WES wnosi inkrementalną wartość ponad zamrożony baseline V5.",
        source=src,
    )
    n = _int(overall.get("resolved_pairs")) or 0
    alpha = _float(overall.get("mean_incremental_alpha_percent"))
    row["sample_count"] = n
    row["sample_unit"] = "resolved_counterfactual_pairs"
    row["status"] = "INSUFFICIENT_DATA" if n < minimum else "RUNNING"
    row["primary_metric"] = _metric("Średnia inkrementalna alpha", alpha, "percent", "WES minus prospektywnie zamrożony baseline dla tej samej decyzji.")
    row["benchmark"] = _metric("Frozen V5 baseline", 0.0, "incremental_percent")
    row["delta_vs_benchmark"] = alpha
    row["details"] = {
        "sample_status": sample.get("status"),
        "economic_decisions": _int(sample.get("economic_decisions")),
        "active_decision_influence": data.get("active_decision_influence"),
    }
    row["notes"] = ["Historyczny backfill jest zabroniony; liczona jest tylko próbka prospektywna."]
    return row


def _aris(_: Path) -> dict[str, Any]:
    row = _base(
        experiment_id="belief-aris-shadow",
        name="Belief / ARIS Shadow",
        category="belief",
        family="Belief Core",
        version="ARIS Shadow",
        started_at=None,
        minimum_sample=30,
        purpose="Sprawdzenie wartości reprezentacji ARIS w Belief Core bez wpływu na decyzje i bez writebacku.",
        source="workflow artifact: belief-aris-shadow-report",
    )
    row["sample_count"] = None
    row["sample_unit"] = "shadow_reports"
    row["status"] = "INSUFFICIENT_DATA"
    row["primary_metric"] = _metric(
        "Wartość inkrementalna",
        None,
        None,
        "Raport jest przechowywany jako artefakt workflow; brak kanonicznej publicznej projekcji metryk w repo.",
    )
    row["notes"] = ["Zero decision influence.", "Zero Belief Core writeback.", "Zero automatic tuning/promotion."]
    return row


def build_registry(root: Path) -> dict[str, Any]:
    # AI Tournament is deliberately excluded. It is a public one-off/fun
    # comparison of frozen LLM picks, not a learning experiment and not an
    # input into Experience Store, promotion gates or future model training.
    builders = (_research_lab, _eurusd_abc, _timesfm, _gse, _brace_spx, _wes, _aris)
    experiments = [builder(root) for builder in builders]
    experiments.sort(key=lambda item: (str(item.get("category")), str(item.get("name"))))

    valid_timestamps = [_iso(item.get("last_updated")) for item in experiments]
    parsed = []
    for value in valid_timestamps:
        if value:
            try:
                parsed.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                pass
    generated_at = (
        max(parsed).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if parsed
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    for item in experiments:
        if item["status"] not in ALLOWED_STATUSES:
            item["status"] = "ERROR"
        if item["category"] not in ALLOWED_CATEGORIES:
            item["status"] = "ERROR"

    statuses = [item["status"] for item in experiments]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "authority": {
            "read_only": True,
            "production_decision_influence": False,
            "automatic_model_tuning": False,
            "automatic_promotion": False,
            "purpose": "one source of truth for logical BriefRooms experiments; not a workflow inventory",
        },
        "benchmark_policy": {
            "benchmark_is_experiment_category": False,
            "equities": {
                "applicability": "WHEN_ECONOMICALLY_MEANINGFUL",
                "examples": ["broad equity index", "S&P 500", "SPY or equivalent ETF"],
                "rule": "Choose and freeze the benchmark before evaluation; use benchmark-adjusted return only when the comparison is economically valid.",
            },
            "fx": {
                "applicability": "NOT_APPLICABLE",
                "rule": "Do not invent a market benchmark for EUR/USD or other FX pairs; evaluate net return/edge, drawdown, costs, calibration and risk-adjusted metrics.",
            },
            "crypto": {
                "applicability": "NOT_APPLICABLE",
                "rule": "Do not invent a market benchmark for Bitcoin/crypto trading; evaluate net return/edge, drawdown, costs and risk-adjusted metrics.",
            },
            "forecasting_and_learning": {
                "applicability": "BASELINE_NOT_MARKET_BENCHMARK",
                "rule": "Random, persistence or frozen-model comparisons are baselines/references, not market benchmarks.",
            },
        },
        "status_policy": {
            "allowed": sorted(ALLOWED_STATUSES),
            "meaning": {
                "RUNNING": "Eksperyment ma wystarczającą próbkę do dalszej obserwacji, ale nie ma zgody na promocję.",
                "INSUFFICIENT_DATA": "Próbka jest za mała albo brak kanonicznej metryki do decyzji.",
                "CONTINUE": "Istnieje interesujący sygnał; wymagany dalszy shadow/holdout lub przegląd człowieka.",
                "PROMOTE": "Spełnione zdefiniowane bramki; nadal wymagana odrębna kontrola promocji.",
                "PARK": "Brak wystarczającej wartości obecnie; zachować kod i stan bez dalszego kosztu runtime/API.",
                "KILL": "Eksperyment zakończony i nie powinien dalej zużywać runtime/API.",
                "ERROR": "Nie można wiarygodnie odczytać lub sklasyfikować eksperymentu.",
            },
        },
        "summary": {
            "total": len(experiments),
            "active": sum(status in {"RUNNING", "CONTINUE"} for status in statuses),
            "awaiting_evidence": statuses.count("INSUFFICIENT_DATA"),
            "promotion_candidates": statuses.count("PROMOTE"),
            "parked_or_killed": sum(status in {"PARK", "KILL"} for status in statuses),
            "errors": statuses.count("ERROR"),
        },
        "experiments": experiments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical BriefRooms Experiment Registry")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_registry(args.root)
    output = args.output if args.output.is_absolute() else args.root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
