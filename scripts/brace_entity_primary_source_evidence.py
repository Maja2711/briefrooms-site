#!/usr/bin/env python3
"""PR #13 — Entity Primary-Source Evidence Foundation.

This layer turns authoritative issuer filings into deterministic, timestamped
primary-source observations for the Company/Entity Belief Framework.

What it does:
- follows the PR12 active entity universe (current Portfolio 10K + BRACE candidates),
- resolves SEC CIKs and issuer reporting regime,
- observes prospective 10-K/10-Q/8-K and 20-F/6-K/40-F filings,
- captures selected SEC XBRL Company Facts tied to the exact filing accession,
- preserves append-only provenance and collection windows.

What it deliberately does NOT do:
- no historical evidence backfill,
- no LLM interpretation,
- no positive/negative Belief polarity,
- no Entity forecast capture,
- no BRACE score/ranking/exposure/sizing/veto/trade influence,
- no promotion.

A later reviewed layer may interpret these primary observations into Belief
Evidence and forecasts. Any later Engine↔Belief bridge still requires prospective
paired WITH vs WITHOUT BELIEF economics before promotion review.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time as time_module
import urllib.request
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple
from zoneinfo import ZoneInfo

from belief_adapter_contract import Observation
from belief_core import iso_z, parse_time
from brace_company_entity_framework import (
    DEFAULT_ANALYSIS,
    DEFAULT_PORTFOLIO,
    DEFAULT_UNIVERSE,
    desired_entities,
)

MODE = "research_shadow"
SCHEMA_VERSION = "brace-entity-primary-source-evidence-v1"
REPORT_VERSION = "brace-entity-primary-source-evidence-report-v1"
STATE_FILENAME = "ENTITY_PRIMARY_SOURCE_EVIDENCE_STATE.json"
REPORT_FILENAME = "BRACE_ENTITY_PRIMARY_SOURCE_EVIDENCE_REPORT.json"

ROOT = Path(__file__).resolve().parents[1]
SEC_TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{filename}"
SEC_INDEX_HEADERS = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{accession}-index-headers.html"

SEC_PRIMARY_FORM_BASES = frozenset({"10-K", "10-Q", "8-K", "20-F", "6-K", "40-F"})
DOMESTIC_FORM_BASES = frozenset({"10-K", "10-Q", "8-K"})
FOREIGN_FORM_BASES = frozenset({"20-F", "6-K", "40-F"})

NY = ZoneInfo("America/New_York")
FIXED_EST = timezone(timedelta(hours=-5))
MAX_SOURCE_ERROR_CHARS = 320

# Canonical metric -> exact XBRL local-name aliases. The layer records facts only;
# it does not assign bullish/bearish polarity. This intentionally supports both
# common US-GAAP and common IFRS local names where SEC Company Facts exposes them.
METRIC_TAG_ALIASES: Mapping[str, Tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "Revenue",
    ),
    "net_income": (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncome",
    ),
    "diluted_eps": (
        "EarningsPerShareDiluted",
        "DilutedEarningsLossPerShare",
    ),
    "operating_income": (
        "OperatingIncomeLoss",
        "ProfitLossFromOperatingActivities",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PaymentsToAcquireProductiveAssets",
    ),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalents",
    ),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "share_repurchases": (
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
    ),
    "dividends_paid": (
        "PaymentsOfDividends",
        "DividendsPaid",
    ),
    "deposits": ("Deposits",),
    "provision_for_credit_losses": (
        "ProvisionForCreditLosses",
        "ProvisionForLoanLeaseAndOtherLosses",
    ),
    "net_interest_income": (
        "InterestIncomeExpenseNet",
        "InterestIncomeExpenseNonoperatingNet",
    ),
}

METRIC_DIMENSIONS: Mapping[str, Tuple[str, ...]] = {
    "revenue": ("revenue_durability",),
    "net_income": ("earnings_momentum",),
    "diluted_eps": ("earnings_momentum",),
    "operating_income": ("margin_trajectory",),
    "operating_cash_flow": ("earnings_quality",),
    "capex": ("capex_returns",),
    "cash": ("balance_sheet_strength",),
    "assets": ("balance_sheet_strength",),
    "liabilities": ("balance_sheet_strength",),
    "share_repurchases": ("capital_allocation",),
    "dividends_paid": ("capital_allocation",),
    "deposits": ("deposit_funding",),
    "provision_for_credit_losses": ("credit_quality",),
    "net_interest_income": ("net_interest_income_durability",),
}

ALIAS_TO_METRIC: Dict[str, str] = {
    alias.casefold(): metric
    for metric, aliases in METRIC_TAG_ALIASES.items()
    for alias in aliases
}


@dataclass(frozen=True)
class FilingRecord:
    entity_id: str
    ticker: str
    cik: int
    form: str
    accession_number: str
    filing_date: str
    report_date: str
    acceptance_raw: str
    accepted_at: str
    primary_document: str
    description: str
    primary_document_url: str
    index_headers_url: str

    @property
    def form_base(self) -> str:
        return normalize_form(self.form)


@dataclass(frozen=True)
class SourceIssue:
    entity_id: str
    code: str
    message: str
    source_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SecClientProtocol(Protocol):
    def ticker_index(self) -> Mapping[str, Mapping[str, Any]]: ...
    def submissions(self, cik: int) -> Mapping[str, Any]: ...
    def companyfacts(self, cik: int) -> Mapping[str, Any]: ...
    def acceptance_from_index_headers(self, cik: int, accession_number: str) -> Optional[str]: ...


class SecEdgarClient:
    """Small SEC REST/Archives client with conservative request pacing."""

    def __init__(self, timeout: int = 20, min_interval_seconds: float = 0.12) -> None:
        self.timeout = int(timeout)
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.user_agent = os.getenv(
            "SEC_USER_AGENT",
            "BriefRooms-EntityEvidence/1.0 research https://briefrooms.com",
        ).strip()
        self._last_request_monotonic: Optional[float] = None

    def _pace(self) -> None:
        if self._last_request_monotonic is not None:
            elapsed = time_module.monotonic() - self._last_request_monotonic
            remaining = self.min_interval_seconds - elapsed
            if remaining > 0:
                time_module.sleep(remaining)
        self._last_request_monotonic = time_module.monotonic()

    def _request(self, url: str, accept: str) -> bytes:
        self._pace()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": accept,
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def _json(self, url: str) -> Mapping[str, Any]:
        return json.loads(self._request(url, "application/json").decode("utf-8", "replace"))

    def _text(self, url: str) -> str:
        return self._request(url, "text/html,text/plain;q=0.9").decode("utf-8", "replace")

    def ticker_index(self) -> Mapping[str, Mapping[str, Any]]:
        raw = self._json(SEC_TICKER_MAP)
        out: Dict[str, Mapping[str, Any]] = {}
        for row in raw.values():
            if not isinstance(row, Mapping):
                continue
            ticker = normalize_ticker(str(row.get("ticker") or ""))
            try:
                cik = int(row.get("cik_str"))
            except (TypeError, ValueError):
                continue
            if ticker:
                out[ticker] = {
                    "ticker": ticker,
                    "cik": cik,
                    "title": str(row.get("title") or ""),
                }
        return out

    def submissions(self, cik: int) -> Mapping[str, Any]:
        return self._json(SEC_SUBMISSIONS.format(cik=int(cik)))

    def companyfacts(self, cik: int) -> Mapping[str, Any]:
        return self._json(SEC_COMPANYFACTS.format(cik=int(cik)))

    def acceptance_from_index_headers(self, cik: int, accession_number: str) -> Optional[str]:
        accession_clean = str(accession_number).replace("-", "")
        url = SEC_INDEX_HEADERS.format(
            cik=int(cik),
            accession_clean=accession_clean,
            accession=str(accession_number),
        )
        text = self._text(url)
        match = re.search(r"<ACCEPTANCE-DATETIME>\s*(\d{14})", text, flags=re.IGNORECASE)
        return match.group(1) if match else None


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "score_change": False,
        "candidate_ranking_change": False,
        "target_exposure_change": False,
        "sizing_change": False,
        "veto": False,
        "direction_reversal": False,
        "forced_exit": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "bounded_influence": False,
        "historical_backfill": False,
        "llm_interpretation": False,
        "belief_polarity_assignment": False,
        "entity_forecast_capture": False,
        "entity_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "primary_source_collection_enabled": True,
        "sec_edgar_submissions_enabled": True,
        "sec_xbrl_companyfacts_enabled": True,
        "reporting_regime_resolution_enabled": True,
        "prospective_primary_observation_ledger_enabled": True,
        "issuer_ir_adapter_extension_point_enabled": True,
        "issuer_ir_live_collection_enabled": False,
        "belief_evidence_interpretation_enabled": False,
        "entity_forecast_capture_enabled": False,
        "with_without_bridge_enabled": False,
        "promotion_gate_enabled": False,
    }


def promotion_evidence_standard() -> Dict[str, Any]:
    return {
        "with_without_required": True,
        "paired_prospective_counterfactual_required": True,
        "effective_n_required": True,
        "stable_uplift_required": True,
        "multi_regime_robustness_required": True,
        "concentration_check_required": True,
        "drawdown_not_materially_worse_required": True,
        "tail_risk_not_materially_worse_required": True,
        "belief_calibration_required": True,
        "drift_check_required": True,
        "data_quality_and_provenance_required": True,
        "anti_hindsight_required": True,
        "automatic_promotion": False,
        "review_output_only": "ELIGIBLE_FOR_PROMOTION_REVIEW",
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR13 zero-influence invariant violated: " + ",".join(bad))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def normalize_ticker(value: str) -> str:
    ticker = str(value or "").strip().upper()
    if ticker.endswith(".US"):
        ticker = ticker[:-3]
    return ticker.replace("/", "-")


def normalize_form(value: str) -> str:
    form = str(value or "").strip().upper()
    return form[:-2] if form.endswith("/A") else form


def is_primary_form(value: str) -> bool:
    return normalize_form(value) in SEC_PRIMARY_FORM_BASES


def parse_sec_acceptance(value: str) -> Optional[datetime]:
    """Parse SEC acceptance time conservatively for anti-lookahead.

    If the source includes an explicit timezone, trust it. For timezone-less EDGAR
    timestamps, compute both America/New_York and fixed EST interpretations and use
    the later UTC instant. This can delay availability by an hour during DST but it
    cannot make the filing available earlier than either reasonable SEC-Eastern
    interpretation.
    """
    raw = str(value or "").strip()
    if not raw:
        return None

    if re.fullmatch(r"\d{14}", raw):
        naive = datetime.strptime(raw, "%Y%m%d%H%M%S")
    else:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            digits = re.sub(r"\D", "", raw)
            if len(digits) < 14:
                return None
            try:
                naive = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
            except ValueError:
                return None
        else:
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc)
            naive = parsed

    ny_utc = naive.replace(tzinfo=NY).astimezone(timezone.utc)
    est_utc = naive.replace(tzinfo=FIXED_EST).astimezone(timezone.utc)
    return max(ny_utc, est_utc)


def reporting_regime(forms: Iterable[str]) -> str:
    bases = {normalize_form(x) for x in forms if is_primary_form(x)}
    domestic = bool(bases & {"10-K", "10-Q"})
    foreign = bool(bases & {"20-F", "6-K", "40-F"})
    if domestic and foreign:
        return "mixed_or_transition_requires_review"
    if foreign:
        return "foreign_private_issuer_sec"
    if domestic:
        return "domestic_sec_periodic_reporting"
    if "8-K" in bases:
        return "sec_registered_event_reporting_only_unresolved"
    return "unresolved_no_supported_periodic_form_observed"


def _column(recent: Mapping[str, Any], key: str, index: int) -> Any:
    values = recent.get(key) or []
    return values[index] if index < len(values) else ""


def _parse_filing_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def filing_records(
    *,
    entity_id: str,
    ticker: str,
    cik: int,
    submissions: Mapping[str, Any],
    client: SecClientProtocol,
    boundary_at: datetime,
) -> Tuple[Tuple[FilingRecord, ...], Tuple[SourceIssue, ...]]:
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    out: List[FilingRecord] = []
    issues: List[SourceIssue] = []
    boundary_day = boundary_at.astimezone(timezone.utc).date()

    for index, form_raw in enumerate(forms):
        form = str(form_raw or "")
        if not is_primary_form(form):
            continue
        accession = str(_column(recent, "accessionNumber", index) or "").strip()
        filing_date = str(_column(recent, "filingDate", index) or "").strip()
        report_date = str(_column(recent, "reportDate", index) or "").strip()
        primary_document = str(_column(recent, "primaryDocument", index) or "").strip()
        description = str(_column(recent, "primaryDocDescription", index) or "").strip()
        acceptance_raw = str(_column(recent, "acceptanceDateTime", index) or "").strip()
        if not accession:
            continue

        accepted = parse_sec_acceptance(acceptance_raw)
        filing_day = _parse_filing_date(filing_date)
        # Avoid downloading headers for clearly old filings. Same-day or future-day
        # candidates relative to the current collection window are resolved exactly.
        if accepted is None and filing_day is not None and filing_day >= boundary_day - timedelta(days=1):
            try:
                acceptance_raw = str(client.acceptance_from_index_headers(cik, accession) or "")
                accepted = parse_sec_acceptance(acceptance_raw)
            except Exception as exc:
                issues.append(SourceIssue(
                    entity_id,
                    "acceptance_timestamp_fetch_failed",
                    f"{type(exc).__name__}: {str(exc)[:MAX_SOURCE_ERROR_CHARS]}",
                    accession,
                ))

        if accepted is None:
            # Old unresolved filings are not relevant for a prospective window. New
            # unresolved filings fail closed and are surfaced as data-quality issues.
            if filing_day is not None and filing_day >= boundary_day - timedelta(days=1):
                issues.append(SourceIssue(
                    entity_id,
                    "acceptance_timestamp_unresolved",
                    "Filing is not evidence-eligible until a precise SEC acceptance timestamp is resolved.",
                    accession,
                ))
            continue

        accession_clean = accession.replace("-", "")
        primary_url = SEC_ARCHIVES.format(
            cik=int(cik),
            accession_clean=accession_clean,
            filename=primary_document or f"{accession}-index.html",
        )
        headers_url = SEC_INDEX_HEADERS.format(
            cik=int(cik),
            accession_clean=accession_clean,
            accession=accession,
        )
        out.append(FilingRecord(
            entity_id=entity_id,
            ticker=ticker,
            cik=int(cik),
            form=form,
            accession_number=accession,
            filing_date=filing_date,
            report_date=report_date,
            acceptance_raw=acceptance_raw,
            accepted_at=iso_z(accepted),
            primary_document=primary_document,
            description=description,
            primary_document_url=primary_url,
            index_headers_url=headers_url,
        ))

    out.sort(key=lambda row: (row.accepted_at, row.accession_number))
    return tuple(out), tuple(issues)


def filing_observation(filing: FilingRecord) -> Observation:
    return Observation.make(
        adapter="entity_primary_source_sec",
        metric="entity_primary_filing",
        entity=filing.entity_id,
        observed_at=filing.accepted_at,
        value={
            "form": filing.form,
            "form_base": filing.form_base,
            "accession_number": filing.accession_number,
            "filing_date": filing.filing_date,
            "report_date": filing.report_date,
            "primary_document": filing.primary_document,
            "description": filing.description,
        },
        unit="filing",
        source="SEC EDGAR",
        source_type="primary",
        source_ref=filing.primary_document_url,
        reliability=.995,
        independence_cluster=f"issuer-filing:{filing.entity_id}:{filing.accession_number}",
        tags=("entity", "primary_source", "sec_filing", filing.form_base.lower()),
        metadata={
            "ticker": filing.ticker,
            "cik": filing.cik,
            "acceptance_raw": filing.acceptance_raw,
            "acceptance_timestamp_policy": "conservative_not_before",
            "index_headers_url": filing.index_headers_url,
            "belief_polarity": "uninterpreted",
            "forecast_eligible": False,
        },
    )


def _canonical_metric(tag: str) -> Optional[str]:
    return ALIAS_TO_METRIC.get(str(tag or "").casefold())


def _fact_source_ref(filing: FilingRecord, taxonomy: str, tag: str, unit: str, row: Mapping[str, Any]) -> str:
    parts = [
        SEC_COMPANYFACTS.format(cik=filing.cik),
        f"accn={filing.accession_number}",
        f"taxonomy={taxonomy}",
        f"tag={tag}",
        f"unit={unit}",
        f"start={row.get('start') or ''}",
        f"end={row.get('end') or ''}",
        f"fy={row.get('fy') or ''}",
        f"fp={row.get('fp') or ''}",
        f"frame={row.get('frame') or ''}",
    ]
    return "#".join(parts)


def companyfact_observations(
    filing: FilingRecord,
    payload: Mapping[str, Any],
) -> Tuple[Observation, ...]:
    out: List[Observation] = []
    seen_keys = set()
    facts = payload.get("facts") or {}
    for taxonomy, taxonomy_rows in facts.items():
        if not isinstance(taxonomy_rows, Mapping):
            continue
        for tag, tag_payload in taxonomy_rows.items():
            metric = _canonical_metric(str(tag))
            if metric is None or not isinstance(tag_payload, Mapping):
                continue
            units = tag_payload.get("units") or {}
            if not isinstance(units, Mapping):
                continue
            for unit, rows in units.items():
                if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                    continue
                for raw in rows:
                    if not isinstance(raw, Mapping):
                        continue
                    if str(raw.get("accn") or "") != filing.accession_number:
                        continue
                    row_form = str(raw.get("form") or filing.form)
                    if normalize_form(row_form) != filing.form_base:
                        continue
                    value = raw.get("val")
                    if value is None:
                        continue
                    identity = (
                        metric,
                        str(taxonomy),
                        str(tag),
                        str(unit),
                        str(raw.get("start") or ""),
                        str(raw.get("end") or ""),
                        str(raw.get("fy") or ""),
                        str(raw.get("fp") or ""),
                        str(raw.get("frame") or ""),
                        json.dumps(value, sort_keys=True, default=str),
                    )
                    if identity in seen_keys:
                        continue
                    seen_keys.add(identity)
                    source_ref = _fact_source_ref(filing, str(taxonomy), str(tag), str(unit), raw)
                    out.append(Observation.make(
                        adapter="entity_primary_source_sec",
                        metric=f"entity_primary_fact.{metric}",
                        entity=filing.entity_id,
                        observed_at=filing.accepted_at,
                        value=value,
                        unit=str(unit),
                        source="SEC EDGAR XBRL Company Facts",
                        source_type="primary",
                        source_ref=source_ref,
                        reliability=.995,
                        independence_cluster=f"issuer-filing:{filing.entity_id}:{filing.accession_number}",
                        tags=("entity", "primary_source", "xbrl_fact", metric),
                        metadata={
                            "ticker": filing.ticker,
                            "cik": filing.cik,
                            "form": filing.form,
                            "form_base": filing.form_base,
                            "accession_number": filing.accession_number,
                            "filing_date": filing.filing_date,
                            "report_date": filing.report_date,
                            "taxonomy": str(taxonomy),
                            "tag": str(tag),
                            "label": str(tag_payload.get("label") or ""),
                            "description": str(tag_payload.get("description") or ""),
                            "period_start": raw.get("start"),
                            "period_end": raw.get("end"),
                            "fiscal_year": raw.get("fy"),
                            "fiscal_period": raw.get("fp"),
                            "frame": raw.get("frame"),
                            "dimension_candidates": list(METRIC_DIMENSIONS.get(metric, ())),
                            "belief_polarity": "uninterpreted_primary_fact",
                            "historical_comparison_performed": False,
                            "forecast_eligible": False,
                        },
                    ))
    out.sort(key=lambda row: (row.metric, row.source_ref))
    return tuple(out)


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "first_run_at": None,
        "last_updated_at": None,
        "entities": {},
        "observations": [],
    }


def _open_collection_window(entity_state: MutableMapping[str, Any], when: str, reason: str) -> None:
    windows = entity_state.setdefault("collection_windows", [])
    if windows and windows[-1].get("closed_at") is None:
        return
    windows.append({
        "opened_at": when,
        "closed_at": None,
        "reason": reason,
    })
    entity_state["current_window_opened_at"] = when


def _close_collection_window(entity_state: MutableMapping[str, Any], when: str) -> None:
    windows = entity_state.setdefault("collection_windows", [])
    if windows and windows[-1].get("closed_at") is None:
        windows[-1]["closed_at"] = when
    entity_state["current_window_opened_at"] = None


def _append_observations(state: MutableMapping[str, Any], rows: Sequence[Observation]) -> int:
    ledger = state.setdefault("observations", [])
    by_id = {str(row.get("observation_id")): row for row in ledger if isinstance(row, Mapping)}
    added = 0
    for observation in rows:
        payload = asdict(observation)
        payload["tags"] = list(observation.tags)
        payload["metadata"] = dict(observation.metadata)
        existing = by_id.get(observation.observation_id)
        if existing is not None:
            if existing != payload:
                raise ValueError(f"Observation identity collision with changed payload: {observation.observation_id}")
            continue
        ledger.append(payload)
        by_id[observation.observation_id] = payload
        added += 1
    ledger.sort(key=lambda row: (str(row.get("observed_at")), str(row.get("observation_id"))))
    return added


def _load_desired_entities(
    portfolio_path: Path,
    analysis_path: Path,
    universe_path: Path,
) -> Dict[str, Dict[str, Any]]:
    portfolio = _read_json(portfolio_path, {"positions": []})
    analysis = _read_json(analysis_path, {"candidates": []})
    universe = _read_json(universe_path, {"instruments": []})
    return desired_entities(portfolio, analysis, universe)


def _source_issue_dict(issue: SourceIssue, observed_at: str) -> Dict[str, Any]:
    payload = issue.to_dict()
    payload["observed_at"] = observed_at
    return payload


def run(
    state_dir: Path,
    *,
    portfolio_path: Path = DEFAULT_PORTFOLIO,
    analysis_path: Path = DEFAULT_ANALYSIS,
    universe_path: Path = DEFAULT_UNIVERSE,
    as_of: Optional[datetime] = None,
    sec_client: Optional[SecClientProtocol] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    when = iso_z(now)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME
    state = _read_json(state_path, empty_state())
    state["schema_version"] = SCHEMA_VERSION
    state["mode"] = MODE
    state.setdefault("entities", {})
    state.setdefault("observations", [])
    if not state.get("first_run_at"):
        state["first_run_at"] = when

    desired = _load_desired_entities(portfolio_path, analysis_path, universe_path)
    client = sec_client or SecEdgarClient()
    run_issues: List[Dict[str, Any]] = []
    new_observations = 0
    new_filing_observations = 0
    new_fact_observations = 0

    try:
        ticker_index = client.ticker_index()
        ticker_index_error = None
    except Exception as exc:
        ticker_index = {}
        ticker_index_error = f"{type(exc).__name__}: {str(exc)[:MAX_SOURCE_ERROR_CHARS]}"
        run_issues.append({
            "entity_id": "GLOBAL",
            "code": "sec_ticker_index_unavailable",
            "message": ticker_index_error,
            "source_ref": SEC_TICKER_MAP,
            "observed_at": when,
        })

    entities: MutableMapping[str, Any] = state["entities"]

    # Deactivate collection windows for entities no longer in the PR12 active set.
    for entity_id, entity_state_raw in list(entities.items()):
        if entity_id in desired:
            continue
        entity_state = deepcopy(dict(entity_state_raw))
        if entity_state.get("current_status") == "active":
            _close_collection_window(entity_state, when)
            entity_state["last_deactivated_at"] = when
        entity_state["current_status"] = "dormant"
        entity_state["last_updated_at"] = when
        entities[entity_id] = entity_state

    for entity_id, entity in sorted(desired.items()):
        entity_state = deepcopy(dict(entities.get(entity_id) or {}))
        was_new = not bool(entity_state)
        was_dormant = entity_state.get("current_status") == "dormant"
        ticker = normalize_ticker(str(entity.get("market_symbol") or entity.get("data_symbol") or entity.get("broker_symbol") or ""))

        if was_new:
            entity_state = {
                "entity_id": entity_id,
                "first_seen_at": when,
                "current_status": "active",
                "collection_windows": [],
                "seen_accessions": [],
                "filing_observation_count": 0,
                "structured_fact_observation_count": 0,
            }
            _open_collection_window(entity_state, when, "first_pr13_activation")
        elif was_dormant:
            entity_state["current_status"] = "active"
            entity_state["last_reactivated_at"] = when
            _open_collection_window(entity_state, when, "reactivated_after_dormancy")
        else:
            entity_state["current_status"] = "active"
            if not entity_state.get("current_window_opened_at"):
                _open_collection_window(entity_state, when, "collection_window_repaired")

        boundary_at = parse_time(str(entity_state["current_window_opened_at"]))
        entity_state.update({
            "market_symbol": ticker,
            "sector": entity.get("sector"),
            "activation_source": entity.get("activation_source"),
            "activation_class": entity.get("activation_class"),
            "candidate_rank": entity.get("candidate_rank"),
            "last_seen_at": when,
            "last_updated_at": when,
        })

        source_match = ticker_index.get(ticker) if ticker else None
        if source_match:
            cik = int(source_match.get("cik"))
            entity_state["sec_source"] = {
                "status": "resolved",
                "ticker": ticker,
                "cik": cik,
                "issuer_name": source_match.get("title"),
                "ticker_map_source": SEC_TICKER_MAP,
            }
        else:
            prior_sec = dict(entity_state.get("sec_source") or {})
            cik = int(prior_sec.get("cik")) if prior_sec.get("cik") else 0
            if not cik:
                entity_state["sec_source"] = {
                    "status": "unresolved_no_ticker_match",
                    "ticker": ticker,
                    "cik": None,
                    "ticker_map_source": SEC_TICKER_MAP,
                    "ticker_index_error": ticker_index_error,
                }
                run_issues.append({
                    "entity_id": entity_id,
                    "code": "sec_ticker_unresolved",
                    "message": f"No SEC ticker-map match for {ticker or entity_id}; no synthetic source fallback was used.",
                    "source_ref": SEC_TICKER_MAP,
                    "observed_at": when,
                })
                entities[entity_id] = entity_state
                continue

        try:
            submissions = client.submissions(cik)
        except Exception as exc:
            entity_state["sec_source"]["status"] = "submissions_unavailable"
            run_issues.append({
                "entity_id": entity_id,
                "code": "sec_submissions_unavailable",
                "message": f"{type(exc).__name__}: {str(exc)[:MAX_SOURCE_ERROR_CHARS]}",
                "source_ref": SEC_SUBMISSIONS.format(cik=cik),
                "observed_at": when,
            })
            entities[entity_id] = entity_state
            continue

        recent = ((submissions.get("filings") or {}).get("recent") or {})
        entity_state["reporting_regime"] = reporting_regime(recent.get("form") or [])
        entity_state["sec_source"]["status"] = "resolved"
        entity_state["sec_source"]["reporting_regime"] = entity_state["reporting_regime"]
        entity_state["sec_source"]["submissions_source"] = SEC_SUBMISSIONS.format(cik=cik)
        entity_state["sec_source"]["companyfacts_source"] = SEC_COMPANYFACTS.format(cik=cik)

        filings, issues = filing_records(
            entity_id=entity_id,
            ticker=ticker,
            cik=cik,
            submissions=submissions,
            client=client,
            boundary_at=boundary_at,
        )
        run_issues.extend(_source_issue_dict(issue, when) for issue in issues)
        seen_accessions = set(str(x) for x in (entity_state.get("seen_accessions") or []))

        prospective_filings: List[FilingRecord] = []
        for filing in filings:
            accepted = parse_time(filing.accepted_at)
            if accepted <= boundary_at:
                # Seed/maintain the cursor without turning historical filings into evidence.
                seen_accessions.add(filing.accession_number)
                continue
            if accepted > now + timedelta(minutes=5):
                run_issues.append({
                    "entity_id": entity_id,
                    "code": "future_dated_sec_acceptance",
                    "message": "SEC acceptance timestamp is materially after the PR13 run timestamp; filing was not ingested.",
                    "source_ref": filing.index_headers_url,
                    "observed_at": when,
                })
                continue
            if filing.accession_number in seen_accessions:
                continue
            prospective_filings.append(filing)

        companyfacts_payload: Optional[Mapping[str, Any]] = None
        if prospective_filings:
            try:
                companyfacts_payload = client.companyfacts(cik)
            except Exception as exc:
                run_issues.append({
                    "entity_id": entity_id,
                    "code": "sec_companyfacts_unavailable",
                    "message": f"{type(exc).__name__}: {str(exc)[:MAX_SOURCE_ERROR_CHARS]}",
                    "source_ref": SEC_COMPANYFACTS.format(cik=cik),
                    "observed_at": when,
                })

        for filing in prospective_filings:
            filing_rows = [filing_observation(filing)]
            if companyfacts_payload is not None:
                filing_rows.extend(companyfact_observations(filing, companyfacts_payload))
            added = _append_observations(state, filing_rows)
            new_observations += added
            if added:
                # Count from the attempted rows because all rows are new for a new accession.
                new_filing_observations += 1
                new_fact_observations += max(0, added - 1)
            seen_accessions.add(filing.accession_number)
            entity_state["last_prospective_filing_at"] = filing.accepted_at
            entity_state["last_prospective_accession"] = filing.accession_number

        entity_state["seen_accessions"] = sorted(seen_accessions)
        entity_state["filing_observation_count"] = sum(
            1
            for row in state.get("observations", [])
            if row.get("entity") == entity_id and row.get("metric") == "entity_primary_filing"
        )
        entity_state["structured_fact_observation_count"] = sum(
            1
            for row in state.get("observations", [])
            if row.get("entity") == entity_id and str(row.get("metric") or "").startswith("entity_primary_fact.")
        )
        entity_state["last_poll_at"] = when
        entity_state["data_quality_status"] = (
            "source_resolved"
            if entity_state.get("sec_source", {}).get("status") == "resolved"
            else "source_incomplete"
        )
        entities[entity_id] = entity_state

    state["last_updated_at"] = when
    state["last_run_issues"] = run_issues
    _write_json(state_path, state)

    active_rows = [dict(row) for row in entities.values() if row.get("current_status") == "active"]
    dormant_rows = [dict(row) for row in entities.values() if row.get("current_status") == "dormant"]
    source_resolved = sum(1 for row in active_rows if (row.get("sec_source") or {}).get("status") == "resolved")
    regimes: Dict[str, int] = {}
    for row in active_rows:
        key = str(row.get("reporting_regime") or "unresolved")
        regimes[key] = regimes.get(key, 0) + 1

    observation_rows = list(state.get("observations") or [])
    filing_count = sum(1 for row in observation_rows if row.get("metric") == "entity_primary_filing")
    fact_count = sum(1 for row in observation_rows if str(row.get("metric") or "").startswith("entity_primary_fact."))

    report = {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": when,
        "mode": MODE,
        "active_decision_influence": False,
        "purpose": "Prospective authoritative issuer-source observations for later Entity Belief interpretation and calibration.",
        "source_contract": {
            "primary_provider": "SEC EDGAR",
            "ticker_map": SEC_TICKER_MAP,
            "submissions_api": "data.sec.gov/submissions/CIK##########.json",
            "companyfacts_api": "data.sec.gov/api/xbrl/companyfacts/CIK##########.json",
            "supported_form_bases": sorted(SEC_PRIMARY_FORM_BASES),
            "secondary_news_used": False,
            "yahoo_fundamentals_used": False,
            "llm_used": False,
            "issuer_ir_live_collection": "deferred_until_authoritative_url_registry_and_adapter_review",
        },
        "anti_hindsight": {
            "historical_backfill": False,
            "first_entity_run_opens_collection_window_only": True,
            "dormant_period_filings_backfilled_on_reactivation": False,
            "reactivation_opens_new_collection_window": True,
            "exact_acceptance_timestamp_required": True,
            "timezone_less_sec_acceptance_policy": "use_later_of_America_New_York_or_fixed_EST_interpretation",
            "pre_window_filings_are_cursor_only_not_evidence": True,
        },
        "interpretation_boundary": {
            "primary_observations_created": True,
            "belief_support_or_opposition_assigned": False,
            "metric_change_direction_assigned": False,
            "historical_comparison_performed": False,
            "entity_forecasts_created": False,
            "reason": "Raw primary facts are not equivalent to bullish/bearish Belief evidence; interpretation requires a separately reviewed contract.",
        },
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
        "promotion_evidence_standard": promotion_evidence_standard(),
        "reporting_regimes": regimes,
        "sample": {
            "active_entities": len(active_rows),
            "dormant_entities": len(dormant_rows),
            "source_resolved_active_entities": source_resolved,
            "prospective_observations_total": len(observation_rows),
            "prospective_filing_observations_total": filing_count,
            "prospective_structured_fact_observations_total": fact_count,
            "new_observations_this_run": new_observations,
            "new_filing_observations_this_run": new_filing_observations,
            "new_structured_fact_observations_this_run": new_fact_observations,
            "source_issues_this_run": len(run_issues),
        },
        "entities": {
            "active": sorted(active_rows, key=lambda row: str(row.get("entity_id"))),
            "dormant": sorted(dormant_rows, key=lambda row: str(row.get("entity_id"))),
        },
        "source_issues": run_issues,
        "next_stage_not_enabled": {
            "primary_observation_to_belief_evidence_interpretation": True,
            "entity_forecast_capture": True,
            "brace_entity_bridge": True,
            "promotion_gate": True,
        },
    }
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="PR13 Entity Primary-Source Evidence Foundation")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--portfolio", default=str(DEFAULT_PORTFOLIO))
    parser.add_argument("--analysis", default=str(DEFAULT_ANALYSIS))
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    args = parser.parse_args()
    report = run(
        Path(args.state_dir),
        portfolio_path=Path(args.portfolio),
        analysis_path=Path(args.analysis),
        universe_path=Path(args.universe),
    )
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "reporting_regimes": report["reporting_regimes"],
        "active_decision_influence": report["active_decision_influence"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
