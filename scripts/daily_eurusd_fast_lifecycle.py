"""Low-latency lifecycle executor for an already-open Daily EUR/USD position.

This module has NO entry/direction authority. It can only persist an exit for the
currently persisted OPEN position using the production hard/dynamic lifecycle
rules inherited from v1.4 R_PACE_V1 (SL/TP, dynamic exits, soft/hard horizon).
"""
from __future__ import annotations

import argparse
import copy
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

DEFAULT_OUTPUT = Path("data/investments/eurusd_daily_spot.json")
DEFAULT_HISTORY = Path("data/investments/eurusd_daily_history.json")


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
    return dict(normalized or position)


def _enrich_trade(position: Mapping[str, Any], trade: Mapping[str, Any]) -> dict[str, Any]:
    enriched = dict(trade)
    for key in ("entry_thesis", "execution_price_engine", "contextual_entry_policy_at_entry"):
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
    trade = v14.evaluate_position(position, bars, observed)
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
        "engine_version": "eurusd-daily-spot-v1.9.0",
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
        engine_version="eurusd-daily-spot-v1.9.0",
        status=_status_for_reason(str(trade.get("exit_reason") or "")),
        decision_mode="WITH",
        metadata=metadata,
    ).validate()
    return output.to_dict()


def fetch_bars(client: Any | None = None) -> list[Bar]:
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
    bars = fetch_bars(client)
    observed_at = bars[-1].timestamp.astimezone(timezone.utc)
    trade = evaluate_open_position(payload, bars, observed_at)
    return {
        "position_open": True,
        "position_id": position.get("trade_id"),
        "observed_at": _iso(observed_at),
        "exit_triggered": trade is not None,
        "exit_reason": None if trade is None else trade.get("exit_reason"),
        "closed_at": None if trade is None else trade.get("closed_at"),
        "exit_price": None if trade is None else trade.get("exit_price"),
    }


def apply_if_triggered(output_path: Path, history_path: Path, client: Any | None = None) -> dict[str, Any]:
    payload = _load(output_path)
    position = _open_position(payload)
    if position is None:
        return {"changed": False, "reason": "no_open_position"}

    bars = fetch_bars(client)
    observed_at = bars[-1].timestamp.astimezone(timezone.utc)
    trade = evaluate_open_position(payload, bars, observed_at)
    if trade is None:
        return {
            "changed": False,
            "reason": "no_exit_trigger",
            "position_id": position.get("trade_id"),
            "observed_at": _iso(observed_at),
        }

    if str(trade.get("trade_id") or "") != str(position.get("trade_id") or ""):
        raise RuntimeError("trade_identity_mismatch")

    history = lifecycle.load_history(history_path)
    history = lifecycle.append_trade(history, trade)
    lifecycle.save_history(history_path, history, observed_at)
    output = close_output(payload, trade, history, datetime.now(timezone.utc))
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "changed": True,
        "position_id": trade.get("trade_id"),
        "exit_reason": trade.get("exit_reason"),
        "closed_at": trade.get("closed_at"),
        "exit_price": trade.get("exit_price"),
        "observed_at": _iso(observed_at),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--probe-json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    history_path = Path(args.history)

    if args.probe_json:
        result = probe(output_path)
        Path(args.probe_json).write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
        print("EURUSD_REALTIME_PROBE", json.dumps(result, sort_keys=True))
        return 0

    if args.apply:
        result = apply_if_triggered(output_path, history_path)
        print("EURUSD_REALTIME_APPLY", json.dumps(result, sort_keys=True))
        return 0

    parser.error("choose --probe-json or --apply")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
