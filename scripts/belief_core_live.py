#!/usr/bin/env python3
"""Live shadow-data adapter/orchestrator for BriefRooms Belief Core v2.

No trading, sizing or policy calls exist here. The job only:
- ingests timestamped public-market evidence,
- recomputes hourly World State,
- freezes scheduled forecast sets,
- resolves matured forecasts from deterministic market outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from belief_core import BeliefCore, BeliefDefinition, Evidence, iso_z, parse_time

NY = ZoneInfo("America/New_York")
MODE = "shadow"
TRADE_EXECUTION_ENABLED = False
POLICY_OUTPUT_ENABLED = False
AUTOMATIC_TUNING_ENABLED = False

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 20)
FORECAST_SLOTS = (time(10, 0), time(13, 0), time(16, 0))
SLOT_GRACE_MINUTES = 45

SYMBOLS = ("SPY", "RSP", "IWM", "^VIX", "HYG", "LQD", "TLT", "UUP")
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT = "BriefRooms-BeliefCore/2.0 (+shadow-research)"

BELIEFS: Tuple[BeliefDefinition, ...] = (
    BeliefDefinition(
        "spx.trend.bullish", "SPX/US equity trend is bullish into the target horizon",
        prior_probability=.50, half_life_hours=18, entity="SPX", domain="trend",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="spy_close_above_reference",
    ),
    BeliefDefinition(
        "spx.breadth.healthy", "US equity breadth improves into the target horizon",
        prior_probability=.50, half_life_hours=24, entity="SPX", domain="breadth",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="breadth_ratio_above_reference",
    ),
    BeliefDefinition(
        "spx.volatility.benign", "Equity volatility remains contained into the target horizon",
        prior_probability=.55, half_life_hours=12, entity="SPX", domain="volatility",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="vix_below_dynamic_cap",
    ),
    BeliefDefinition(
        "spx.liquidity.supportive", "Credit/liquidity conditions remain supportive into the target horizon",
        prior_probability=.52, half_life_hours=36, entity="US_RISK", domain="liquidity",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="credit_ratio_above_reference",
    ),
    BeliefDefinition(
        "spx.financial_conditions.supportive", "Rates/USD/credit financial conditions remain supportive into the target horizon",
        prior_probability=.50, half_life_hours=48, entity="US_MACRO", domain="macro",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="financial_conditions_majority_supportive",
    ),
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def strength_from_return(value: float, full_scale: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return clamp(abs(value) / max(1e-9, full_scale), .08, 1.0)


def floor_half_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def in_market_window(local_dt: datetime) -> bool:
    return local_dt.weekday() < 5 and MARKET_OPEN <= local_dt.time().replace(tzinfo=None) <= MARKET_CLOSE


def due_planned_slot(local_dt: datetime, planned: time, already_done: bool) -> bool:
    if already_done or local_dt.weekday() >= 5:
        return False
    planned_dt = datetime.combine(local_dt.date(), planned, tzinfo=NY)
    return planned_dt <= local_dt < planned_dt + timedelta(minutes=SLOT_GRACE_MINUTES)


def next_weekday_close(local_dt: datetime) -> datetime:
    day = local_dt.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(16, 0), tzinfo=NY)


def weekly_target(local_dt: datetime) -> datetime:
    return datetime.combine(local_dt.date() + timedelta(days=7), time(16, 0), tzinfo=NY)


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    close: float


class YahooChartClient:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def bars(self, symbol: str, range_: str = "10d", interval: str = "30m") -> List[Bar]:
        encoded = urllib.parse.quote(symbol, safe="")
        url = f"{YAHOO_BASE}/{encoded}?range={range_}&interval={interval}&includePrePost=false&events=div%2Csplits"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.load(resp)
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            raise RuntimeError(f"Yahoo returned no chart result for {symbol}")
        timestamps = result.get("timestamp") or []
        quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
        closes = quote.get("close") or []
        out: List[Bar] = []
        for epoch, close in zip(timestamps, closes):
            if close is None:
                continue
            out.append(Bar(datetime.fromtimestamp(int(epoch), tz=timezone.utc), float(close)))
        if not out:
            raise RuntimeError(f"Yahoo returned no usable closes for {symbol}")
        return out


class MarketSnapshot:
    def __init__(self, bars: Mapping[str, Sequence[Bar]]) -> None:
        self.bars = {key: list(value) for key, value in bars.items()}

    def latest(self, symbol: str) -> float:
        return self.bars[symbol][-1].close

    def observed_at(self, symbol: str = "SPY") -> datetime:
        return self.bars[symbol][-1].timestamp

    def return_over_bars(self, symbol: str, n: int) -> float:
        rows = self.bars[symbol]
        if len(rows) <= n:
            return 0.0
        old, new = rows[-1 - n].close, rows[-1].close
        return 0.0 if old == 0 else new / old - 1.0

    def ratio_return(self, numerator: str, denominator: str, n: int) -> float:
        a, b = self.bars[numerator], self.bars[denominator]
        m = min(len(a), len(b))
        if m <= n:
            return 0.0
        r0 = a[-1 - n].close / b[-1 - n].close
        r1 = a[-1].close / b[-1].close
        return 0.0 if r0 == 0 else r1 / r0 - 1.0

    def ratio(self, numerator: str, denominator: str) -> float:
        return self.latest(numerator) / self.latest(denominator)

    def is_current_session(self, now: datetime) -> bool:
        return self.observed_at("SPY").astimezone(NY).date() == now.astimezone(NY).date()


def fetch_snapshot(client: YahooChartClient) -> MarketSnapshot:
    return MarketSnapshot({symbol: client.bars(symbol, "10d", "30m") for symbol in SYMBOLS})


def ev(
    belief_id: str,
    cluster: str,
    source_ref: str,
    observed_at: datetime,
    direction: int,
    strength: float,
    evidence_type: str,
    note: str,
    metadata: Optional[Mapping[str, Any]] = None,
    reliability: float = .82,
    source: str = "Yahoo Finance chart",
) -> Evidence:
    evidence_id = stable_id("ev", belief_id, cluster, iso_z(observed_at), direction, round(strength, 6), source_ref)
    return Evidence(
        evidence_id=evidence_id,
        belief_id=belief_id,
        source=source,
        observed_at=iso_z(observed_at),
        direction=1 if direction >= 0 else -1,
        strength=clamp(strength),
        reliability=clamp(reliability),
        independence_cluster=cluster,
        source_type="secondary",
        source_ref=source_ref,
        evidence_type=evidence_type,
        note=note,
        metadata=dict(metadata or {}),
    )


def build_market_evidence(snapshot: MarketSnapshot) -> List[Evidence]:
    observed_at = snapshot.observed_at("SPY")
    out: List[Evidence] = []

    spy_3h = snapshot.return_over_bars("SPY", 6)
    spy_1d = snapshot.return_over_bars("SPY", 13)
    spy_5d = snapshot.return_over_bars("SPY", 65)
    trend_score = .45 * spy_3h / .008 + .35 * spy_1d / .015 + .20 * spy_5d / .04
    out.append(ev(
        "spx.trend.bullish", "market:SPY:trend", f"yahoo:SPY:{iso_z(observed_at)}", observed_at,
        1 if trend_score >= 0 else -1, clamp(abs(trend_score), .10, 1.0), "price_trend",
        f"SPY momentum: 3h={spy_3h:.4%}, 1d={spy_1d:.4%}, 5d={spy_5d:.4%}",
    ))

    rsp_rel = snapshot.ratio_return("RSP", "SPY", 13)
    iwm_rel = snapshot.ratio_return("IWM", "SPY", 13)
    out.append(ev(
        "spx.breadth.healthy", "market:RSP-SPY:breadth", f"yahoo:RSP/SPY:{iso_z(observed_at)}", observed_at,
        1 if rsp_rel >= 0 else -1, strength_from_return(rsp_rel, .012), "breadth",
        f"RSP/SPY 1d relative return={rsp_rel:.4%}",
    ))
    out.append(ev(
        "spx.breadth.healthy", "market:IWM-SPY:breadth", f"yahoo:IWM/SPY:{iso_z(observed_at)}", observed_at,
        1 if iwm_rel >= 0 else -1, strength_from_return(iwm_rel, .018), "breadth",
        f"IWM/SPY 1d relative return={iwm_rel:.4%}",
    ))

    vix = snapshot.latest("^VIX")
    vix_1d = snapshot.return_over_bars("^VIX", 13)
    benign_level = (20.0 - vix) / 8.0
    benign_change = -vix_1d / .15
    vol_score = .65 * benign_level + .35 * benign_change
    out.append(ev(
        "spx.volatility.benign", "market:VIX:vol", f"yahoo:^VIX:{iso_z(observed_at)}", observed_at,
        1 if vol_score >= 0 else -1, clamp(abs(vol_score), .10, 1.0), "volatility",
        f"VIX={vix:.2f}, 1d change={vix_1d:.2%}",
    ))

    credit_rel = snapshot.ratio_return("HYG", "LQD", 13)
    hyg_1d = snapshot.return_over_bars("HYG", 13)
    out.append(ev(
        "spx.liquidity.supportive", "market:HYG-LQD:credit", f"yahoo:HYG/LQD:{iso_z(observed_at)}", observed_at,
        1 if credit_rel >= 0 else -1, strength_from_return(credit_rel, .008), "credit_liquidity",
        f"HYG/LQD 1d ratio return={credit_rel:.4%}",
    ))
    out.append(ev(
        "spx.liquidity.supportive", "market:HYG:return", f"yahoo:HYG:{iso_z(observed_at)}", observed_at,
        1 if hyg_1d >= 0 else -1, strength_from_return(hyg_1d, .012), "credit_liquidity",
        f"HYG 1d return={hyg_1d:.4%}",
    ))

    tlt_1d = snapshot.return_over_bars("TLT", 13)
    uup_1d = snapshot.return_over_bars("UUP", 13)
    out.append(ev(
        "spx.financial_conditions.supportive", "market:TLT:rates", f"yahoo:TLT:{iso_z(observed_at)}", observed_at,
        1 if tlt_1d >= 0 else -1, strength_from_return(tlt_1d, .015), "rates",
        f"TLT 1d return={tlt_1d:.4%}",
    ))
    out.append(ev(
        "spx.financial_conditions.supportive", "market:UUP:usd", f"yahoo:UUP:{iso_z(observed_at)}", observed_at,
        1 if uup_1d <= 0 else -1, strength_from_return(uup_1d, .008), "usd",
        f"UUP 1d return={uup_1d:.4%}; weaker USD treated as supportive",
    ))
    out.append(ev(
        "spx.financial_conditions.supportive", "market:HYG:macro-credit", f"yahoo:HYG-macro:{iso_z(observed_at)}", observed_at,
        1 if hyg_1d >= 0 else -1, strength_from_return(hyg_1d, .012), "credit_liquidity",
        f"HYG 1d return={hyg_1d:.4%}",
    ))
    return out


def classify_regime(snapshot: MarketSnapshot) -> str:
    vix = snapshot.latest("^VIX")
    spy = snapshot.return_over_bars("SPY", 13)
    credit = snapshot.ratio_return("HYG", "LQD", 13)
    if vix >= 28:
        return "high_vol"
    if spy <= -.012 and credit < 0:
        return "risk_off"
    if vix < 18 and spy > 0 and credit >= 0:
        return "risk_on"
    return "neutral"


def outcome_spec(belief_id: str, snapshot: MarketSnapshot) -> Dict[str, Any]:
    if belief_id == "spx.trend.bullish":
        return {"kind": "price_above", "symbol": "SPY", "reference": snapshot.latest("SPY")}
    if belief_id == "spx.breadth.healthy":
        return {"kind": "ratio_above", "numerator": "RSP", "denominator": "SPY", "reference": snapshot.ratio("RSP", "SPY")}
    if belief_id == "spx.volatility.benign":
        reference = snapshot.latest("^VIX")
        return {"kind": "value_below", "symbol": "^VIX", "reference": reference, "threshold": max(20.0, reference * 1.10)}
    if belief_id == "spx.liquidity.supportive":
        return {"kind": "ratio_above", "numerator": "HYG", "denominator": "LQD", "reference": snapshot.ratio("HYG", "LQD")}
    if belief_id == "spx.financial_conditions.supportive":
        return {"kind": "majority_supportive", "reference": {
            "TLT": snapshot.latest("TLT"), "HYG": snapshot.latest("HYG"), "UUP": snapshot.latest("UUP")}}
    raise KeyError(belief_id)


def evaluate_spec(spec: Mapping[str, Any], values: Mapping[str, float]) -> bool:
    kind = spec["kind"]
    if kind == "price_above":
        return float(values[str(spec["symbol"])]) > float(spec["reference"])
    if kind == "ratio_above":
        ratio = float(values[str(spec["numerator"])]) / float(values[str(spec["denominator"])])
        return ratio > float(spec["reference"])
    if kind == "value_below":
        return float(values[str(spec["symbol"])]) <= float(spec["threshold"])
    if kind == "majority_supportive":
        reference = spec["reference"]
        votes = [
            float(values["TLT"]) >= float(reference["TLT"]),
            float(values["HYG"]) >= float(reference["HYG"]),
            float(values["UUP"]) <= float(reference["UUP"]),
        ]
        return sum(bool(value) for value in votes) >= 2
    raise ValueError(f"unknown outcome spec: {kind}")


def required_symbols(spec: Mapping[str, Any]) -> List[str]:
    kind = spec["kind"]
    if kind in {"price_above", "value_below"}:
        return [str(spec["symbol"])]
    if kind == "ratio_above":
        return [str(spec["numerator"]), str(spec["denominator"])]
    if kind == "majority_supportive":
        return ["TLT", "HYG", "UUP"]
    raise ValueError(str(kind))


def target_values(
    client: YahooChartClient,
    spec: Mapping[str, Any],
    target_at: datetime,
    now: datetime,
    live_snapshot: Optional[MarketSnapshot],
) -> Optional[Dict[str, float]]:
    target_local = target_at.astimezone(NY)
    now_local = now.astimezone(NY)
    symbols = required_symbols(spec)
    if live_snapshot is not None and target_local.date() == now_local.date() and live_snapshot.is_current_session(now):
        return {symbol: live_snapshot.latest(symbol) for symbol in symbols}

    values: Dict[str, float] = {}
    for symbol in symbols:
        rows = client.bars(symbol, "3mo", "1d")
        candidates = [bar for bar in rows if bar.timestamp.astimezone(NY).date() >= target_local.date()]
        if not candidates:
            return None
        values[symbol] = candidates[0].close
    return values


def load_scheduler(state_dir: Path) -> Dict[str, Any]:
    path = state_dir / "scheduler.json"
    if not path.exists():
        return {"schema_version": 1, "completed_slots": {}, "gaps": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_scheduler(state_dir: Path, payload: Mapping[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "scheduler.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_world_state(state_dir: Path, core: BeliefCore, when: datetime, regime: str) -> None:
    path = state_dir / "world_state_history.jsonl"
    row = {
        "timestamp": iso_z(when),
        "regime": regime,
        "mode": MODE,
        "beliefs": {
            key: {
                "probability": value.probability,
                "confidence": value.confidence,
                "audit_status": value.audit_status,
                "domain": value.domain,
            }
            for key, value in sorted(core.beliefs.items())
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def freeze_set(
    core: BeliefCore,
    snapshot: MarketSnapshot,
    when: datetime,
    target: datetime,
    consumer: str,
    slot_key: str,
    regime: str,
) -> int:
    count = 0
    for belief_id in sorted(core.beliefs):
        forecast_id = stable_id("forecast", consumer, slot_key, belief_id)
        if forecast_id in core.forecasts:
            continue
        metadata = {
            "consumer": consumer,
            "slot_key": slot_key,
            "market_observed_at": iso_z(snapshot.observed_at("SPY")),
            "outcome_spec": outcome_spec(belief_id, snapshot),
            "shadow_only": True,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
        }
        core.capture_forecast(
            belief_id,
            as_of=when,
            target_at=target,
            regime=regime,
            forecast_id=forecast_id,
            metadata=metadata,
        )
        count += 1
    return count


def verify_due(
    core: BeliefCore,
    client: YahooChartClient,
    now: datetime,
    live_snapshot: Optional[MarketSnapshot],
) -> int:
    verified_ids = {value.forecast_id for value in core.verifications.values() if value.forecast_id}
    count = 0
    for forecast in sorted(core.forecasts.values(), key=lambda item: item.target_at):
        if forecast.forecast_id in verified_ids or parse_time(forecast.target_at) > now:
            continue
        spec = dict(forecast.metadata.get("outcome_spec") or {})
        if not spec:
            continue
        values = target_values(client, spec, parse_time(forecast.target_at), now, live_snapshot)
        if values is None:
            continue
        outcome = evaluate_spec(spec, values)
        outcome_ref = "yahoo:" + ",".join(required_symbols(spec)) + ":target=" + forecast.target_at
        core.verify_forecast(
            forecast.forecast_id,
            outcome,
            verified_at=now,
            outcome_source="Yahoo Finance chart",
            outcome_ref=outcome_ref,
            note="Automatic deterministic shadow verification",
        )
        count += 1
    return count


def run_cycle(state_dir: Path, now: datetime, client: YahooChartClient) -> Dict[str, Any]:
    if TRADE_EXECUTION_ENABLED or POLICY_OUTPUT_ENABLED or AUTOMATIC_TUNING_ENABLED:
        raise RuntimeError("Belief Core live adapter safety invariant violated")
    state_dir.mkdir(parents=True, exist_ok=True)
    core = BeliefCore(state_dir)
    core.register_beliefs(BELIEFS)
    scheduler = load_scheduler(state_dir)
    completed = scheduler.setdefault("completed_slots", {})
    local = now.astimezone(NY)
    snapshot: Optional[MarketSnapshot] = None
    evidence_count = world_count = forecast_count = wes_count = 0

    if in_market_window(local):
        snapshot = fetch_snapshot(client)
        if snapshot.is_current_session(now):
            evidence = build_market_evidence(snapshot)
            core.ingest(evidence)
            core.save()
            evidence_count = len(evidence)
            regime = classify_regime(snapshot)

            half_slot = floor_half_hour(local)
            hour_key = f"world:{local.date().isoformat()}:{half_slot.hour:02d}"
            if half_slot.minute == 0 and hour_key not in completed:
                core.recompute(now)
                append_world_state(state_dir, core, now, regime)
                completed[hour_key] = iso_z(now)
                world_count = 1

            for planned in FORECAST_SLOTS:
                key = f"shared:{local.date().isoformat()}:{planned.hour:02d}{planned.minute:02d}"
                if due_planned_slot(local, planned, key in completed):
                    core.recompute(now)
                    if planned.hour < 16:
                        target = datetime.combine(local.date(), time(16, 0), tzinfo=NY)
                    else:
                        target = next_weekday_close(local)
                    forecast_count += freeze_set(core, snapshot, now, target, "BRACE+BRACE-SPX", key, regime)
                    completed[key] = iso_z(now)

            wes_key = f"wes:{local.date().isoformat()}:1600"
            if local.weekday() == 4 and due_planned_slot(local, time(16, 0), wes_key in completed):
                core.recompute(now)
                wes_count += freeze_set(core, snapshot, now, weekly_target(local), "WES", wes_key, regime)
                completed[wes_key] = iso_z(now)
        else:
            scheduler.setdefault("gaps", []).append({"timestamp": iso_z(now), "reason": "no_current_us_session_bar"})

    verified = verify_due(core, client, now, snapshot)
    scheduler["last_run_at"] = iso_z(now)
    scheduler["last_status"] = {
        "evidence_ingested": evidence_count,
        "world_state_snapshots": world_count,
        "shared_forecasts_frozen": forecast_count,
        "wes_forecasts_frozen": wes_count,
        "forecasts_verified": verified,
        "mode": MODE,
    }
    scheduler["gaps"] = scheduler.get("gaps", [])[-100:]
    save_scheduler(state_dir, scheduler)
    core.save()
    core.write_dashboard(now)
    return scheduler["last_status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BriefRooms Belief Core live shadow collection cycle")
    parser.add_argument("--state-dir", default=os.environ.get("BELIEF_CORE_STATE_DIR", ".belief_runtime/core"))
    parser.add_argument("--now", help="ISO timestamp override for deterministic testing/manual replay")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    status = run_cycle(Path(args.state_dir), now, YahooChartClient())
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
