"""As-of empirical EURUSD event paths; never a certified predictive model.

Historical samples must contain actual executable-side quotes, official event
provenance and explicit incremental costs. Missing evidence produces null
estimates. This module neither fetches nor creates historical prices.
"""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from eurusd_central_bank_wire import dt, digest, iso

SCHEMA_VERSION = "eurusd-cb-event-distribution-v1"
WINDOWS = (1, 5, 15, 30, 60, 120)
MAX_SAMPLES = 5000
MINIMUM_CALENDAR_SPAN_DAYS = 90


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _source_url(value: Any, *, bank: str | None = None) -> bool:
    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
            return False
        if bank:
            expected = "federalreserve.gov" if bank == "FED" else "ecb.europa.eu"
            return parsed.hostname.removeprefix("www.") == expected
        return "." in parsed.hostname and parsed.hostname not in {"localhost", "127.0.0.1"}
    except ValueError:
        return False


def load_model(path: str | Path | None) -> list[dict]:
    """Read only; absence/corruption never manufactures a calibration sample."""
    if path is None:
        return []
    try:
        target = Path(path)
        if target.stat().st_size > 30_000_000:
            return []
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
            return []
        rows = payload.get("samples")
        if not isinstance(rows, list) or len(rows) > MAX_SAMPLES or not all(isinstance(row, dict) for row in rows):
            return []
        return copy.deepcopy(rows)
    except (OSError, ValueError, TypeError):
        return []


