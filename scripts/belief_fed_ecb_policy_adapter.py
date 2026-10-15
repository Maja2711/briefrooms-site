from __future__ import annotations

"""Small, conservative EUR/USD policy-SPREAD impulse from two official rate series.

This is the realized 30-day CHANGE in Fed target-upper minus ECB deposit
facility, NOT OIS/FedWatch policy expectations and NOT a snapshot of US rates
alone. Policy-rate levels are not directional without a change. Missing, late,
future or malformed data never generates trading Evidence.
"""

import csv
import io
import math
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from belief_adapter_contract import (
    AdapterResult, EvidenceAssessment, Observation, observation_to_evidence,
)
from belief_core import iso_z

FED_SERIES = "DFEDTARU"  # Board of Governors via Federal Reserve Bank of St Louis
ECB_SERIES = "FM.B.U2.EUR.4F.KR.DFR.LEV"  # ECB deposit facility official rate
FED_REFERENCE = "https://fred.stlouisfed.org/series/DFEDTARU"
ECB_REFERENCE = "https://data.ecb.europa.eu/data/datasets/FM/FM.B.U2.EUR.4F.KR.DFR.LEV"
ECB_CSV_URL = (
    "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.DFR.LEV"
    "?lastNObservations=100&format=csvdata"
)
LOOKBACK_DAYS = 30
FED_MAX_LAST_OBSERVATION_DAYS = 3
MATERIAL_SPREAD_CHANGE_PP = 0.05  # 5bp; unchanged official rates are NO SIGNAL


