#!/usr/bin/env python3
"""Generate and permanently lock the BRACE portfolio for AI Tournament 2026."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "ai_tournament" / "config.json"
SUBMISSION_PATH = ROOT / "data" / "ai_tournament" / "submissions" / "brace.json"
COMMITMENT_PATH = ROOT / "data" / "ai_tournament" / "commitments" / "brace.json"
AUDIT_PATH = ROOT / "data" / "ai_tournament" / "brace_selection_audit.json"

ALGORITHM_VERSION = "brace-buy-hold-v1"
FREEZE_DATE = "2026-07-31"
DECISION_DATE = "2026-08-02"
EXECUTION_DATE = "2026-08-03"
END_DATE = "2026-11-03"
EXPECTED_UNIVERSE_SIZE = 55

COMPANY_BY_TICKER: dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon",
    "GOOGL": "Alphabet Class A", "META": "Meta Platforms", "AVGO": "Broadcom",
    "TSLA": "Tesla", "JPM": "JPMorgan Chase", "V": "Visa", "MA": "Mastercard",
    "XOM": "Exxon Mobil", "LLY": "Eli Lilly", "UNH": "UnitedHealth Group",
    "COST": "Costco Wholesale", "WMT": "Walmart", "HD": "Home Depot",
    "PG": "Procter & Gamble", "KO": "Coca-Cola", "PEP": "PepsiCo",
    "NFLX": "Netflix", "AMD": "Advanced Micro Devices", "ORCL": "Oracle",
    "CRM": "Salesforce", "QCOM": "Qualcomm", "TSM": "Taiwan Semiconductor Manufacturing",
    "ASML": "ASML Holding", "ARM": "Arm Holdings", "INTC": "Intel", "MU": "Micron Technology",
    "AMAT": "Applied Materials", "LRCX": "Lam Research", "KLAC": "KLA Corporation",
    "MRVL": "Marvell Technology", "ANET": "Arista Networks", "DELL": "Dell Technologies",
    "SMCI": "Super Micro Computer", "IBM": "International Business Machines",
    "ADBE": "Adobe", "NOW": "ServiceNow", "PLTR": "Palantir Technologies",
    "SNOW": "Snowflake", "PANW": "Palo Alto Networks", "CRWD": "CrowdStrike",
    "DDOG": "Datadog", "NET": "Cloudflare", "MDB": "MongoDB", "SHOP": "Shopify",
    "UBER": "Uber Technologies", "INTU": "Intuit", "PATH": "UiPath", "AI": "C3.ai",
    "SOUN": "SoundHound AI", "SAP": "SAP SE", "APP": "AppLovin",
}

CATEGORY_BY_TICKER: dict[str, str] = {
    "AAPL": "mega_platform", "MSFT": "mega_platform", "NVDA": "semiconductors",
    "AMZN": "mega_platform", "GOOGL": "mega_platform", "META": "mega_platform",
    "AVGO": "semiconductors", "TSLA": "consumer_digital", "JPM": "financials",
    "V": "financials", "MA": "financials", "XOM": "energy", "LLY": "healthcare",
    "UNH": "healthcare", "COST": "consumer_defensive", "WMT": "consumer_defensive",
    "HD": "consumer_defensive", "PG": "consumer_defensive", "KO": "consumer_defensive",
    "PEP": "consumer_defensive", "NFLX": "consumer_digital", "AMD": "semiconductors",
    "ORCL": "enterprise_software", "CRM": "enterprise_software", "QCOM": "semiconductors",
    "TSM": "semiconductors", "ASML": "semiconductors", "ARM": "semiconductors",
    "INTC": "semiconductors", "MU": "semiconductors", "AMAT": "semiconductors",
    "LRCX": "semiconductors", "KLAC": "semiconductors", "MRVL": "semiconductors",
    "ANET": "cyber_network", "DELL": "hardware_infrastructure",
    "SMCI": "hardware_infrastructure", "IBM": "enterprise_software",
    "ADBE": "enterprise_software", "NOW": "enterprise_software",
    "PLTR": "enterprise_software", "SNOW": "enterprise_software",
    "PANW": "cyber_network", "CRWD": "cyber_network", "DDOG": "enterprise_software",
    "NET": "cyber_network", "MDB": "enterprise_software", "SHOP": "consumer_digital",
    "UBER": "consumer_digital", "INTU": "enterprise_software",
    "PATH": "enterprise_software", "AI": "enterprise_software",
    "SOUN": "consumer_digital", "SAP": "enterprise_software", "APP": "consumer_digital",
}
CATEGORY_CAPS = {
    "semiconductors": 2,
    "mega_platform": 2,
    "enterprise_software": 2,
    "cyber_network": 1,
    "hardware_infrastructure": 1,
    "financials": 1,
    "healthcare": 1,
    "consumer_defensive": 1,
    "consumer_digital": 1,
    "energy": 1,
}


class BraceSelectionError(RuntimeError):
    """Raised when BRACE cannot produce a valid locked portfolio."""


@dataclass(frozen=True)
class Metrics:
    ticker: str
    category: str
    close: float
    return_20d: float
    return_60d: float
    relative_20d: float
    relative_60d: float
    sma20_gap: float
    sma60_gap: float
    volatility_20d: float
    drawdown_60d: float
    score: float


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_config(value)
    return value


def validate_config(config: Mapping[str, Any]) -> None:
    universe = config.get("universe")
    if not isinstance(universe, list) or len(universe) != EXPECTED_UNIVERSE_SIZE:
        raise BraceSelectionError(f"BRACE requires exactly {EXPECTED_UNIVERSE_SIZE} stocks")
    if len(set(universe)) != len(universe):
        raise BraceSelectionError("BRACE universe contains duplicate tickers")
    missing_categories = sorted(set(universe) - set(CATEGORY_BY_TICKER))
    if missing_categories:
        raise BraceSelectionError(f"Missing categories for: {', '.join(missing_categories)}")
    forbidden = {"SPY", "QQQ", "IWM", "GLD", "TLT"}
    if forbidden.intersection(universe):
        raise BraceSelectionError("ETF or non-stock instrument found in BRACE universe")
    rules = config.get("rules") or {}
    if int(rules.get("max_positions", 0)) != 6:
        raise BraceSelectionError("BRACE tournament requires max_positions=6")
    if float(rules.get("max_position_weight", 0)) != 0.30:
        raise BraceSelectionError("BRACE tournament requires 30% position cap")
    if float(rules.get("min_position_weight", 0)) != 0.05:
        raise BraceSelectionError("BRACE tournament requires 5% minimum position")
    if float(rules.get("max_cash_weight", 0)) != 0.20:
        raise BraceSelectionError("BRACE tournament requires 20% cash cap")


def _returns(closes: Sequence[float]) -> list[float]:
    return [
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]


def calculate_metrics(
    ticker: str,
    closes: Sequence[float],
    benchmark_closes: Sequence[float],
) -> Metrics:
    clean = [float(value) for value in closes if math.isfinite(float(value)) and float(value) > 0]
    bench = [float(value) for value in benchmark_closes if math.isfinite(float(value)) and float(value) > 0]
    if len(clean) < 61 or len(bench) < 61:
        raise BraceSelectionError(f"At least 61 closes are required for {ticker}")
    clean = clean[-130:]
    bench = bench[-130:]
    ret20 = clean[-1] / clean[-21] - 1.0
    ret60 = clean[-1] / clean[-61] - 1.0
    bench20 = bench[-1] / bench[-21] - 1.0
    bench60 = bench[-1] / bench[-61] - 1.0
    daily = _returns(clean)
    vol20 = statistics.stdev(daily[-20:]) * math.sqrt(252) if len(daily[-20:]) >= 2 else 0.0
    sma20 = statistics.mean(clean[-20:])
    sma60 = statistics.mean(clean[-60:])
    recent_peak = max(clean[-60:])
    sma20_gap = clean[-1] / sma20 - 1.0
    sma60_gap = clean[-1] / sma60 - 1.0
    drawdown = clean[-1] / recent_peak - 1.0
    relative20 = ret20 - bench20
    relative60 = ret60 - bench60
    score = (
        2.00 * ret20
        + 1.20 * ret60
        + 0.90 * sma20_gap
        + 0.60 * sma60_gap
        + 0.80 * relative20
        + 0.40 * relative60
        - 0.45 * vol20
        + 0.35 * drawdown
    )
    return Metrics(
        ticker=ticker,
        category=CATEGORY_BY_TICKER[ticker],
        close=clean[-1],
        return_20d=ret20,
        return_60d=ret60,
        relative_20d=relative20,
        relative_60d=relative60,
        sma20_gap=sma20_gap,
        sma60_gap=sma60_gap,
        volatility_20d=vol20,
        drawdown_60d=drawdown,
        score=score,
    )


def rank_universe(
    series_by_ticker: Mapping[str, Sequence[float]],
    benchmark_closes: Sequence[float],
    universe: Sequence[str],
) -> tuple[list[Metrics], list[dict[str, str]]]:
    ranked: list[Metrics] = []
    rejected: list[dict[str, str]] = []
    for ticker in universe:
        values = series_by_ticker.get(ticker)
        if not values:
            rejected.append({"ticker": ticker, "reason": "missing_price_series"})
            continue
        try:
            ranked.append(calculate_metrics(ticker, values, benchmark_closes))
        except Exception as exc:
            rejected.append({"ticker": ticker, "reason": str(exc)[:180]})
    ranked.sort(key=lambda row: (row.score, row.return_20d, row.ticker), reverse=True)
    if len(ranked) < 20:
        raise BraceSelectionError(f"Only {len(ranked)} stocks have sufficient frozen data")
    return ranked, rejected


def choose_stocks(ranked: Sequence[Metrics], count: int = 6) -> list[Metrics]:
    selected: list[Metrics] = []
    category_counts: dict[str, int] = {}
    for row in ranked:
        cap = CATEGORY_CAPS.get(row.category, 1)
        if category_counts.get(row.category, 0) >= cap:
            continue
        selected.append(row)
        category_counts[row.category] = category_counts.get(row.category, 0) + 1
        if len(selected) == count:
            break
    if len(selected) < count:
        raise BraceSelectionError("Diversification constraints left too few BRACE positions")
    return selected


def cash_weight_pct(benchmark_closes: Sequence[float]) -> int:
    clean = [float(value) for value in benchmark_closes if math.isfinite(float(value)) and float(value) > 0]
    if len(clean) < 61:
        raise BraceSelectionError("Benchmark requires at least 61 closes")
    ret20 = clean[-1] / clean[-21] - 1.0
    sma60_gap = clean[-1] / statistics.mean(clean[-60:]) - 1.0
    if ret20 >= 0 and sma60_gap >= 0:
        return 5
    if ret20 < -0.05 or sma60_gap < -0.05:
        return 20
    return 10


def rank_weights(cash_pct: int) -> list[int]:
    schedules = {
        5: [25, 20, 15, 15, 10, 10],
        10: [25, 20, 15, 10, 10, 10],
        20: [20, 15, 15, 10, 10, 10],
    }
    try:
        return schedules[cash_pct]
    except KeyError as exc:
        raise BraceSelectionError(f"Unsupported cash regime: {cash_pct}") from exc


def build_submission(selected: Sequence[Metrics], cash_pct: int) -> dict[str, Any]:
    weights = rank_weights(cash_pct)
    allocations = []
    for rank, (row, weight) in enumerate(zip(selected, weights), start=1):
        allocations.append(
            {
                "ticker": row.ticker,
                "company": COMPANY_BY_TICKER[row.ticker],
                "weight_pct": weight,
                "selection_reason": (
                    f"BRACE rank #{rank}: 20-session return {row.return_20d * 100:.2f}%, "
                    f"60-session return {row.return_60d * 100:.2f}%, "
                    f"distance from 60-session average {row.sma60_gap * 100:.2f}% "
                    f"and annualized 20-session volatility {row.volatility_20d * 100:.2f}%."
                ),
            }
        )
    highest_risk = max(selected, key=lambda row: (row.volatility_20d, -row.score))
    spread = selected[0].score - selected[-1].score
    confidence = max(55, min(82, round(64 + spread * 60)))
    submission = {
        "agent_name": "BRACE",
        "decision_date": DECISION_DATE,
        "execution_date": EXECUTION_DATE,
        "end_date": END_DATE,
        "strategy": "three_month_buy_and_hold",
        "same_allocations_for_pln_and_usd": True,
        "allocations": allocations,
        "cash_weight_pct": cash_pct,
        "portfolio_thesis": (
            "BRACE selected the strongest risk-adjusted medium-term trends from the frozen 55-stock universe. "
            "The algorithm combines absolute and benchmark-relative momentum, trend, volatility and drawdown, "
            "while category caps prevent a single AI segment from dominating the portfolio."
        ),
        "expected_three_month_driver": (
            "Continuation of risk-adjusted momentum and earnings revisions in the highest-ranked selected stocks."
        ),
        "biggest_portfolio_risk": (
            "A broad factor reversal that simultaneously weakens momentum leaders and compresses AI-related valuations."
        ),
        "expected_best_performer": selected[0].ticker,
        "expected_highest_risk_position": highest_risk.ticker,
        "confidence_pct": confidence,
        "final_decision_locked": True,
        "brace_metadata": {
            "algorithm_version": ALGORITHM_VERSION,
            "market_data_cutoff": FREEZE_DATE,
            "selection_mode": "deterministic_no_llm_no_api",
        },
    }
    validate_submission(submission)
    return submission


def validate_submission(submission: Mapping[str, Any]) -> None:
    allocations = submission.get("allocations")
    if not isinstance(allocations, list) or not 4 <= len(allocations) <= 6:
        raise BraceSelectionError("BRACE submission must contain 4-6 stocks")
    tickers = [str(row.get("ticker")) for row in allocations]
    if len(set(tickers)) != len(tickers):
        raise BraceSelectionError("BRACE submission contains duplicate stocks")
    weights = [int(row.get("weight_pct")) for row in allocations]
    if any(weight < 5 or weight > 30 for weight in weights):
        raise BraceSelectionError("BRACE position weight outside 5-30%")
    cash = int(submission.get("cash_weight_pct"))
    if cash < 0 or cash > 20:
        raise BraceSelectionError("BRACE cash weight outside 0-20%")
    if sum(weights) + cash != 100:
        raise BraceSelectionError("BRACE weights do not sum to 100%")
    if submission.get("final_decision_locked") is not True:
        raise BraceSelectionError("BRACE decision must be locked")


def _metrics_dict(row: Metrics) -> dict[str, Any]:
    return {
        "ticker": row.ticker,
        "category": row.category,
        "close": round(row.close, 8),
        "return_20d": round(row.return_20d, 10),
        "return_60d": round(row.return_60d, 10),
        "relative_20d": round(row.relative_20d, 10),
        "relative_60d": round(row.relative_60d, 10),
        "sma20_gap": round(row.sma20_gap, 10),
        "sma60_gap": round(row.sma60_gap, 10),
        "volatility_20d": round(row.volatility_20d, 10),
        "drawdown_60d": round(row.drawdown_60d, 10),
        "score": round(row.score, 12),
    }


def build_commitment(submission: Mapping[str, Any]) -> dict[str, Any]:
    digest = sha256_text(canonical_json(submission))
    stock_weight = sum(int(row["weight_pct"]) for row in submission["allocations"])
    return {
        "schema_version": "ai-tournament-manual-commitment-v1",
        "tournament_id": "briefrooms-ai-tournament-2026-01",
        "participant_id": "brace",
        "participant_display_name": "BRACE",
        "submission_mode": "deterministic_algorithm",
        "decision_date": DECISION_DATE,
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "locked": True,
        "canonicalization": "json_sort_keys_utf8_compact_v1",
        "sha256": digest,
        "validation": {
            "valid": True,
            "positions_count": len(submission["allocations"]),
            "stock_weight_pct": stock_weight,
            "cash_weight_pct": submission["cash_weight_pct"],
            "total_weight_pct": stock_weight + int(submission["cash_weight_pct"]),
            "all_tickers_allowed": True,
            "all_position_weights_between_5_and_30_pct": True,
            "same_allocations_for_pln_and_usd": True,
            "final_decision_locked": True,
        },
        "algorithm_version": ALGORITHM_VERSION,
        "market_data_cutoff": FREEZE_DATE,
        "publication_policy": "Reveal all five full allocations together after BRACE is locked.",
    }


def fetch_frozen_series(universe: Sequence[str]) -> tuple[dict[str, list[float]], list[float], dict[str, Any]]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise BraceSelectionError("yfinance is required to generate the BRACE submission") from exc

    tickers = list(universe) + ["SPY"]
    raw = yf.download(
        tickers=tickers,
        start="2026-01-01",
        end="2026-08-01",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    if raw is None or raw.empty:
        raise BraceSelectionError("Frozen market-data download returned no rows")

    def close_values(ticker: str) -> list[float]:
        frame = None
        if getattr(raw.columns, "nlevels", 1) > 1:
            level0 = list(raw.columns.get_level_values(0).unique())
            level1 = list(raw.columns.get_level_values(1).unique())
            if ticker in level0:
                frame = raw[ticker]
            elif ticker in level1:
                frame = raw.xs(ticker, level=1, axis=1)
        else:
            frame = raw
        if frame is None or "Close" not in frame:
            return []
        series = frame["Close"].dropna()
        if series.empty:
            return []
        last_date = str(series.index[-1].date())
        if last_date > FREEZE_DATE:
            series = series.loc[:FREEZE_DATE]
        return [float(value) for value in series.tolist()]

    series_by_ticker = {ticker: close_values(ticker) for ticker in universe}
    benchmark = close_values("SPY")
    data_manifest = {
        "provider": "Yahoo Finance via yfinance",
        "requested_start": "2026-01-01",
        "requested_end_exclusive": "2026-08-01",
        "market_data_cutoff": FREEZE_DATE,
        "available_tickers": sorted(ticker for ticker, values in series_by_ticker.items() if values),
        "series_lengths": {ticker: len(values) for ticker, values in sorted(series_by_ticker.items())},
        "benchmark_series_length": len(benchmark),
    }
    data_manifest["manifest_sha256"] = sha256_text(canonical_json(data_manifest))
    return series_by_ticker, benchmark, data_manifest


def generate(
    config_path: Path = CONFIG_PATH,
    submission_path: Path = SUBMISSION_PATH,
    commitment_path: Path = COMMITMENT_PATH,
    audit_path: Path = AUDIT_PATH,
) -> dict[str, Any]:
    if submission_path.exists() or commitment_path.exists():
        raise BraceSelectionError("BRACE is already locked; refusing to regenerate or overwrite it")
    config = load_config(config_path)
    universe = list(config["universe"])
    series_by_ticker, benchmark, manifest = fetch_frozen_series(universe)
    ranked, rejected = rank_universe(series_by_ticker, benchmark, universe)
    selected = choose_stocks(ranked, count=6)
    cash_pct = cash_weight_pct(benchmark)
    submission = build_submission(selected, cash_pct)
    commitment = build_commitment(submission)
    audit = {
        "schema_version": "brace-selection-audit-v1",
        "algorithm_version": ALGORITHM_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decision_date": DECISION_DATE,
        "market_data_cutoff": FREEZE_DATE,
        "universe_version": config["universe_version"],
        "universe_size": len(universe),
        "score_formula": (
            "2.00*r20 + 1.20*r60 + 0.90*sma20_gap + 0.60*sma60_gap + "
            "0.80*relative20 + 0.40*relative60 - 0.45*volatility20 + 0.35*drawdown60"
        ),
        "category_caps": CATEGORY_CAPS,
        "cash_regime": {
            "cash_weight_pct": cash_pct,
            "rules": {
                "risk_on": "SPY return_20d >= 0 and SPY sma60_gap >= 0 => 5%",
                "risk_off": "SPY return_20d < -5% or SPY sma60_gap < -5% => 20%",
                "neutral": "otherwise => 10%",
            },
        },
        "selected_tickers": [row.ticker for row in selected],
        "ranked_universe": [_metrics_dict(row) for row in ranked],
        "rejected_data": rejected,
        "data_manifest": manifest,
        "submission_sha256": commitment["sha256"],
    }
    for path, value in (
        (submission_path, submission),
        (commitment_path, commitment),
        (audit_path, audit),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return submission


def validate_locked(
    submission_path: Path = SUBMISSION_PATH,
    commitment_path: Path = COMMITMENT_PATH,
    audit_path: Path = AUDIT_PATH,
) -> None:
    submission = json.loads(submission_path.read_text(encoding="utf-8"))
    commitment = json.loads(commitment_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    validate_submission(submission)
    digest = sha256_text(canonical_json(submission))
    if digest != commitment.get("sha256"):
        raise BraceSelectionError("BRACE submission hash does not match commitment")
    if digest != audit.get("submission_sha256"):
        raise BraceSelectionError("BRACE submission hash does not match audit")
    if commitment.get("locked") is not True:
        raise BraceSelectionError("BRACE commitment is not locked")
    if audit.get("market_data_cutoff") != FREEZE_DATE:
        raise BraceSelectionError("BRACE audit uses the wrong market-data cutoff")
    print(f"BRACE locked portfolio valid: {digest}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "validate"))
    args = parser.parse_args()
    if args.command == "generate":
        submission = generate()
        print(json.dumps(submission, ensure_ascii=False, indent=2))
    else:
        validate_locked()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