def _sample(row: Mapping, *, now: datetime, bank: str, event_type: str, regime: str, direction: str) -> dict:
    if row.get("bank") != bank or row.get("event_type") != event_type or row.get("direction") != direction:
        raise ValueError("NONMATCHING_BANK_TYPE_DIRECTION")
    context = row.get("regime")
    if not isinstance(context, Mapping) or context.get("label") != regime:
        raise ValueError("NONMATCHING_REGIME")
    event = row.get("event_source", {})
    published, first_seen, event_ingested = (dt(event.get(key)) for key in ("published_at", "first_seen_at", "ingested_at"))
    if not published or not first_seen or not event_ingested or not published <= first_seen <= event_ingested < now:
        raise ValueError("EVENT_PROVENANCE_NOT_AVAILABLE_BEFORE_DECISION")
    if event.get("statement_verified") is not True or not _source_url(event.get("source_url"), bank=bank) or not re.fullmatch(r"[0-9a-f]{64}", str(event.get("document_sha256") or "")):
        raise ValueError("OFFICIAL_EVENT_DOCUMENT_UNVERIFIED")
    regime_at = dt(context.get("observed_at"))
    if not regime_at or regime_at > published or not _source_url(context.get("source_ref")):
        raise ValueError("REGIME_NOT_SOURCED_AS_OF_EVENT")
    closed, ingested = dt(row.get("outcome_closed_at")), dt(row.get("ingested_at"))
    if not closed or not ingested or not published + timedelta(minutes=120) <= closed <= ingested < now or ingested < event_ingested:
        raise ValueError("OUTCOME_NOT_COMPLETE_BEFORE_DECISION")
    event_id = row.get("event_id")
    if not isinstance(event_id, str) or not event_id or not row.get("immutable_id"):
        raise ValueError("MISSING_IMMUTABLE_EVENT_IDENTITY")
    supplied_hash = row.get("sample_sha256")
    if supplied_hash and supplied_hash != digest({key: value for key, value in row.items() if key != "sample_sha256"}):
        raise ValueError("SAMPLE_HASH_MISMATCH")
    source = row.get("price_source", {})
    resolution = _number(source.get("maximum_quote_gap_seconds"))
    if source.get("kind") != "BID_ASK" or source.get("verified") is not True or not source.get("source") or not _source_url(source.get("source_ref")) or resolution is None or not 1 <= resolution <= 300:
        raise ValueError("EXECUTABLE_PRICE_SOURCE_UNVERIFIED")
    cost, slippage = _number(row.get("incremental_cost_pips")), _number(row.get("event_slippage_pips"))
    if cost is None or slippage is None or cost < 0 or slippage < 0 or cost + slippage > 1000 or row.get("cost_semantics") != "INCREMENTAL_HOLD_VS_CLOSE_NOW":
        raise ValueError("COSTS_OR_SLIPPAGE_UNKNOWN")
    raw_quotes = row.get("quotes")
    if not isinstance(raw_quotes, list) or not 2 <= len(raw_quotes) <= 2000:
        raise ValueError("MISSING_BOUNDED_QUOTE_PATH")
    quotes, quote_ids = [], set()
    for quote in raw_quotes:
        observed, received = dt(quote.get("observed_at")), dt(quote.get("ingested_at"))
        bid, ask = _number(quote.get("bid")), _number(quote.get("ask"))
        if not observed or not received or not published <= observed <= received <= ingested < now or received - observed > timedelta(seconds=60):
            raise ValueError("PRICE_TIMESTAMP_NOT_AS_OF")
        if bid is None or ask is None or not .1 <= bid <= ask <= 5 or quote.get("verified") is not True or not quote.get("quote_id"):
            raise ValueError("NO_EXECUTABLE_BID_ASK")
        if quote["quote_id"] in quote_ids:
            raise ValueError("DUPLICATE_QUOTE_ID")
        quote_ids.add(quote["quote_id"])
        quotes.append({"at": observed, "received": received, "bid": bid, "ask": ask, "quote_id": quote["quote_id"]})
    quotes.sort(key=lambda quote: quote["at"])
    if quotes[0]["at"] - published > timedelta(seconds=60):
        raise ValueError("PUBLICATION_REFERENCE_QUOTE_MISSING")
    if any(right["at"] <= left["at"] or (right["at"] - left["at"]).total_seconds() > resolution for left, right in zip(quotes, quotes[1:])):
        raise ValueError("QUOTE_PATH_GAPS_OR_CONFLICTS")
    sign = 1 if direction == "LONG" else -1
    def mark(quote):
        return quote["bid"] if direction == "LONG" else quote["ask"]
    reference = mark(quotes[0])
    windows = {}
    summaries = row.get("windows", {})
    for minutes in WINDOWS:
        cutoff = published + timedelta(minutes=minutes)
        selected = [quote for quote in quotes if quote["at"] <= cutoff and quote["received"] <= ingested]
        if len(selected) < 2 or cutoff - selected[-1]["at"] > timedelta(seconds=60):
            raise ValueError("INCOMPLETE_EVENT_WINDOW")
        deltas = [sign * (mark(quote) - reference) * 10000 for quote in selected]
        actual = {"delta_pips": deltas[-1], "mae_pips": min(0., *deltas), "mfe_pips": max(0., *deltas)}
        # If precomputed summaries accompany the raw path, prove they describe
        # that same path rather than trusting favourable imported statistics.
        declared = summaries.get(str(minutes), summaries.get(minutes)) if isinstance(summaries, Mapping) else None
        if declared is not None:
            if not isinstance(declared, Mapping) or any(_number(declared.get(key)) is None or abs(float(declared[key]) - value) > 1e-6 for key, value in actual.items()):
                raise ValueError("SUMMARY_DISAGREES_WITH_EXECUTABLE_QUOTES")
        windows[str(minutes)] = {**actual, "path_delta_pips": deltas}
    return {"event_id": event_id, "source_url": event["source_url"], "published_at": published,
            "end_at": published + timedelta(minutes=120), "cost_pips": cost + slippage,
            "maximum_quote_gap_seconds": resolution, "windows": windows,
            "sample_sha256": digest(row), "immutable_id": row["immutable_id"]}


