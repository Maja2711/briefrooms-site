from __future__ import annotations

"""EUR/USD Fed-vs-ECB *market-implied* OIS forward differential.

Only vendor-sourced, point-in-time USD SOFR and EUR €STR OIS discount curves
are accepted. Overnight prints, futures, government bonds and official policy
rate levels are NOT substitutions for OIS. No configured vendor = NO Evidence.
"""
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, observation_to_evidence
from belief_core import iso_z
from belief_lseg_ois_market_feed import fetch_lseg_ois_curves

FEED_URL_ENV = "BELIEF_EURUSD_OIS_CURVES_URL"
FEED_TOKEN_ENV = "BELIEF_EURUSD_OIS_CURVES_TOKEN"
CONTRACT = "briefrooms-eurusd-ois-curves-v1"
TENORS_MONTHS = (3, 6, 12)
MAX_QUOTE_AGE_MINUTES = 75.0
MAX_CROSS_CURRENCY_SKEW_MINUTES = 10.0
SIGNAL_MIN_SPREAD_CHANGE_BPS = 5.0
MAX_SIGNAL_STRENGTH = 0.22
BELIEF_ID = "eurusd.policy_differential.supportive"


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ois_market_timestamp_required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("ois_market_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("ois_market_timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


def _reference(value: Any) -> str:
    text = str(value or "").strip()
    uri = urllib.parse.urlparse(text)
    if uri.scheme != "https" or not uri.netloc or uri.username or uri.password:
        raise ValueError("ois_source_reference_requires_https")
    return text


def _curve(payload: Any, *, currency: str, index: str, at: datetime) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"ois_{currency}_curve_missing")
    if payload.get("currency") != currency or str(payload.get("overnight_index", "")).upper() != index:
        raise ValueError(f"ois_{currency}_currency_or_index_mismatch")
    if payload.get("curve_type") != "ois_discount_factors":
        raise ValueError(f"ois_{currency}_must_be_OIS_discount_factors")
    if payload.get("construction") != "bootstrapped_from_ois_swaps":
        raise ValueError(f"ois_{currency}_bootstrap_provenance_missing")
    observed = _time(payload.get("market_observed_at"))
    age = (at - observed).total_seconds() / 60.0
    if not -2.0 <= age <= MAX_QUOTE_AGE_MINUTES:
        raise ValueError(f"ois_{currency}_stale_or_future_market_quote")
    source = _reference(payload.get("source_ref"))
    instrument = str(payload.get("instrument_id") or "").strip()
    if not instrument:
        raise ValueError(f"ois_{currency}_instrument_id_missing")
    nodes = payload.get("discount_factors")
    if not isinstance(nodes, list):
        raise ValueError(f"ois_{currency}_nodes_missing")
    factors: dict[int, float] = {}
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError(f"ois_{currency}_node_invalid")
        months = node.get("months")
        if type(months) is not int or months not in TENORS_MONTHS or months in factors:
            raise ValueError(f"ois_{currency}_tenor_invalid_or_duplicate")
        raw = node.get("df")
        if isinstance(raw, bool) or raw is None:
            raise ValueError(f"ois_{currency}_discount_factor_invalid")
        try:
            df = float(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"ois_{currency}_discount_factor_invalid") from exc
        if not math.isfinite(df) or not 0.30 <= df <= 1.10:
            raise ValueError(f"ois_{currency}_discount_factor_out_of_bounds")
        factors[months] = df
    if set(factors) != set(TENORS_MONTHS):
        raise ValueError(f"ois_{currency}_incomplete_tenor_curve")

    def forward(start: int, end: int) -> float:
        # Continuously compounded, ACT/12 approximation. Provider must
        # deliver spot-normalized OIS discount factors, not par swap rates.
        value = -math.log(factors[end] / factors[start]) * 1200.0 / (end - start)
        if not math.isfinite(value) or not -3.0 <= value <= 30.0:
            raise ValueError(f"ois_{currency}_forward_out_of_bounds")
        return value

    return {
        "market_observed_at": observed,
        "source_ref": source,
        "instrument_id": instrument,
        "currency": currency,
        "overnight_index": index,
        "forward_3m6m_percent": forward(3, 6),
        "forward_6m12m_percent": forward(6, 12),
    }


