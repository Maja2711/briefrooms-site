#!/usr/bin/env python3
"""Point-in-time, zero-LLM SEC/XBRL fundamental research for Stock Trading v2.

Uses official SEC ticker mapping and companyfacts; refuses to substitute
missing accounting values, and never changes Champion admission or prices.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_discovery as discovery
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_discovery as discovery

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_fundamentals_config.json"
FRONTIER_ROOT = ROOT / "data/investments/stock_trading_v2_frontier"
OUTPUT_ROOT = ROOT / "data/investments/stock_trading_v2_fundamentals"
SCHEMA = "stock-trading-v2-fundamentals-v1"

# Never add two different revenue concepts. Prefer a consistent series.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_income": ("OperatingIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": ("StockholdersEquity",),
}
UNITS = {"eps_diluted": "USD/shares"}
INSTANT = {"assets", "liabilities", "equity"}
FINANCIAL_SECTORS = ("financial", "bank", "insurance", "capital markets")
SCORE_FACTORS = (
    ("revenue_growth_yoy", 25, -0.15, 0.20),
    ("eps_growth_yoy", 25, -0.30, 0.30),
    ("operating_margin", 20, -0.05, 0.25),
    ("fcf_margin", 20, -0.10, 0.20),
    ("return_on_equity", 10, -0.05, 0.25),
)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return _ratio(current - previous, previous)


def _record(fact: Mapping[str, Any], *, concept: str, unit: str) -> dict[str, Any]:
    return {
        "value": float(fact["val"]), "concept": f"us-gaap:{concept}", "unit": unit,
        "start": fact.get("start"), "end": fact["end"],
        "filed": fact["filed"], "form": fact["form"], "accession": fact.get("accn"),
    }


def annual_series(
    payload: Mapping[str, Any], metric: str, as_of: datetime,
) -> dict[str, dict[str, Any]]:
    """Annual 10-K facts only, exact fiscal start/end and conservatively filed earlier.

    SEC companyfacts contains restatements, prior-period comparatives and YTD
    10-Q facts. Never confuse one of those with a fresh annual observation.
    """
    names = CONCEPTS[metric]
    unit = UNITS.get(metric, "USD")
    taxonomy = ((payload.get("facts") or {}).get("us-gaap") or {})
    selected: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
    for priority, concept in enumerate(names):
        raw = ((taxonomy.get(concept) or {}).get("units") or {}).get(unit) or []
        for fact in raw:
            if not isinstance(fact, Mapping):
                continue
            if str(fact.get("form") or "") not in {"10-K", "10-K/A"}:
                continue
            filed, end = str(fact.get("filed") or ""), str(fact.get("end") or "")
            if not filed or not end or filed >= as_of.date().isoformat():
                # Filing dates alone lack acceptance time: same-day facts are excluded.
                continue
            value = _finite(fact.get("val"))
            if value is None:
                continue
            try:
                period_end = date.fromisoformat(end)
                if period_end > as_of.date():
                    continue
                if metric not in INSTANT:
                    start = date.fromisoformat(str(fact["start"]))
                    duration = (period_end - start).days
                    if not 330 <= duration <= 400:
                        continue
            except (ValueError, KeyError, TypeError):
                continue
            # Favor most recently filed restatement, then preferred taxonomy tag.
            rank = (filed, -priority)
            previous = selected.get(end)
            if previous is None or rank > previous[0]:
                selected[end] = (rank, _record(fact, concept=concept, unit=unit))
    return {period: item[1] for period, item in selected.items()}


def direct_quarter_series(
    payload: Mapping[str, Any], metric: str, as_of: datetime,
) -> dict[str, dict[str, Any]]:
    """Direct 3-month 10-Q XBRL facts, never YTD 6/9-month values or inferred Q4.

    A 10-Q may contain 3-, 6- and 9-month observations with the same end
    date. The SEC's exact start/end duration makes them distinguishable.
    """
    if metric in INSTANT:
        return {}
    taxonomy = ((payload.get("facts") or {}).get("us-gaap") or {})
    unit = UNITS.get(metric, "USD")
    selected: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
    for priority, concept in enumerate(CONCEPTS[metric]):
        facts = ((taxonomy.get(concept) or {}).get("units") or {}).get(unit) or []
        for fact in facts:
            if not isinstance(fact, Mapping):
                continue
            if str(fact.get("form") or "") not in {"10-Q", "10-Q/A"}:
                continue
            filed, end = str(fact.get("filed") or ""), str(fact.get("end") or "")
            if not filed or not end or filed >= as_of.date().isoformat():
                continue
            if _finite(fact.get("val")) is None:
                continue
            try:
                end_day = date.fromisoformat(end)
                start_day = date.fromisoformat(str(fact["start"]))
                if end_day > as_of.date() or not 65 <= (end_day - start_day).days <= 115:
                    continue
            except (TypeError, KeyError, ValueError):
                continue
            rank = (filed, -priority)
            previous = selected.get(end)
            if previous is None or rank > previous[0]:
                selected[end] = (rank, _record(fact, concept=concept, unit=unit))
    return {period: item[1] for period, item in selected.items()}


def evaluate_recent_quarter(
    payload: Mapping[str, Any], as_of: datetime,
) -> tuple[dict[str, float | None], dict[str, dict[str, Any]], str | None, str | None]:
    series = {
        metric: direct_quarter_series(payload, metric, as_of)
        for metric in ("revenue", "eps_diluted", "net_income", "operating_income")
    }
    periods = sorted(series["revenue"], reverse=True)
    latest = periods[0] if periods else None
    prior = (next(
        (period for period in periods[1:]
         if 330 <= (date.fromisoformat(latest) - date.fromisoformat(period)).days <= 400),
        None,
    ) if latest else None)
    provenance: dict[str, dict[str, Any]] = {}
    def value(metric: str, end: str | None, suffix: str) -> float | None:
        fact = series[metric].get(end or "")
        if fact is None:
            return None
        provenance[f"{metric}_{suffix}"] = dict(fact)
        return float(fact["value"])
    revenue = value("revenue", latest, "quarter")
    prior_revenue = value("revenue", prior, "quarter_yoy")
    eps = value("eps_diluted", latest, "quarter")
    prev_eps = value("eps_diluted", prior, "quarter_yoy")
    net = value("net_income", latest, "quarter")
    operating = value("operating_income", latest, "quarter")
    return ({
        "revenue_quarter_usd": revenue,
        "eps_diluted_quarter_usd": eps,
        "revenue_growth_quarter_yoy": _growth(revenue, prior_revenue),
        "eps_growth_quarter_yoy": _growth(eps, prev_eps),
        "operating_margin_quarter": _ratio(operating, revenue),
        "net_margin_quarter": _ratio(net, revenue),
    }, provenance, latest, prior)


def _score(metrics: Mapping[str, float | None]) -> tuple[float | None, int]:
    weighted = 0.0
    observed_weight = 0
    for key, weight, minimum, maximum in SCORE_FACTORS:
        value = metrics.get(key)
        if value is None:
            continue
        normalized = max(0.0, min(100.0, 100.0 * (float(value) - minimum) / (maximum - minimum)))
        weighted += weight * normalized
        observed_weight += weight
    if observed_weight < 65:
        return None, observed_weight
    return round(weighted / observed_weight, 4), observed_weight


def analyze_companyfacts(
    payload: Mapping[str, Any], *, as_of: datetime, sector: str = "",
    reference_price: float | None = None, price_session: str | None = None,
) -> dict[str, Any]:
    """Annual and direct quarterly GAAP metrics with point-in-time safeguards."""
    quarter_metrics, quarter_sources, quarter_end, quarter_prior = evaluate_recent_quarter(payload, as_of)
    series = {key: annual_series(payload, key, as_of) for key in CONCEPTS}
    revenue_periods = sorted(series["revenue"], reverse=True)
    metrics: dict[str, float | None] = {
        "revenue_growth_yoy": None, "eps_growth_yoy": None,
        "operating_margin": None, "net_margin": None,
        "fcf_margin": None, "return_on_equity": None,
        "liabilities_to_assets": None, "revenue_fy_usd": None,
        "net_income_fy_usd": None, "free_cashflow_fy_usd": None,
        "eps_diluted_fy_usd": None, "indicative_price_to_fy_earnings": None,
    }
    if not revenue_periods:
        return {"status": "INSUFFICIENT_DATA", "fundamental_score": None,
                "observed_score_weight": 0, "metrics": metrics, "source_facts": quarter_sources,
                "quarterly_metrics": quarter_metrics, "latest_quarter_end": quarter_end,
                "prior_year_quarter_end": quarter_prior,
                "fiscal_end": None, "anomalies": [], "llm_review_questions": [],
                "quality_flags": ["no_comparable_annual_10k_revenue"]}

    latest = revenue_periods[0]
    previous = next(
        (period for period in revenue_periods[1:]
         if 330 <= (date.fromisoformat(latest) - date.fromisoformat(period)).days <= 400),
        None,
    )
    sources: dict[str, dict[str, Any]] = dict(quarter_sources)
    def fact_value(metric: str, end: str | None) -> float | None:
        fact = series[metric].get(end or "")
        if fact is not None:
            sources[f"{metric}_{'fy' if end == latest else 'prior'}"] = dict(fact)
            return float(fact["value"])
        return None

    revenue = fact_value("revenue", latest)
    prior_revenue = fact_value("revenue", previous)
    net_income = fact_value("net_income", latest)
    operating_income = fact_value("operating_income", latest)
    eps = fact_value("eps_diluted", latest)
    prior_eps = fact_value("eps_diluted", previous)
    op_cashflow = fact_value("operating_cashflow", latest)
    capex = fact_value("capex", latest)
    assets = fact_value("assets", latest)
    liabilities = fact_value("liabilities", latest)
    equity = fact_value("equity", latest)
    free_cashflow = op_cashflow - capex if op_cashflow is not None and capex is not None and capex >= 0 else None

    metrics.update({
        "revenue_growth_yoy": _growth(revenue, prior_revenue),
        "eps_growth_yoy": _growth(eps, prior_eps),
        "operating_margin": _ratio(operating_income, revenue),
        "net_margin": _ratio(net_income, revenue),
        "fcf_margin": _ratio(free_cashflow, revenue),
        "return_on_equity": _ratio(net_income, equity) if equity is not None and equity > 0 else None,
        "liabilities_to_assets": _ratio(liabilities, assets),
        "revenue_fy_usd": revenue, "net_income_fy_usd": net_income,
        "free_cashflow_fy_usd": free_cashflow, "eps_diluted_fy_usd": eps,
    })
    flags: list[str] = []
    stale = (as_of.date() - date.fromisoformat(latest)).days > 475
    if stale:
        flags.append("annual_filing_stale")
    if previous is None:
        flags.append("no_comparable_prior_fiscal_year")
    if quarter_end is not None and (as_of.date() - date.fromisoformat(quarter_end)).days > 220:
        flags.append("quarterly_data_stale")
    # P/E is indicative, not a validated valuation signal: split adjustments,
    # ADR ratios, annual-vs-TTM earnings and quote provenance require checking.
    if reference_price is not None and reference_price > 0 and eps is not None and eps > 0:
        try:
            age = (as_of.date() - date.fromisoformat(str(price_session))).days
        except (ValueError, TypeError):
            age = 9999
        if 0 <= age <= 7 and not stale:
            metrics["indicative_price_to_fy_earnings"] = round(reference_price / eps, 4)
            flags.append("indicative_pe_not_split_or_adr_adjusted_not_ttm")

    financial = any(word in sector.casefold() for word in FINANCIAL_SECTORS)
    score, coverage = _score(metrics)
    if financial:
        score = None
        flags.append("sector_specific_financial_model_required")
    if stale:
        score = None
    status = ("STALE_FILING" if stale else "NOT_COMPARABLE" if financial
              else "COMPLETE" if score is not None else "PARTIAL")
    anomalies: list[str] = []
    questions: list[str] = []
    eps_growth = metrics["eps_growth_yoy"]
    rev_growth = metrics["revenue_growth_yoy"]
    if eps_growth is not None and rev_growth is not None and eps_growth > 0.30 and rev_growth < 0.08:
        anomalies.append("eps_growth_outpaces_revenue")
        questions.append("What explains EPS growth substantially exceeding revenue growth: margin, buybacks or one-offs? Cite the 10-K.")
    if net_income is not None and net_income > 0 and free_cashflow is not None and free_cashflow < 0:
        anomalies.append("positive_profit_negative_fcf")
        questions.append("Why is free cash flow negative despite positive net income? Check working capital and capex in the 10-K.")
    if rev_growth is not None and rev_growth < -0.10:
        anomalies.append("revenue_contraction")
        questions.append("What caused the annual revenue contraction and was it organic? Cite issuer evidence.")
    return {
        "status": status, "fundamental_score": score,
        "observed_score_weight": coverage, "metrics": metrics,
        "quarterly_metrics": quarter_metrics, "latest_quarter_end": quarter_end,
        "prior_year_quarter_end": quarter_prior,
        "source_facts": sources, "fiscal_end": latest, "prior_fiscal_end": previous,
        "anomalies": anomalies, "llm_review_questions": questions,
        "quality_flags": flags,
    }


class SECClient:
    """Conservative serial SEC requests: <=2/s per workflow, bounded responses."""
    def __init__(self, *, user_agent: str, spacing_seconds: float = 0.55,
                 timeout_seconds: int = 20, max_bytes: int = 24_000_000):
        if "@" not in user_agent:
            raise ValueError("SEC User-Agent must identify a real contact address")
        self.user_agent = user_agent
        self.spacing_seconds = max(0.5, spacing_seconds)
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.next_allowed = 0.0

    def get(self, url: str) -> dict[str, Any]:
        if not (url == "https://www.sec.gov/files/company_tickers.json"
                or url.startswith("https://data.sec.gov/api/xbrl/companyfacts/CIK")):
            raise ValueError("unapproved SEC endpoint")
        for attempt in range(2):
            delay = self.next_allowed - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_allowed = time.monotonic() + self.spacing_seconds
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise ValueError("SEC response exceeds bounded size")
                result = json.loads(raw.decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("SEC returned unexpected JSON type")
                return result
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                if attempt:
                    raise
                time.sleep(1.0)
        raise AssertionError("unreachable")


def map_tickers(raw: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in raw.values():
        if not isinstance(item, Mapping):
            continue
        ticker = str(item.get("ticker") or "").upper().replace(".", "-").strip()
        try:
            cik = int(item["cik_str"])
        except (TypeError, KeyError, ValueError):
            continue
        if ticker and cik > 0:
            result[ticker] = cik
    return result


def build_snapshot(
    frontier: Mapping[str, Any], config: Mapping[str, Any], *,
    client: Any, as_of: datetime | None = None,
) -> dict[str, Any]:
    discovery.validate_frontier(frontier)
    market = str(frontier["market"]).upper()
    if market not in {"US", "GPW"}:
        raise ValueError("unexpected market")
    as_of = as_of or _utc(frontier["generated_at"])
    limit = min(max(1, int(config.get("candidates_per_cycle", 10))), 50)
    candidates = list(frontier.get("candidates") or [])[:limit]
    tickers: dict[str, int] = {}
    map_error: str | None = None
    if market == "US":
        try:
            tickers = map_tickers(client.get("https://www.sec.gov/files/company_tickers.json"))
        except Exception as exc:
            map_error = f"{type(exc).__name__}: {str(exc)[:200]}"
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "").upper()
        row: dict[str, Any] = {"symbol": symbol, "market": market}
        if market != "US":
            row.update({"status": "UNSUPPORTED_SOURCE", "fundamental_score": None,
                        "quality_flags": ["official_gpw_structured_fundamentals_not_integrated"],
                        "metrics": {}, "source_facts": {}, "anomalies": [],
                        "llm_review_questions": []})
        else:
            cik = tickers.get(symbol.replace(".", "-"))
            row["cik"] = cik
            if map_error or cik is None:
                row.update({"status": "DATA_ERROR" if map_error else "NO_CIK",
                            "fundamental_score": None, "metrics": {},
                            "source_facts": {}, "anomalies": [], "llm_review_questions": [],
                            "quality_flags": [map_error or "sec_ticker_not_mapped"]})
            else:
                try:
                    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
                    facts = client.get(url)
                    if int(facts.get("cik") or -1) != cik:
                        raise ValueError("SEC CIK mismatch")
                    row.update(analyze_companyfacts(
                        facts, as_of=as_of, sector=str(candidate.get("sector") or ""),
                        reference_price=_finite((candidate.get("features") or {}).get("last_close")),
                        price_session=(candidate.get("features") or {}).get("latest_session")))
                    row["source_url"] = url
                except Exception as exc:
                    row.update({"status": "DATA_ERROR", "fundamental_score": None,
                                "metrics": {}, "source_facts": {}, "anomalies": [],
                                "llm_review_questions": [],
                                "quality_flags": [f"{type(exc).__name__}: {str(exc)[:200]}"]})
        row["production_decision_influence"] = False
        rows.append(row)
    payload = {
        "schema_version": SCHEMA, "market": market,
        "generated_at": as_of.isoformat().replace("+00:00", "Z"),
        "source_frontier_sha256": frontier["frontier_sha256"],
        "source_frontier_generated_at": frontier["generated_at"],
        "candidate_count": len(rows), "candidates": rows,
        "provider": "SEC_EDGAR_COMPANYFACTS" if market == "US" else "NOT_CONFIGURED",
        "governance": {"production_decision_influence": False,
                       "automatic_portfolio_admission": False,
                       "llm_calls": 0, "annual_10k_only": True,
                       "exclude_same_day_filing": True, "score_mode": "shadow_only"},
    }
    payload["fundamentals_sha256"] = contracts.payload_sha256(payload)
    validate_snapshot(payload)
    return payload


def validate_snapshot(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA or payload.get("market") not in {"US", "GPW"}:
        raise ValueError("invalid fundamentals schema or market")
    gov = payload.get("governance") or {}
    if (gov.get("production_decision_influence") is not False
            or gov.get("automatic_portfolio_admission") is not False
            or gov.get("llm_calls") != 0 or gov.get("score_mode") != "shadow_only"):
        raise ValueError("fundamentals must remain non-executing shadow research")
    rows = payload.get("candidates")
    if not isinstance(rows, list) or len(rows) != payload.get("candidate_count"):
        raise ValueError("fundamentals candidate count mismatch")
    if len({str(row.get("symbol") or "") for row in rows}) != len(rows):
        raise ValueError("duplicate fundamentals symbols")
    for row in rows:
        if row.get("production_decision_influence") is not False:
            raise ValueError("fundamental candidate acquired production authority")
        score = row.get("fundamental_score")
        if score is not None and not (0 <= float(score) <= 100):
            raise ValueError("fundamental score outside [0, 100]")
    body = dict(payload)
    claimed = body.pop("fundamentals_sha256", None)
    if not claimed or contracts.payload_sha256(body) != claimed:
        raise ValueError("fundamentals hash mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "GPW"], required=True)
    parser.add_argument("--frontier", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    output = args.output or OUTPUT_ROOT / f"{args.market.lower()}.json"
    if args.verify:
        validate_snapshot(json.loads(output.read_text(encoding="utf-8")))
        print("FUNDAMENTALS_VERIFY_OK", output)
        return 0
    frontier = json.loads((args.frontier or FRONTIER_ROOT / f"{args.market.lower()}.json").read_text(encoding="utf-8"))
    config = json.loads(args.config.read_text(encoding="utf-8"))
    client = SECClient(user_agent=str(config["sec_user_agent"]),
                       spacing_seconds=float(config.get("spacing_seconds", 0.55)))
    snapshot = build_snapshot(frontier, config, client=client)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"market": args.market, "candidates": snapshot["candidate_count"],
                      "statuses": {key: sum(row["status"] == key for row in snapshot["candidates"])
                                   for key in sorted(set(row["status"] for row in snapshot["candidates"]))},
                      "llm_calls": 0, "production_decision_influence": False},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