def _window(samples: Sequence[Mapping], minutes: int, *, current_net_R: float, risk_pips: float,
            stop_distance_pips: float, tp_distance_pips: float) -> dict:
    outcomes, worst_paths, givebacks, stop_hits, costs, raw_changes, gaps = [], [], [], [], [], [], []
    for sample in samples:
        path = sample["windows"][str(minutes)]
        deltas = path["path_delta_pips"]
        realized, stop_hit, used = deltas[-1], False, []
        for delta in deltas:
            used.append(delta)
            if delta <= -stop_distance_pips:
                # Retain the observed overshoot; never promise an ideal SL fill.
                realized, stop_hit = delta, True
                break
            if delta >= tp_distance_pips:
                # A bounded payoff scenario, explicitly not a fabricated fill.
                realized = min(delta, tp_distance_pips)
                break
        cost = sample["cost_pips"]
        net = current_net_R + (realized - cost) / risk_pips
        adverse = current_net_R + (min(used) - cost) / risk_pips
        outcomes.append(net)
        worst_paths.append(current_net_R + (path["mae_pips"] - cost) / risk_pips)
        givebacks.append(current_net_R > 0 and adverse <= 0)
        stop_hits.append(stop_hit)
        gaps.append(stop_hit and realized < -stop_distance_pips)
        costs.append(cost)
        raw_changes.append(path["delta_pips"])
    ordered = sorted(outcomes)
    tail_n = max(1, math.ceil(len(outcomes) * .05))
    return {"sample_size": len(samples), "effective_sample_size": len(samples),
            "expected_net_R": sum(outcomes) / len(outcomes),
            "observed_profit_giveback_frequency": sum(givebacks) / len(givebacks),
            "observed_SL_frequency": sum(stop_hits) / len(stop_hits),
            "probability_of_profit_giveback": None, "probability_SL": None,
            "lower_5_percent_net_R": ordered[max(0, math.ceil(len(ordered) * .05) - 1)],
            "expected_shortfall_95_R": sum(ordered[:tail_n]) / tail_n,
            "worst_adverse_path_net_R": min(worst_paths), "observed_gap_beyond_stop_frequency": sum(gaps) / len(gaps),
            "mean_incremental_cost_pips": sum(costs) / len(costs),
            "mean_observed_delta_pips": sum(raw_changes) / len(raw_changes),
            "mae_pips": [sample["windows"][str(minutes)]["mae_pips"] for sample in samples],
            "mfe_pips": [sample["windows"][str(minutes)]["mfe_pips"] for sample in samples],
            "absolute_move_pips": [abs(sample["windows"][str(minutes)]["delta_pips"]) for sample in samples],
            "cost_sensitivity": [{"additional_slippage_pips": extra,
                                  "expected_net_R": sum(outcomes) / len(outcomes) - extra / risk_pips,
                                  "expected_shortfall_95_R": sum(ordered[:tail_n]) / tail_n - extra / risk_pips} for extra in (0., 1., 3.)],
            "status": "EMPIRICAL_UNVALIDATED", "payoff_scenario": "OBSERVED_SL_OVERSHOOT_TP_CAPPED_NOT_A_FILL"}