def calculate_policy_ois(payload: Mapping[str, Any], now: datetime) -> tuple[dict[str, Any], Observation | None]:
    at = now.astimezone(timezone.utc)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != CONTRACT:
        raise ValueError("ois_feed_contract_invalid")
    provider = str(payload.get("provider") or "").strip()
    if not provider or provider.lower() in {"unknown", "synthetic", "demo", "test"}:
        raise ValueError("ois_provider_required")
    curves = payload.get("curves")
    if not isinstance(curves, Mapping):
        raise ValueError("ois_curves_missing")
    usd = _curve(curves.get("USD"), currency="USD", index="SOFR", at=at)
    eur = _curve(curves.get("EUR"), currency="EUR", index="ESTR", at=at)
    skew = abs((usd["market_observed_at"] - eur["market_observed_at"]).total_seconds()) / 60.0
    if skew > MAX_CROSS_CURRENCY_SKEW_MINUTES:
        raise ValueError("ois_usd_eur_quote_times_not_aligned")
    # Prefer conservative (older) of the two source quote timestamps for
    # downstream Evidence freshness and prevent ingestion-time laundering.
    observed_at = min(usd["market_observed_at"], eur["market_observed_at"])
    near_spread = usd["forward_3m6m_percent"] - eur["forward_3m6m_percent"]
    far_spread = usd["forward_6m12m_percent"] - eur["forward_6m12m_percent"]
    change_bps = (far_spread - near_spread) * 100.0
    status: dict[str, Any] = {
        "ready": True,
        "source_status": "verified_two_leg_OIS_forward_curves",
        "provider": provider,
        "market_observed_at": iso_z(observed_at),
        "usd_source_ref": usd["source_ref"],
        "eur_source_ref": eur["source_ref"],
        "usd_instrument_id": usd["instrument_id"],
        "eur_instrument_id": eur["instrument_id"],
        "usd_index": "SOFR",
        "eur_index": "ESTR",
        "forward_windows": ["3M-6M", "6M-12M"],
        "near_spread_pp": round(near_spread, 6),
        "far_spread_pp": round(far_spread, 6),
        "forward_spread_change_bps": round(change_bps, 4),
        "quote_skew_minutes": round(skew, 3),
        "realized_policy_rates_used": False,
        "futures_proxy_used": False,
        "meeting_probabilities_claimed": False,
    }
    if abs(change_bps) < SIGNAL_MIN_SPREAD_CHANGE_BPS:
        status["signal_status"] = "no_material_forward_spread_slope"
        return status, None
    status["signal_status"] = "eligible"
    # Declining USD-minus-EUR market-implied forward spread favours EUR/USD.
    # Weight is capped separately in Epistemic at 5%.
    observation = Observation.make(
        adapter="fed_ecb_ois_forward", metric="eurusd_ois_forward_spread_slope",
        entity="EURUSD", observed_at=iso_z(observed_at), value=round(change_bps, 5),
        unit="basis_points",
        source=f"{provider}: SOFR OIS versus ESTR OIS bootstrapped discount curves",
        source_type="derived", source_ref=f"derived:ois:{usd['instrument_id']}:{eur['instrument_id']}",
        reliability=0.78, independence_cluster="fed_ecb:market_ois_forward:3m6m_6m12m",
        tags=("EURUSD", "OIS", "SOFR", "ESTR", "forward_expectations"),
        metadata={**status, "serial_state_snapshot": True, "source_provenance": [
            usd["source_ref"], eur["source_ref"],
        ], "market_time_source": "oldest_currency_leg", "trade_execution_enabled": False},
    )
    return status, observation


