"""TR-03 projection of the shared Fed/ECB facts; no execution authority.

The Daily stop, target, entry and original admission manifest are only read.
OHLC marks are explicitly uncertain paper proxies, never invented BID/ASK.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
WIRE_PATH = ROOT / "data/investments/eurusd_central_bank_wire.json"


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def evaluate_daily(
    position: Mapping[str, Any] | None,
    bars: Sequence[Any],
    observed_at: datetime,
    *,
    wire: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    calendar_events: Sequence[Mapping[str, Any]] = (),
    quote: Mapping[str, Any] | None = None,
    historical_samples: Sequence[Mapping[str, Any]] | None = None,
    regime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate current facts without changing a position or creating a fill."""
    if position and str(position.get('instrument_id') or 'eurusd').lower() != 'eurusd':
        raise ValueError('EURUSD-only adapter')
    from eurusd_central_bank_event_risk import assess_pre_event, load_policy
    from eurusd_central_bank_wire import load_wire, runtime_commit

    now = observed_at.astimezone(timezone.utc)
    facts = wire if wire is not None else load_wire(WIRE_PATH, now=now)
    risk_policy = policy if policy is not None else load_policy()
    normalized: dict[str, Any] | None = None
    if position and str(position.get("status") or "OPEN").upper() == "OPEN":
        instrument = str(position.get("instrument_id") or "eurusd").lower()
        normalized = {
            "instrument_id": instrument,
            "position_id": position.get("trade_id") or position.get("position_id"),
            "direction": position.get("direction"),
            "entry_price": position.get("entry"),
            "stop_loss": position.get("stop"),
            "take_profit": position.get("target"),
            "initial_stop_loss": (position.get("initial_stop") or
                ((position.get("decision_manifest") or {}).get("execution_geometry") or {}).get("stop") or
                position.get("stop")),
            "opened_at": position.get("opened_at"),
        }
        opened = _time(position.get("opened_at"))
        past = [bar for bar in bars if opened is not None and opened <= bar.timestamp <= now]
        try:
            entry, stop = float(normalized["entry_price"]), float(normalized["initial_stop_loss"])
            sign = 1.0 if normalized["direction"] == "LONG" else -1.0
            extremes = [float((bar.high if sign > 0 else bar.low) or bar.close) for bar in past]
            if extremes and abs(entry - stop) > 0:
                normalized["mfe_R"] = max(sign * (price - entry) / abs(entry - stop) for price in extremes)
        except (TypeError, ValueError):
            pass
    past_bars = sorted((bar for bar in bars if bar.timestamp <= now), key=lambda bar: bar.timestamp)
    current_quote = dict(quote or {})
    if not current_quote and past_bars:
        current_quote = {"mid": float(past_bars[-1].close),
            "observed_at": past_bars[-1].timestamp.isoformat(),
            "source": "Daily canonical EURUSD OHLC mark", "verified": False,
            "paper_proxy_uncertain": True}
        if position:
            # Reuse the frozen paper accounting basis only when it is present;
            # it is a cost model, not a claim of a current executable spread.
            from daily_eurusd_lifecycle import _synthetic_execution_config
            cost = _synthetic_execution_config(position)
            if cost is not None:
                current_quote["cost_pips"] = cost["half_pips"]
    from eurusd_cb_event_distribution import load_model
    samples = historical_samples if historical_samples is not None else load_model(ROOT / 'data/investments/eurusd_cb_event_distribution.json')
    decision = assess_pre_event(normalized, current_quote, facts,
        calendar_events=calendar_events, policy=risk_policy, now=now, scope="EURUSD_DAILY",
        historical_samples=samples, regime=regime)
    # Admission proof is compared before/after JSON persistence. Freeze the
    # same JSON types in memory so tuples cannot masquerade as a later rewrite.
    result = json.loads(json.dumps(decision.to_dict(), allow_nan=False))
    result.setdefault("audit", {})["consumer_sha"] = runtime_commit()
    result["wire_snapshot_sha256"] = (result.get("audit") or {}).get("snapshot_sha256")
    result["decision_id"] = result.get("immutable_id")
    result.update({"engine": "EURUSD_DAILY", "lifecycle": "v1.9.1",
        "direction_authority": False, "execution_authority": False,
        "executed_at": None, "execution_price_source": None,
        "stop_changed": False, "history_rewritten": False})
    return result


def safe_evaluate_daily(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """A feed/parser failure must never prevent the old hard-exit evaluator."""
    try:
        return evaluate_daily(*args, **kwargs)
    except Exception as exc:
        try:
            from eurusd_central_bank_event_risk import load_policy
            enforced = (kwargs.get("policy") or load_policy()).get("entry_guard_enabled") is True
        except Exception:
            enforced = True
        return {"scope": "EURUSD_DAILY", "action": "NO_ACTION_UNVERIFIED",
            "mode": "SHADOW", "wire_health": "UNAVAILABLE",
            "risk_protection_status": "PROTECTION_PENDING", "quote_verified": False,
            "execution_authority": False, "executed_at": None,
            "block_new_entry": True, "entry_guard_enforced": enforced,
            "reasons": ["CENTRAL_BANK_ASSESSMENT_UNAVAILABLE", type(exc).__name__],
            "alerts": ["RECOVERY REQUIRED"]}
