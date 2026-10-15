from __future__ import annotations

"""LSEG Data Platform IPA -> verified BriefRooms two-currency OIS curves.

Connects using LSEG's OAuth2 v2 *service* credentials and documented
quantitative-analytics zc-curves endpoint. Does not rely on the Workspace
desktop, scrape prices, or send vendor data to a public repository.

Important: the IPA response must contain source MARKED market observation
timestamps with clock + timezone. A mere valuationDate or GitHub fetch time
is NOT accepted as an observed market price.
"""

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

AUTH_URL = "https://api.refinitiv.com/auth/oauth2/v2/token"
CURVES_URL = (
    "https://api.refinitiv.com/data/quantitative-analytics-curves-and-surfaces"
    "/v1/curves/zc-curves"
)
CONTRACT = "briefrooms-eurusd-ois-curves-v1"
_REQUIRED = {"USD": "SOFR", "EUR": "ESTR"}
_TENORS = {"3M": 3, "6M": 6, "1Y": 12, "12M": 12}
_MARKET_TS_KEYS = ("marketDataDateTime", "marketDataTimestamp", "quoteTimestamp", "marketDataDate")
_MAX_BODY_BYTES = 1024 * 1024


def _post(url: str, body: bytes, headers: Mapping[str, str], timeout: float = 8.0) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(_MAX_BODY_BYTES + 1)
    if len(raw) > _MAX_BODY_BYTES:
        raise ValueError("lseg_oversized_api_response")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("lseg_api_object_required")
    return payload


def _quote_time(row: Mapping[str, Any]) -> str:
    # LSEG valuationDate is not a quote timestamp; date-only inputs fail closed.
    params = row.get("curveParameters") or {}
    if not isinstance(params, Mapping):
        params = {}
    curves = row.get("curves") or {}
    ois = curves.get("OIS") or {} if isinstance(curves, Mapping) else {}
    for node in (ois, params, row):
        if not isinstance(node, Mapping):
            continue
        for key in _MARKET_TS_KEYS:
            value = node.get(key)
            if not isinstance(value, str):
                continue
            if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)", value):
                continue
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if stamp.tzinfo is None:
                continue
            return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise ValueError("lseg_curve_source_quote_time_missing_or_date_only")


def _curve_from_ipa(row: Mapping[str, Any], currency: str) -> dict[str, Any]:
    index = _REQUIRED[currency]
    definition = row.get("curveDefinition")
    if not isinstance(definition, Mapping):
        raise ValueError(f"lseg_{currency}_definition_missing")
    if (definition.get("currency") != currency
            or str(definition.get("indexName", "")).upper() != index
            or definition.get("discountingTenor") != "OIS"
            or str(definition.get("source", "")).lower() not in {"refinitiv", "lseg"}):
        raise ValueError(f"lseg_{currency}_wrong_curve_definition")
    curves = row.get("curves")
    if not isinstance(curves, Mapping) or not isinstance(curves.get("OIS"), Mapping):
        raise ValueError(f"lseg_{currency}_ois_curve_missing")
    points = curves["OIS"].get("curvePoints")
    if not isinstance(points, list):
        raise ValueError(f"lseg_{currency}_curve_points_missing")
    nodes: dict[int, float] = {}
    for point in points:
        if not isinstance(point, Mapping):
            continue
        months = _TENORS.get(point.get("tenor"))
        if months is None:
            continue
        try:
            raw = point["discountFactor"]
            if isinstance(raw, bool):
                raise ValueError("boolean_discount_factor")
            df = float(raw)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"lseg_{currency}_discount_factor_invalid") from exc
        if not math.isfinite(df) or months in nodes:
            raise ValueError(f"lseg_{currency}_discount_factor_invalid_or_duplicate")
        nodes[months] = df
    if set(nodes) != {3, 6, 12}:
        raise ValueError(f"lseg_{currency}_required_ois_curve_points_missing")
    stamp = _quote_time(row)
    return {
        "currency": currency,
        "overnight_index": index,
        "curve_type": "ois_discount_factors",
        "construction": "bootstrapped_from_ois_swaps",
        "market_observed_at": stamp,
        "source_ref": CURVES_URL,
        "instrument_id": str(definition.get("id") or f"LSEG:IPA:{currency}:{index}:OIS"),
        "discount_factors": [{"months": tenor, "df": nodes[tenor]} for tenor in (3, 6, 12)],
    }


def normalize_lseg_ipa_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 2:
        raise ValueError("lseg_ipa_requires_exactly_two_currency_curves")
    by_currency: dict[str, dict[str, Any]] = {}
    for row in data:
        if not isinstance(row, Mapping):
            raise ValueError("lseg_ipa_curve_invalid")
        defn = row.get("curveDefinition")
        ccy = defn.get("currency") if isinstance(defn, Mapping) else None
        if ccy not in _REQUIRED or ccy in by_currency:
            raise ValueError("lseg_ipa_wrong_currency_or_duplicate")
        by_currency[ccy] = _curve_from_ipa(row, ccy)
    if set(by_currency) != {"USD", "EUR"}:
        raise ValueError("lseg_ipa_two_currency_coverage_missing")
    return {
        "schema_version": CONTRACT,
        "provider": "LSEG Data Platform Instrument Pricing Analytics",
        "curves": by_currency,
    }


def lseg_ois_request_body() -> dict[str, Any]:
    return {
        "universe": [
            {
                "curveParameters": {"priceSide": "Mid"},
                "curveDefinition": {
                    "currency": currency, "indexName": index,
                    "indexTenors": ["OIS"], "discountingTenor": "OIS",
                    "source": "Refinitiv",
                },
            }
            for currency, index in _REQUIRED.items()
        ],
        "outputs": ["Constituents"],
    }


def fetch_lseg_ois_curves(
    client_id: str,
    client_secret: str,
    *,
    post: Callable[[str, bytes, Mapping[str, str], float], Mapping[str, Any]] = _post,
) -> dict[str, Any]:
    if not client_id.strip() or not client_secret.strip():
        raise ValueError("lseg_service_credentials_missing")
    login = post(
        AUTH_URL,
        urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode("utf-8"),
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        6.0,
    )
    token = login.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("lseg_oauth_token_unavailable")
    content = post(
        CURVES_URL,
        json.dumps(lseg_ois_request_body(), separators=(",", ":")).encode("utf-8"),
        {"Content-Type": "application/json", "Accept": "application/json",
         "Authorization": "Bearer " + token},
        10.0,
    )
    # Never expose token or vendor raw payload in CI logs.
    return normalize_lseg_ipa_response(content)