def _fetch_csv(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "BriefRooms-PolicyDifferential/1.0",
                      "Accept": "text/csv,application/vnd.sdmx.csv, */*",
                      "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        return response.read(1_000_000).decode("utf-8-sig")


def _rates_csv(text: str, *, date_field: str, value_field: str, as_of: date) -> list[tuple[date, float]]:
    rows = csv.DictReader(io.StringIO(text))
    if not rows.fieldnames or date_field not in rows.fieldnames or value_field not in rows.fieldnames:
        raise ValueError("policy_rate_csv_schema_invalid")
    result: dict[date, float] = {}
    for row in rows:
        try:
            day = date.fromisoformat(str(row[date_field]).strip()[:10])
            rate = float(str(row[value_field]).strip())
        except (TypeError, ValueError):
            continue
        if day > as_of or not math.isfinite(rate) or not -3 <= rate <= 30:
            continue
        result[day] = rate
    return sorted(result.items())


def _last_at(rows: list[tuple[date, float]], day: date) -> float | None:
    eligible = (value for d, value in rows if d <= day)
    return list(eligible)[-1] if rows and rows[0][0] <= day else None


def build_policy_spread(
    now: datetime,
    fed_csv: str,
    ecb_csv: str,
) -> tuple[dict, Observation | None]:
    now = now.astimezone(timezone.utc)
    as_of = now.date()
    cutoff = as_of - timedelta(days=LOOKBACK_DAYS)
    fed = _rates_csv(fed_csv, date_field="DATE", value_field=FED_SERIES, as_of=as_of)
    ecb = _rates_csv(ecb_csv, date_field="TIME_PERIOD", value_field="OBS_VALUE", as_of=as_of)
    status = {
        "schema_version": "eurusd-official-policy-spread-v1",
        "data_kind": "realized_official_policy_rate_levels_not_market_forward_expectations",
        "fed_source_ref": FED_REFERENCE,
        "ecb_source_ref": ECB_REFERENCE,
        "fed_series": FED_SERIES,
        "ecb_series": ECB_SERIES,
        "lookback_days": LOOKBACK_DAYS,
        "ready": False,
        "reason": "insufficient_source_history",
    }
    if not fed or not ecb:
        return status, None
    fed_date, fed_current = fed[-1]
    ecb_date, ecb_current = ecb[-1]
    status.update({
        "fed_latest_date": fed_date.isoformat(),
        "ecb_last_change_date": ecb_date.isoformat(),
        "fed_latest_percent": fed_current,
        "ecb_latest_percent": ecb_current,
    })
    if (as_of - fed_date).days > FED_MAX_LAST_OBSERVATION_DAYS:
        status["reason"] = "fed_daily_rate_stale"
        return status, None
    fed_before = _last_at(fed, cutoff)
    ecb_before = _last_at(ecb, cutoff)
    if fed_before is None or ecb_before is None:
        return status, None
    spread_now = fed_current - ecb_current
    spread_then = fed_before - ecb_before
    delta = spread_now - spread_then
    status.update({
        "current_spread_pp": round(spread_now, 6),
        "baseline_spread_pp": round(spread_then, 6),
        "spread_change_pp": round(delta, 6),
        "ready": True,
        "reason": "material_spread_change" if abs(delta) >= MATERIAL_SPREAD_CHANGE_PP else "no_material_spread_change",
    })
    if abs(delta) < MATERIAL_SPREAD_CHANGE_PP:
        return status, None

    # A NARROWING Fed-minus-ECB spread favours EUR; a widening one favours USD.
    # Evidence is derived from BOTH official series, one independence cluster.
    sign = 1 if delta < 0 else -1
    strength = min(0.24, 0.08 + 0.14 * min(1.0, abs(delta) / 0.25))
    observation = Observation.make(
        adapter="fed_ecb_policy_differential",
        metric="eurusd_realized_official_policy_spread_change_30d",
        entity="EURUSD", observed_at=iso_z(now),
        value=round(delta, 6), unit="percentage_points",
        source="Deterministic Fed target-upper minus ECB deposit-facility 30d change",
        source_type="derived",
        source_ref=f"derived:{FED_SERIES}+{ECB_SERIES}",
        reliability=0.85,
        independence_cluster="fed_ecb:official_policy_spread:30d",
        tags=("EURUSD", "Fed", "ECB", "policy_differential", "official"),
        metadata={
            **status,
            "serial_state_snapshot": True,
            "source_provenance": [FED_REFERENCE, ECB_REFERENCE],
            "not_a_forward_curve": True,
        },
    )
    return status, observation


class FedEcbPolicyDifferentialAdapter:
    name = "fed_ecb_policy_differential"
    version = "1.0.0"

    def __init__(self, fetcher: Callable[[str], str] = _fetch_csv) -> None:
        self.fetcher = fetcher
        self.last_status: dict = {"ready": False, "reason": "not_checked"}

    def run(self, now: datetime) -> AdapterResult:
        fed_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode({
            "id": FED_SERIES,
            "cosd": (now.astimezone(timezone.utc).date() - timedelta(days=90)).isoformat(),
        })
        try:
            fed_csv = self.fetcher(fed_url)
            ecb_csv = self.fetcher(ECB_CSV_URL)
            status, observation = build_policy_spread(now, fed_csv, ecb_csv)
            self.last_status = status
        except Exception as exc:
            self.last_status = {"ready": False, "reason": "official_source_unavailable",
                                "error_type": type(exc).__name__,
                                "fed_source_ref": FED_REFERENCE, "ecb_source_ref": ECB_REFERENCE}
            return AdapterResult(self.name, (), ())
        if observation is None:
            return AdapterResult(self.name, (), ())
        from belief_adapter_contract import EvidenceAssessment
        assessment = EvidenceAssessment(
            belief_id="eurusd.policy_differential.supportive",
            direction=1 if observation.value < 0 else -1,
            strength=min(0.24, 0.08 + 0.14 * min(1.0, abs(observation.value) / 0.25)),
            evidence_type="official_fed_ecb_policy_spread_change_30d",
            note="30d realized official Fed-minus-ECB rate spread change; not implied expectations",
            independence_cluster="fed_ecb:official_policy_spread:30d",
            metadata={"method": "fed_target_upper_minus_ecb_deposit_change_30d",
                      "source_provenance": [FED_REFERENCE, ECB_REFERENCE],
                      "serial_state_snapshot": True, "no_trade_authority": True},
        )
        return AdapterResult(self.name, (observation,), (observation_to_evidence(observation, assessment),))
