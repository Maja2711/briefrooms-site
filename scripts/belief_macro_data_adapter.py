from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, clamp, observation_to_evidence
from belief_core import iso_z

BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_SERIES = {
    "cpi_sa": "CUSR0000SA0",
    "payrolls": "CES0000000001",
    "unemployment": "LNS14000000",
}
MAX_DATA_AGE_DAYS = 75


@dataclass(frozen=True)
class SeriesPoint:
    year: int
    month: int
    value: float

    @property
    def period(self) -> str:
        return f"{self.year:04d}-M{self.month:02d}"


class BLSClient:
    def __init__(self, timeout: int = 20, user_agent: str = "BriefRooms-BeliefCore/1.0 research https://briefrooms.com") -> None:
        self.timeout = int(timeout)
        self.user_agent = user_agent

    def fetch(self, series_ids: Sequence[str], *, start_year: int, end_year: int) -> Mapping[str, object]:
        payload = json.dumps({
            "seriesid": list(series_ids),
            "startyear": str(start_year),
            "endyear": str(end_year),
        }).encode("utf-8")
        request = urllib.request.Request(
            BLS_API,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def parse_bls_series(payload: Mapping[str, object]) -> Dict[str, Tuple[SeriesPoint, ...]]:
    status = str(payload.get("status") or "")
    if status and status != "REQUEST_SUCCEEDED":
        return {}
    results = payload.get("Results")
    if not isinstance(results, Mapping):
        return {}
    raw_series = results.get("series")
    if not isinstance(raw_series, list):
        return {}

    out: Dict[str, Tuple[SeriesPoint, ...]] = {}
    for row in raw_series:
        if not isinstance(row, Mapping):
            continue
        series_id = str(row.get("seriesID") or "")
        points: List[SeriesPoint] = []
        data = row.get("data")
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, Mapping):
                continue
            period = str(item.get("period") or "")
            if len(period) != 3 or not period.startswith("M") or period == "M13":
                continue
            try:
                year = int(item.get("year"))
                month = int(period[1:])
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            if 1 <= month <= 12 and math.isfinite(value):
                points.append(SeriesPoint(year, month, value))
        points.sort(key=lambda p: (p.year, p.month))
        if points:
            out[series_id] = tuple(points)
    return out


def _period_age_days(point: SeriesPoint, now: datetime) -> int:
    # Monthly BLS observations describe the named month; use a month-end-ish anchor
    # only for stale-data gating, never as a sourced release timestamp.
    anchor = datetime(point.year, point.month, 28, tzinfo=timezone.utc)
    return max(0, (now.astimezone(timezone.utc) - anchor).days)


def _primary_observation(
    *,
    now: datetime,
    metric: str,
    series_id: str,
    point: SeriesPoint,
    value: object,
    unit: str,
    cluster: str,
    metadata: Mapping[str, object],
) -> Observation:
    age_days = _period_age_days(point, now)
    return Observation.make(
        adapter="macro_data",
        metric=metric,
        entity="US_MACRO",
        observed_at=iso_z(now),
        value=value,
        unit=unit,
        source="U.S. Bureau of Labor Statistics Public Data API",
        source_type="primary",
        source_ref=BLS_API,
        reliability=.99,
        independence_cluster=cluster,
        status="stale" if age_days > MAX_DATA_AGE_DAYS else "ok",
        tags=("macro_data", "primary_source", "bls"),
        metadata={
            "series_id": series_id,
            "data_period": point.period,
            "data_age_days": age_days,
            **dict(metadata),
        },
    )


def _derived_observation(
    *,
    now: datetime,
    metric: str,
    value: object,
    unit: str,
    cluster: str,
    primary_ids: Sequence[str],
    primary_period: str,
    metadata: Mapping[str, object],
) -> Observation:
    return Observation.make(
        adapter="macro_data",
        metric=metric,
        entity="US_MACRO",
        observed_at=iso_z(now),
        value=value,
        unit=unit,
        source="Deterministic transform of U.S. Bureau of Labor Statistics data",
        source_type="derived",
        source_ref=f"derived:{','.join(primary_ids)}",
        reliability=.99,
        independence_cluster=cluster,
        tags=("macro_data", "deterministic"),
        metadata={
            "upstream_observation_ids": list(primary_ids),
            "primary_source_ref": BLS_API,
            "data_period": primary_period,
            **dict(metadata),
        },
    )