def fetch_ois_feed(url: str, token: str = "") -> Mapping[str, Any]:
    address = _reference(url)
    headers = {"Accept": "application/json", "Cache-Control": "no-cache",
               "User-Agent": "BriefRooms-OIS-Policy/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(address, headers=headers)
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("ois_feed_too_large")
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, Mapping):
        raise ValueError("ois_feed_contract_invalid")
    return decoded


class FedEcbOisForwardAdapter:
    name = "fed_ecb_ois_forward"
    version = "1.0.0"

    def __init__(self, fetcher: Callable[..., Mapping[str, Any]] = fetch_ois_feed,
                 url: str | None = None, token: str | None = None,
                 lseg_fetcher: Callable[..., Mapping[str, Any]] = fetch_lseg_ois_curves,
                 lseg_client_id: str | None = None,
                 lseg_client_secret: str | None = None) -> None:
        self.fetcher = fetcher
        self.url = str(url if url is not None else os.environ.get(FEED_URL_ENV, "")).strip()
        self.token = str(token if token is not None else os.environ.get(FEED_TOKEN_ENV, "")).strip()
        self.lseg_fetcher = lseg_fetcher
        self.lseg_client_id = str(lseg_client_id if lseg_client_id is not None
                                  else os.environ.get("LSEG_OIS_CLIENT_ID", "")).strip()
        self.lseg_client_secret = str(lseg_client_secret if lseg_client_secret is not None
                                      else os.environ.get("LSEG_OIS_CLIENT_SECRET", "")).strip()
        self.last_status: dict[str, Any] = {"ready": False, "reason": "not_checked"}

    def run(self, now: datetime) -> AdapterResult:
        use_lseg = bool(self.lseg_client_id and self.lseg_client_secret)
        if not use_lseg and not self.url:
            self.last_status = {"ready": False, "reason": "ois_market_feed_not_configured",
                                "expected_schema_version": CONTRACT,
                                "lseg_service_credentials_complete": False}
            return AdapterResult(self.name, (), ())
        try:
            # A complete, entitled LSEG cloud service account takes priority.
            # A desktop Workspace login never grants rights to cloud API data.
            payload = (self.lseg_fetcher(self.lseg_client_id, self.lseg_client_secret)
                       if use_lseg else self.fetcher(self.url, self.token))
            status, observation = calculate_policy_ois(payload, now)
            self.last_status = {**status, "access_route": "lseg_ipa_oauth_v2" if use_lseg else "sourced_https_feed"}
        except Exception as exc:
            self.last_status = {"ready": False, "reason": "ois_market_feed_unavailable_or_invalid",
                                "error_type": type(exc).__name__,
                                "error_code": str(exc)[:120] if isinstance(exc, ValueError) else type(exc).__name__}
            return AdapterResult(self.name, (), ())
        if observation is None:
            return AdapterResult(self.name, (), ())
        basis_points = float(observation.value)
        assessment = EvidenceAssessment(
            belief_id="eurusd.policy_differential.supportive",
            direction=1 if basis_points < 0 else -1,
            strength=min(MAX_SIGNAL_STRENGTH, 0.06 + 0.12 * min(1.0, abs(basis_points) / 30.0)),
            evidence_type="market_ois_forward_spread_3m6m_6m12m",
            note="SOFR-vs-ESTR OIS forward spread slope from verified market discount curves",
            independence_cluster="fed_ecb:market_ois_forward:3m6m_6m12m",
            metadata={"serial_state_snapshot": True, "source_provenance": observation.metadata["source_provenance"],
                      "time_consistency_checked": True, "trade_execution_enabled": False,
                      "ois_market_implied_not_official_policy": True},
        )
        return AdapterResult(self.name, (observation,), (observation_to_evidence(observation, assessment),))
