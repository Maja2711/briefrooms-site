"""Low-latency lifecycle executor for an already-open Daily EUR/USD position.

This module has NO entry/direction authority. It can only persist an exit for the
currently persisted OPEN position using the production hard/dynamic lifecycle
rules inherited from v1.4 R_PACE_V1 (SL/TP, dynamic exits, soft/hard horizon).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot as base
import daily_eurusd_spot_v14 as v14
import daily_eurusd_spot_v16 as v16
import daily_eurusd_spot_v18 as v18
import daily_eurusd_macro_event_risk as macro_event_risk
from daily_eurusd_decision_record import ENGINE_VERSION as PRODUCTION_ENGINE_VERSION
from saxo_private_feed import assert_private_state_destination

DEFAULT_OUTPUT = Path("data/investments/eurusd_daily_spot.json")
DEFAULT_HISTORY = Path("data/investments/eurusd_daily_history.json")


def save_cb_audit(
    directory: Path,
    assessment: Mapping[str, Any],
    *,
    position: Mapping[str, Any],
) -> Path:
    """Append one immutable SHADOW observation outside business-state files.

    The same input is idempotent. A conflicting existing receipt raises rather
    than replacing evidence. Private price provenance keeps its existing path
    boundary even for an audit-only artifact.
    """
    assert_private_state_destination(position, directory)
    body = json.dumps(dict(assessment), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False) + "\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    # Use only our content digest in a path, never an external source ID.
    target = Path(directory) / (digest + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except FileExistsError:
        if target.read_text(encoding="utf-8") != body:
            raise ValueError("immutable central-bank fast audit conflict")
    return target


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _open_position(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    position = metadata.get("position") if isinstance(metadata, Mapping) else None
    if not isinstance(position, Mapping):
        return None
    if str(position.get("status") or "").upper() != "OPEN":
        return None
    normalized = v14.normalize_position(dict(position))
    result = dict(normalized or position)
    candidate = metadata.get("candidate_at_refresh") if isinstance(metadata.get("candidate_at_refresh"), Mapping) else metadata.get("candidate")
    belief_macro = metadata.get("belief_macro") if isinstance(metadata.get("belief_macro"), Mapping) else None
    if isinstance(candidate, Mapping):
        result["_management_candidate"] = dict(candidate)
    if isinstance(belief_macro, Mapping):
        result["_belief_macro_context"] = dict(belief_macro)
    return result


def _enrich_trade(position: Mapping[str, Any], trade: Mapping[str, Any]) -> dict[str, Any]:
    enriched = dict(trade)
    for key in ("entry_thesis", "execution_price_engine", "contextual_entry_policy_at_entry", "decision_manifest"):
        value = position.get(key)
        if isinstance(value, Mapping):
            enriched[key] = dict(value)
    try:
        enriched["post_trade_review"] = v16._post_trade_review(enriched)
    except Exception:
        # Review metadata is useful but must never block a deterministic exit.
        pass
    return enriched


def evaluate_open_position(
    payload: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    position = _open_position(payload)
    if position is None or not bars:
        return None
    observed = observed_at or bars[-1].timestamp.astimezone(timezone.utc)
    # v1.9 delegates open-position lifecycle to v18; call that same
    # production chain so SL/TP, R_PACE, Event Intelligence and macro-event
    # invalidation cannot drift between the normal and realtime paths.
    trade = v18._evaluate_position(position, bars, observed)
    if trade is None:
        return None
    return _enrich_trade(position, trade)


def _status_for_reason(reason: str) -> str:
    reason = str(reason or "").upper()
    if reason == "STOP_LOSS":
        return "CLOSED_SL"
    if reason == "TAKE_PROFIT":
        return "CLOSED_TP"
    if reason.startswith("DYNAMIC_") or reason == "SOFT_HORIZON_EXIT":
        return "CLOSED_DYNAMIC"
    if "EVENT" in reason:
        return "CLOSED_EVENT"
    return "CLOSED_TIME"


def close_output(
    previous: Mapping[str, Any],
    trade: Mapping[str, Any],
    history: Mapping[str, Any],
    detected_at: datetime,
) -> dict[str, Any]:
    metadata = copy.deepcopy(previous.get("metadata") or {})
    metadata["position"] = None
    metadata["last_trade"] = dict(trade)
    metadata["learning"] = lifecycle.learning_state(history.get("trades") or [])
    metadata["learning"]["production_direction_learning_applied"] = False
    metadata["fast_lifecycle"] = {
        "schema_version": "daily-eurusd-fast-lifecycle-v1",
        "mode": "OPEN_POSITION_EXIT_ONLY",
        "entry_authority": False,
        "direction_authority": False,
        "exit_authority": True,
        "detected_at": _iso(detected_at),
        "trade_id": trade.get("trade_id"),
        "exit_reason": trade.get("exit_reason"),
        "source": "realtime_open_position_watch",
    }
    runtime_projection = dict(metadata.get("runtime_projection") or {})
    runtime_projection.update({
        "engine_version": PRODUCTION_ENGINE_VERSION,
        "decision_mode": "WITH",
        "historical_trade_rewritten": False,
        "realtime_lifecycle_exit": True,
    })
    metadata["runtime_projection"] = runtime_projection

    output = DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=str(trade.get("closed_at") or _iso(detected_at)),
        direction="FLAT",
        score=float(previous.get("score") or 50.0),
        confidence=float(previous.get("confidence") or 0.0),
        entry=None,
        stop=None,
        target=None,
        horizon=str(previous.get("horizon") or "intraday_to_27h"),
        engine_version=PRODUCTION_ENGINE_VERSION,
        status=_status_for_reason(str(trade.get("exit_reason") or "")),
        decision_mode="WITH",
        metadata=metadata,
    ).validate()
    return output.to_dict()


def fetch_bars(client: Any | None = None) -> list[Bar]:
    # Private D1's exit-only path must use the SAME candle source as its
    # decision cycle. Never mix Yahoo triggers with Saxo paper execution.
    if (client is None and os.environ.get("SAXO_PRIVATE_PRIMARY") == "1"
            and os.environ.get("SAXO_CHART_PRIMARY") == "1"):
        from saxo_private_chart import SaxoPrivateChartClient
        client = SaxoPrivateChartClient()
    client = client or base.YahooChartClient(timeout=8)
    bars = list(client.bars(base.EURUSD, "5d", "1m"))
    if not bars:
        raise RuntimeError("no_eurusd_1m_bars")
    return bars


def probe(output_path: Path, client: Any | None = None) -> dict[str, Any]:
    payload = _load(output_path)
    position = _open_position(payload)
    if position is None:
        return {"position_open": False, "exit_triggered": False}
    assert_private_state_destination(position, output_path)
    bars = fetch_bars(client)
    observed_at = bars[-1].timestamp.astimezone(timezone.utc)
    trade = evaluate_open_position(payload, bars, observed_at)
    cb_risk = macro_event_risk.assess_central_bank_position(position, bars, datetime.now(timezone.utc))
    return {
        "position_open": True,
        "position_id": position.get("trade_id"),
        "observed_at": _iso(observed_at),
        "exit_triggered": trade is not None,
        "exit_reason": None if trade is None else trade.get("exit_reason"),
        "closed_at": None if trade is None else trade.get("closed_at"),
        "exit_price": None if trade is None else trade.get("exit_price"),
        "central_bank_event_risk": cb_risk,
    }


def apply_if_triggered(output_path: Path, history_path: Path, client: Any | None = None) -> dict[str, Any]:
    payload = _load(output_path)
    position = _open_position(payload)
    if position is None:
        return {"changed": False, "reason": "no_open_position"}
    assert_private_state_destination(position, output_path, history_path)

    bars = fetch_bars(client)
    observed_at = bars[-1].timestamp.astimezone(timezone.utc)
    trade = evaluate_open_position(payload, bars, observed_at)
    cb_risk = macro_event_risk.assess_central_bank_position(position, bars, datetime.now(timezone.utc))
    if trade is None:
        return {
            "changed": False,
            "reason": "no_exit_trigger",
            "position_id": position.get("trade_id"),
            "observed_at": _iso(observed_at),
            "central_bank_event_risk": cb_risk,
        }

    if str(trade.get("trade_id") or "") != str(position.get("trade_id") or ""):
        raise RuntimeError("trade_identity_mismatch")

    history = lifecycle.load_history(history_path)
    history = lifecycle.append_trade(history, trade)
    lifecycle.save_history(history_path, history, observed_at)
    output = close_output(payload, trade, history, datetime.now(timezone.utc))
    output["metadata"]["central_bank_event_risk"] = {
        **cb_risk, "existing_lifecycle_exit_has_priority": True,
        "existing_exit_reason": trade.get("exit_reason"),
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "changed": True,
        "position_id": trade.get("trade_id"),
        "exit_reason": trade.get("exit_reason"),
        "closed_at": trade.get("closed_at"),
        "exit_price": trade.get("exit_price"),
        "observed_at": _iso(observed_at),
        "central_bank_event_risk": cb_risk,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--probe-json")
    parser.add_argument("--cb-audit-dir", type=Path,
                        help="Append-only SHADOW receipts; never position/history state")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    history_path = Path(args.history)

    if args.probe_json:
        prior = _open_position(_load(output_path))
        if prior is not None:
            assert_private_state_destination(prior, args.probe_json)
        result = probe(output_path)
        if args.cb_audit_dir and isinstance(result.get("central_bank_event_risk"), Mapping) and prior is not None:
            try:
                save_cb_audit(args.cb_audit_dir, result["central_bank_event_risk"], position=prior)
                result["central_bank_audit_status"] = "RECORDED"
            except (OSError, ValueError) as exc:
                # Audit storage failure cannot suppress a proven old hard exit.
                result["central_bank_audit_status"] = "RECOVERY_REQUIRED"
                result["central_bank_audit_error"] = type(exc).__name__
        Path(args.probe_json).write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
        print("EURUSD_REALTIME_PROBE", json.dumps(result, sort_keys=True))
        return 0

    if args.apply:
        prior = _open_position(_load(output_path))
        result = apply_if_triggered(output_path, history_path)
        if args.cb_audit_dir and isinstance(result.get("central_bank_event_risk"), Mapping) and prior is not None:
            try:
                save_cb_audit(args.cb_audit_dir, result["central_bank_event_risk"], position=prior)
                result["central_bank_audit_status"] = "RECORDED"
            except (OSError, ValueError) as exc:
                result["central_bank_audit_status"] = "RECOVERY_REQUIRED"
                result["central_bank_audit_error"] = type(exc).__name__
        print("EURUSD_REALTIME_APPLY", json.dumps(result, sort_keys=True))
        return 0

    parser.error("choose --probe-json or --apply")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