def _inflation_result(points: Sequence[SeriesPoint], now: datetime):
    if len(points) < 4:
        return (), ()
    latest = points[-1]
    if latest.value <= 0 or points[-4].value <= 0:
        return (), ()
    three_month_annualized = (latest.value / points[-4].value) ** 4 - 1.0
    yoy = None
    if len(points) >= 13 and points[-13].value > 0:
        yoy = latest.value / points[-13].value - 1.0

    cluster = f"bls:inflation:{latest.period}"
    primary = _primary_observation(
        now=now,
        metric="cpi_index_sa",
        series_id=BLS_SERIES["cpi_sa"],
        point=latest,
        value=latest.value,
        unit="index",
        cluster=cluster,
        metadata={"three_month_annualized": three_month_annualized, "year_over_year": yoy},
    )
    observations = [primary]
    evidence = []
    if primary.status != "ok":
        return tuple(observations), tuple(evidence)

    direction = 0
    strength = 0.0
    note = ""
    if three_month_annualized >= .035:
        direction = -1
        strength = clamp(.16 + (three_month_annualized - .035) / .06 * .28, .16, .44)
        note = f"BLS CPI 3-month annualized inflation is {three_month_annualized:.2%}, a deterministic inflation-pressure headwind to supportive financial conditions."
    elif three_month_annualized <= .022:
        direction = 1
        strength = clamp(.14 + (.022 - three_month_annualized) / .04 * .22, .14, .36)
        note = f"BLS CPI 3-month annualized inflation is {three_month_annualized:.2%}, a deterministic disinflation tailwind to supportive financial conditions."

    if direction:
        derived = _derived_observation(
            now=now,
            metric="cpi_policy_pressure",
            value=round(three_month_annualized, 8),
            unit="3m_annualized_rate",
            cluster=cluster,
            primary_ids=(primary.observation_id,),
            primary_period=latest.period,
            metadata={
                "threshold_high": .035,
                "threshold_low": .022,
                "year_over_year": yoy,
                "interpretation": "policy-pressure proxy; not a release-surprise model",
            },
        )
        observations.append(derived)
        evidence.append(observation_to_evidence(
            derived,
            EvidenceAssessment(
                belief_id="spx.financial_conditions.supportive",
                direction=direction,
                strength=strength,
                evidence_type="bls_inflation_pressure",
                note=note,
                independence_cluster=cluster,
                metadata={
                    "primary_observation_ids": [primary.observation_id],
                    "primary_source_ref": BLS_API,
                    "data_period": latest.period,
                    "three_month_annualized": three_month_annualized,
                    "year_over_year": yoy,
                },
            ),
        ))
    return tuple(observations), tuple(evidence)