def estimate_distribution(samples: Sequence[Mapping], *, now: datetime, bank: str, event_type: str,
                          regime: str, direction: str, current_net_R: float, risk_pips: float,
                          stop_distance_pips: float, tp_distance_pips: float, minimum_samples: int = 40) -> dict:
    decision_at = dt(now)
    parameters = {key: _number(value) for key, value in (("current_net_R", current_net_R), ("risk_pips", risk_pips),
                  ("stop_distance_pips", stop_distance_pips), ("tp_distance_pips", tp_distance_pips))}
    if not decision_at or bank not in {"FED", "ECB"} or event_type not in {"FOMC_DECISION", "ECB_DECISION", "PRESS_CONFERENCE"} or not isinstance(regime, str) or not regime.strip() or len(regime) > 100 or direction not in {"LONG", "SHORT"}:
        raise ValueError("bounded as-of event/regime/direction required")
    if bank == "FED" and event_type == "ECB_DECISION" or bank == "ECB" and event_type == "FOMC_DECISION":
        raise ValueError("event type does not match bank")
    if type(minimum_samples) is not int or not 40 <= minimum_samples <= MAX_SAMPLES:
        raise ValueError("minimum independent event samples cannot be below 40")
    if any(value is None for value in parameters.values()) or not 0 < parameters["risk_pips"] <= 10000 or not 0 <= parameters["stop_distance_pips"] <= 10000 or not 0 <= parameters["tp_distance_pips"] <= 10000:
        raise ValueError("finite position economics required")
    if not isinstance(samples, (list, tuple)) or len(samples) > MAX_SAMPLES:
        raise ValueError("bounded sample list required")
    eligible, rejected = [], []
    for row in samples:
        try:
            if not isinstance(row, Mapping):
                raise ValueError("INVALID_SAMPLE")
            eligible.append(_sample(row, now=decision_at, bank=bank, event_type=event_type, regime=regime, direction=direction))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            rejected.append({"event_id": row.get("event_id") if isinstance(row, Mapping) else None, "reason": str(error)})
    # Reject conflicting identities, and count identical retries once. The
    # choice is independent of whether an episode was profitable.
    unique, seen_ids, seen_urls, conflicts = [], {}, {}, set()
    for row in sorted(eligible, key=lambda item: (item["published_at"], item["event_id"])):
        previous = seen_ids.get(row["event_id"]) or seen_urls.get(row["source_url"])
        if previous:
            if previous["sample_sha256"] != row["sample_sha256"]:
                conflicts.update((previous["event_id"], row["event_id"]))
                rejected.append({"event_id": row["event_id"], "reason": "CONFLICTING_EVENT_IDENTITY"})
            continue
        seen_ids[row["event_id"]] = row
        seen_urls[row["source_url"]] = row
        unique.append(row)
    unique = [row for row in unique if row["event_id"] not in conflicts]
    overlap = set()
    for left, right in zip(unique, unique[1:]):
        if right["published_at"] <= left["end_at"]:
            overlap.update((left["event_id"], right["event_id"]))
    rejected.extend({"event_id": identity, "reason": "OVERLAPPING_EVENT_WINDOWS"} for identity in sorted(overlap))
    unique = [row for row in unique if row["event_id"] not in overlap]
    span = (unique[-1]["published_at"] - unique[0]["published_at"]).total_seconds() / 86400 if len(unique) > 1 else 0.
    qualified = len(unique) >= minimum_samples and span >= MINIMUM_CALENDAR_SPAN_DAYS
    result = {"schema_version": SCHEMA_VERSION, "status": "EMPIRICAL_UNVALIDATED" if qualified else "RISK_MODEL_UNCALIBRATED",
              "risk_model_status": "EMPIRICAL_UNVALIDATED" if qualified else "RISK_MODEL_UNCALIBRATED",
              "as_of": iso(decision_at), "bank": bank, "event_type": event_type, "regime": regime, "direction": direction,
              "minimum_samples": minimum_samples, "effective_sample_size": len(unique), "input_sample_count": len(samples),
              "calendar_span_days": span, "minimum_calendar_span_days": MINIMUM_CALENDAR_SPAN_DAYS,
              "rejected_samples": rejected, "windows": {},
              "expected_net_R": None, "observed_profit_giveback_frequency": None, "observed_SL_frequency": None,
              "probability_of_profit_giveback": None, "probability_SL": None, "expected_shortfall_95_R": None,
              "source_event_ids": [row["event_id"] for row in unique], "sample_set_sha256": digest([row["sample_sha256"] for row in unique]),
              "activation_allowed": False, "authority": "SHADOW_CONTEXT_ONLY",
              "representation": {"bank_type_regime_direction_exact_match": True, "all_six_windows_complete": True,
                                 "overlapping_events_excluded": True, "maximum_quote_gap_seconds": max((row["maximum_quote_gap_seconds"] for row in unique), default=None)},
              "limitations": ["EMPIRICAL_FREQUENCIES_ARE_NOT_VALIDATED_PREDICTIVE_PROBABILITIES",
                              "QUOTE_GAPS_CANNOT_PROVE_INTRABAR_EXTREMES", "NO_BROKER_FILL_GUARANTEE",
                              "REGIME_SHIFT_AND_SELECTION_BIAS_REQUIRE_OOS_REVIEW", "EXPECTED_SHORTFALL_IS_OBSERVED_SAMPLE_TAIL_ONLY"]}
    if qualified:
        result["windows"] = {str(minutes): _window(unique, minutes, **parameters) for minutes in WINDOWS}
        endpoint = result["windows"]["120"]
        for key in ("expected_net_R", "observed_profit_giveback_frequency", "observed_SL_frequency", "expected_shortfall_95_R"):
            result[key] = endpoint[key]
        result["lower_5_percent_net_R"] = endpoint["lower_5_percent_net_R"]
        result["worst_adverse_path_net_R"] = endpoint["worst_adverse_path_net_R"]
    return result