def _labor_result(payroll: Sequence[SeriesPoint], unemployment: Sequence[SeriesPoint], now: datetime):
    if len(payroll) < 4 or len(unemployment) < 4:
        return (), ()
    p_latest = payroll[-1]
    u_latest = unemployment[-1]
    # Require both series to refer to the same monthly data period before combining.
    if p_latest.period != u_latest.period:
        return (), ()

    payroll_changes = [
        payroll[i].value - payroll[i - 1].value
        for i in range(len(payroll) - 3, len(payroll))
    ]
    payroll_3m_avg = sum(payroll_changes) / len(payroll_changes)
    unemployment_3m_change = u_latest.value - unemployment[-4].value
    cluster = f"bls:labor:{p_latest.period}"

    payroll_primary = _primary_observation(
        now=now,
        metric="total_nonfarm_payroll_level",
        series_id=BLS_SERIES["payrolls"],
        point=p_latest,
        value=p_latest.value,
        unit="thousands",
        cluster=cluster,
        metadata={"three_month_average_change_thousands": payroll_3m_avg},
    )
    unemployment_primary = _primary_observation(
        now=now,
        metric="unemployment_rate",
        series_id=BLS_SERIES["unemployment"],
        point=u_latest,
        value=u_latest.value,
        unit="percent",
        cluster=cluster,
        metadata={"three_month_change_percentage_points": unemployment_3m_change},
    )
    observations = [payroll_primary, unemployment_primary]
    evidence = []
    if payroll_primary.status != "ok" or unemployment_primary.status != "ok":
        return tuple(observations), tuple(evidence)

    direction = 0
    strength = 0.0
    note = ""
    if payroll_3m_avg >= 125.0 and unemployment_3m_change <= .10:
        direction = 1
        strength = clamp(.15 + (payroll_3m_avg - 125.0) / 250.0 * .18, .15, .33)
        note = f"BLS labor data show a {payroll_3m_avg:.0f}k three-month average payroll gain with unemployment changing {unemployment_3m_change:+.1f}pp over three months, a modest macro tailwind to the bullish SPX trend belief."
    elif payroll_3m_avg <= 50.0 or unemployment_3m_change >= .30:
        direction = -1
        weakness = max((50.0 - payroll_3m_avg) / 150.0, (unemployment_3m_change - .30) / .70, 0.0)
        strength = clamp(.17 + weakness * .18, .17, .35)
        note = f"BLS labor data show a {payroll_3m_avg:.0f}k three-month average payroll gain with unemployment changing {unemployment_3m_change:+.1f}pp over three months, a modest macro headwind to the bullish SPX trend belief."

    if direction:
        primary_ids = (payroll_primary.observation_id, unemployment_primary.observation_id)
        derived = _derived_observation(
            now=now,
            metric="labor_growth_regime",
            value={
                "payroll_3m_average_change_thousands": round(payroll_3m_avg, 3),
                "unemployment_3m_change_percentage_points": round(unemployment_3m_change, 3),
            },
            unit="macro_regime",
            cluster=cluster,
            primary_ids=primary_ids,
            primary_period=p_latest.period,
            metadata={
                "strong_payroll_threshold_thousands": 125.0,
                "weak_payroll_threshold_thousands": 50.0,
                "unemployment_deterioration_threshold_pp": .30,
                "interpretation": "growth-regime proxy; not a market-return forecast",
            },
        )
        observations.append(derived)
        evidence.append(observation_to_evidence(
            derived,
            EvidenceAssessment(
                belief_id="spx.trend.bullish",
                direction=direction,
                strength=strength,
                evidence_type="bls_labor_growth_regime",
                note=note,
                independence_cluster=cluster,
                metadata={
                    "primary_observation_ids": list(primary_ids),
                    "primary_source_ref": BLS_API,
                    "data_period": p_latest.period,
                    "payroll_3m_average_change_thousands": payroll_3m_avg,
                    "unemployment_3m_change_percentage_points": unemployment_3m_change,
                },
            ),
        ))
    return tuple(observations), tuple(evidence)


class MacroDataAdapter:
    name = "macro_data"
    version = "2.0.0"

    def __init__(self, *, client: Optional[BLSClient] = None) -> None:
        self.client = client or BLSClient()

    def run(self, now: datetime) -> AdapterResult:
        now = now.astimezone(timezone.utc)
        try:
            payload = self.client.fetch(
                tuple(BLS_SERIES.values()),
                start_year=now.year - 2,
                end_year=now.year,
            )
            series = parse_bls_series(payload)
        except Exception:
            series = {}

        observations: List[Observation] = []
        evidence = []
        inflation_obs, inflation_ev = _inflation_result(series.get(BLS_SERIES["cpi_sa"], ()), now)
        labor_obs, labor_ev = _labor_result(
            series.get(BLS_SERIES["payrolls"], ()),
            series.get(BLS_SERIES["unemployment"], ()),
            now,
        )
        observations.extend(inflation_obs)
        observations.extend(labor_obs)
        evidence.extend(inflation_ev)
        evidence.extend(labor_ev)
        return AdapterResult(self.name, tuple(observations), tuple(evidence))
