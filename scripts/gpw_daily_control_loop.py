#!/usr/bin/env python3
"""Self-healing control loop for the Polish GPW 1-2 session paper outlook.

The core ranking remains in ``gpw_daily_pick.py``.  This wrapper closes two
operational gaps:

* scheduled GitHub Actions may start late, so publication must be driven by the
  current Warsaw state instead of a narrow cron-arrival window;
* resolved paper decisions must feed a bounded learning overlay before the next
  forecast, without allowing one trade to rewrite the strategy.

Only the Polish GPW daily module uses this loop.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Callable

try:
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:  # GitHub Actions PYTHONPATH=scripts
    import gpw_daily_pick as gpw


ROOT = Path(__file__).resolve().parents[1]
LEARNING_PATH = ROOT / "data/investments/gpw_daily_pick_learning.json"
EARLIEST_GENERATION = clock_time(6, 45)
DEFAULT_RECENT_WINDOW = 20
DEFAULT_PRIOR_STRENGTH = 10.0
DEFAULT_MAX_SCORE_ADJUSTMENT = 12.0
DEFAULT_ATTEMPTS = 2

_ACTIVE_LEARNING_CONFIG: dict[str, Any] = {}
_ACTIVE_LEARNING_CONTEXT: dict[str, Any] = {}
_ORIGINAL_REQUEST_JSON_COMPLETION = gpw.request_json_completion


def _resolved_activated(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in history
        if row.get("decision") == "TRANSAKCJA"
        and row.get("outcome", {}).get("status") == "RESOLVED"
        and row.get("outcome", {}).get("activated") is True
    ]
    return sorted(rows, key=lambda row: str(row.get("date") or ""))


def _mean_r(rows: list[dict[str, Any]]) -> float | None:
    values = [float(row.get("outcome", {}).get("r_multiple", 0.0)) for row in rows]
    return statistics.fmean(values) if values else None


def _shrink(value: float | None, sample: int, prior_strength: float) -> float:
    if value is None or sample <= 0:
        return 0.0
    return float(value) * sample / (sample + max(prior_strength, 0.0))


def adaptive_history_expectancy_score(
    history: list[dict[str, Any]], sector: str, minimum_sample: int
) -> tuple[float, int]:
    """Return a bounded, shrinkage-based historical score.

    Adaptation starts only after the configured *global* sample is available.
    The score remains only one 10% component of the core composite, so history
    can refine a decision but cannot dominate price, liquidity, risk or news.
    """
    resolved = _resolved_activated(history)
    sample = len(resolved)
    if sample < minimum_sample:
        return 50.0, sample

    learning = _ACTIVE_LEARNING_CONFIG
    recent_window = int(learning.get("recent_window", DEFAULT_RECENT_WINDOW))
    prior_strength = float(learning.get("prior_strength", DEFAULT_PRIOR_STRENGTH))
    max_adjustment = float(
        learning.get("max_historical_score_adjustment", DEFAULT_MAX_SCORE_ADJUSTMENT)
    )

    recent = resolved[-recent_window:]
    sector_rows = [row for row in resolved if row.get("selection", {}).get("sector") == sector]

    components: list[tuple[float, float]] = []
    components.append((_shrink(_mean_r(resolved), len(resolved), prior_strength), 0.45))
    components.append((_shrink(_mean_r(recent), len(recent), prior_strength), 0.35))
    if sector_rows:
        components.append(
            (_shrink(_mean_r(sector_rows), len(sector_rows), prior_strength), 0.20)
        )

    total_weight = sum(weight for _, weight in components) or 1.0
    expected_r = sum(value * weight for value, weight in components) / total_weight
    adjustment = max(-max_adjustment, min(max_adjustment, expected_r * 18.0))
    return round(gpw.clamp(50.0 + adjustment), 2), sample


def _last_lesson(resolved: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not resolved:
        return None
    row = resolved[-1]
    selection = row.get("selection") or {}
    outcome = row.get("outcome") or {}
    symbol = str(selection.get("ticker") or selection.get("symbol") or "—")
    r_value = float(outcome.get("r_multiple", 0.0))
    result = float(outcome.get("return_percent", 0.0))
    reason = str(outcome.get("exit_reason") or "koniec_horyzontu")

    if reason == "stop":
        lesson = (
            f"{symbol}: pozycja zakończyła się stopem ({r_value:.2f}R). "
            "Pętla obniża ocenę podobnych układów dopiero po zebraniu wymaganej próby; "
            "pojedyncza strata nie zmienia wag strategii."
        )
    elif reason == "target":
        lesson = (
            f"{symbol}: cel został osiągnięty ({r_value:.2f}R). "
            "Pętla zachowuje ten wynik jako dodatni dowód, ale nie zwiększa wag po jednej obserwacji."
        )
    elif r_value > 0:
        lesson = (
            f"{symbol}: pozycja zakończyła horyzont dodatnio ({r_value:.2f}R). "
            "Wynik zasila oczekiwaną skuteczność podobnych układów po shrinkage."
        )
    else:
        lesson = (
            f"{symbol}: pozycja zakończyła horyzont słabo ({r_value:.2f}R). "
            "Wynik obniża oczekiwaną skuteczność podobnych układów dopiero w ramach zbiorczej próby."
        )
    return {
        "date": row.get("date"),
        "symbol": symbol,
        "sector": selection.get("sector"),
        "return_percent": round(result, 3),
        "r_multiple": round(r_value, 3),
        "exit_reason": reason,
        "lesson": lesson,
    }


def build_learning_snapshot(
    history: list[dict[str, Any]], config: dict[str, Any], *, now: datetime
) -> dict[str, Any]:
    learning = config.get("learning") or {}
    minimum = int(learning.get("minimum_resolved_trades_for_adaptation", 8))
    recent_window = int(learning.get("recent_window", DEFAULT_RECENT_WINDOW))
    prior_strength = float(learning.get("prior_strength", DEFAULT_PRIOR_STRENGTH))
    resolved = _resolved_activated(history)
    recent = resolved[-recent_window:]

    by_sector: dict[str, list[dict[str, Any]]] = {}
    for row in resolved:
        sector = str(row.get("selection", {}).get("sector") or "inne")
        by_sector.setdefault(sector, []).append(row)

    sector_rows = []
    for sector, rows in sorted(by_sector.items()):
        sector_rows.append(
            {
                "sector": sector,
                "sample": len(rows),
                "average_r": round(_mean_r(rows) or 0.0, 3),
                "shrunk_r": round(
                    _shrink(_mean_r(rows), len(rows), prior_strength), 3
                ),
            }
        )

    stops = sum(1 for row in resolved if row.get("outcome", {}).get("exit_reason") == "stop")
    targets = sum(1 for row in resolved if row.get("outcome", {}).get("exit_reason") == "target")
    wins = sum(1 for row in resolved if float(row.get("outcome", {}).get("return_percent", 0.0)) > 0)

    return {
        "schema_version": "gpw-daily-learning-v1",
        "updated_at": now.isoformat(timespec="seconds"),
        "resolved_trades": len(resolved),
        "adaptation_active": len(resolved) >= minimum,
        "minimum_sample": minimum,
        "method": "bayesian_shrinkage_historical_overlay_v1",
        "automatic_weight_changes": False,
        "global_average_r": round(_mean_r(resolved) or 0.0, 3) if resolved else None,
        "recent_average_r": round(_mean_r(recent) or 0.0, 3) if recent else None,
        "recent_window": recent_window,
        "win_rate": round(wins / len(resolved), 4) if resolved else None,
        "stop_rate": round(stops / len(resolved), 4) if resolved else None,
        "target_rate": round(targets / len(resolved), 4) if resolved else None,
        "sector_expectancy": sector_rows,
        "last_lesson": _last_lesson(resolved),
        "guardrails": {
            "weights_frozen": True,
            "historical_component_weight_percent": float(config.get("weights", {}).get("historical_expectancy", 10)),
            "max_historical_score_adjustment": float(
                learning.get("max_historical_score_adjustment", DEFAULT_MAX_SCORE_ADJUSTMENT)
            ),
            "single_trade_weight_mutation": False,
        },
    }


def write_learning_snapshot(snapshot: dict[str, Any]) -> None:
    gpw.atomic_json(LEARNING_PATH, snapshot)


def _public_learning(snapshot: dict[str, Any]) -> dict[str, Any]:
    last = snapshot.get("last_lesson") or None
    return {
        "method": snapshot.get("method"),
        "resolved_trades": snapshot.get("resolved_trades", 0),
        "adaptation_active": bool(snapshot.get("adaptation_active")),
        "minimum_sample": snapshot.get("minimum_sample"),
        "recent_average_r": snapshot.get("recent_average_r"),
        "last_lesson": last,
        "weights_frozen": True,
    }


def _install_learning_overlay(config: dict[str, Any], snapshot: dict[str, Any]) -> None:
    global _ACTIVE_LEARNING_CONFIG, _ACTIVE_LEARNING_CONTEXT
    _ACTIVE_LEARNING_CONFIG = dict(config.get("learning") or {})
    _ACTIVE_LEARNING_CONTEXT = _public_learning(snapshot)
    gpw.history_expectancy_score = adaptive_history_expectancy_score

    def learning_aware_request(**kwargs):
        messages = copy.deepcopy(kwargs.get("messages") or [])
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            try:
                payload = json.loads(str(message.get("content") or ""))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            task = str(payload.get("task") or "")
            if "GPW" not in task:
                continue
            payload["learning_context"] = {
                **_ACTIVE_LEARNING_CONTEXT,
                "instruction": (
                    "To jest wyłącznie kontekst z rozliczonych paper decyzji. "
                    "Nie traktuj pojedynczej transakcji jako dowodu i nie omijaj bramek źródłowych."
                ),
            }
            message["content"] = json.dumps(payload, ensure_ascii=False)
        forwarded = dict(kwargs)
        forwarded["messages"] = messages
        return _ORIGINAL_REQUEST_JSON_COMPLETION(**forwarded)

    gpw.request_json_completion = learning_aware_request


def prefetch_market(config: dict[str, Any]) -> dict[str, list[gpw.Bar]]:
    """Fetch the 40-name universe concurrently to keep the pre-open loop fast."""
    symbols = [str(row["symbol"]) for row in config["universe"]]
    result: dict[str, list[gpw.Bar]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        futures = {pool.submit(gpw.fetch_yahoo_bars, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result[symbol] = future.result()
            except Exception:
                continue
    return result


def _market_fetcher_from_cache(cache: dict[str, list[gpw.Bar]]) -> Callable[[str], list[gpw.Bar]]:
    def fetch(symbol: str) -> list[gpw.Bar]:
        bars = cache.get(symbol)
        if not bars:
            raise gpw.PublicationError(f"Brak danych rynkowych {symbol} w prefetch cache.")
        return bars

    return fetch


def _refresh_learning(config: dict[str, Any], now: datetime) -> tuple[dict[str, Any], str | None]:
    settle_error: str | None = None
    try:
        gpw.settle_history()
    except Exception as exc:  # settlement must not block the next morning publication
        settle_error = type(exc).__name__
    history = gpw.all_history()
    snapshot = build_learning_snapshot(history, config, now=now)
    if settle_error:
        snapshot["settlement_warning"] = settle_error
    write_learning_snapshot(snapshot)
    _install_learning_overlay(config, snapshot)
    return snapshot, settle_error


def _enrich_payload(payload: dict[str, Any], snapshot: dict[str, Any], *, attempts: int) -> dict[str, Any]:
    enriched = copy.deepcopy(payload)
    enriched["learning"] = _public_learning(snapshot)
    enriched.setdefault("data_quality", {})["control_loop_attempts"] = attempts
    enriched["methodology"]["learning_overlay"] = {
        "method": snapshot.get("method"),
        "active": bool(snapshot.get("adaptation_active")),
        "minimum_sample": snapshot.get("minimum_sample"),
        "weights_frozen": True,
    }
    return enriched


def _publish_current_failure(
    now: datetime, config: dict[str, Any], snapshot: dict[str, Any], stage: str
) -> dict[str, Any]:
    payload = gpw.failure_payload(
        now,
        config,
        "Poranny wybór nie został poprawnie opublikowany przed 08:30; na dziś nie otwieramy transakcji.",
        stage,
    )
    payload["reason"] = "Brak dzisiaj wyboru — poranny sygnał nie został potwierdzony przed 08:30."
    payload["locked"] = True
    payload = _enrich_payload(payload, snapshot, attempts=0)
    gpw.publish(payload)
    return payload


def control_once(
    *,
    now: datetime | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    market_prefetcher: Callable[[dict[str, Any]], dict[str, list[gpw.Bar]]] = prefetch_market,
) -> dict[str, Any]:
    now = now or gpw.now_warsaw()
    config = gpw.load_config()
    snapshot, _ = _refresh_learning(config, now)
    current = gpw.load_json(gpw.PUBLIC_PATH)

    if not gpw.is_session_day(now.date(), config):
        if isinstance(current, dict) and current.get("date") == now.date().isoformat():
            return current
        payload = gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Dziś nie ma sesji GPW.")
        payload["locked"] = True
        payload["data_quality"] = {"status": "not_applicable", "complete_ratio": 1.0}
        payload = _enrich_payload(payload, snapshot, attempts=0)
        gpw.publish(payload)
        return payload

    cutoff = gpw.cutoff_for(now.date(), config)
    if now >= cutoff:
        if isinstance(current, dict) and current.get("date") == now.date().isoformat():
            gpw.validate_payload(current, require_today=True, now=now)
            return current
        return _publish_current_failure(now, config, snapshot, "missed_cutoff_guardian")

    if now.timetz().replace(tzinfo=None) < EARLIEST_GENERATION:
        return current if isinstance(current, dict) else {
            "date": now.date().isoformat(),
            "decision": "WAITING_PREOPEN_WINDOW",
        }

    if (
        isinstance(current, dict)
        and current.get("date") == now.date().isoformat()
        and current.get("decision") != "AWARIA_DANYCH"
    ):
        gpw.validate_payload(current, require_today=True, now=now)
        return current

    last_payload: dict[str, Any] | None = None
    for attempt in range(1, max(1, attempts) + 1):
        attempt_now = gpw.now_warsaw()
        if attempt_now >= cutoff:
            break
        cache = market_prefetcher(config)
        payload = gpw.generate(
            now=attempt_now,
            market_fetcher=_market_fetcher_from_cache(cache),
        )
        payload = _enrich_payload(payload, snapshot, attempts=attempt)
        last_payload = payload
        if payload.get("decision") != "AWARIA_DANYCH":
            gpw.publish(payload)
            gpw.validate_payload(gpw.load_json(gpw.PUBLIC_PATH), require_today=True, now=attempt_now)
            return payload
        if attempt < attempts and gpw.now_warsaw() + timedelta(seconds=20) < cutoff:
            time.sleep(12)

    if last_payload is not None and gpw.now_warsaw() < cutoff:
        gpw.publish(last_payload)
        return last_payload
    return _publish_current_failure(gpw.now_warsaw(), config, snapshot, "cutoff_after_retries")


def validate_control_state(*, now: datetime | None = None) -> None:
    now = now or gpw.now_warsaw()
    payload = gpw.load_json(gpw.PUBLIC_PATH)
    gpw.validate_payload(payload, require_today=True, now=now)
    learning = gpw.load_json(LEARNING_PATH)
    if not isinstance(learning, dict) or learning.get("schema_version") != "gpw-daily-learning-v1":
        raise gpw.PublicationError("Brak poprawnego stanu uczenia GPW.")
    if learning.get("automatic_weight_changes") is not False:
        raise gpw.PublicationError("Pętla GPW nie może samodzielnie mutować wag strategii.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "settle", "validate"), default="auto")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    args = parser.parse_args()

    if args.mode == "settle":
        config = gpw.load_config()
        now = gpw.now_warsaw()
        try:
            changed = gpw.settle_history()
        finally:
            snapshot = build_learning_snapshot(gpw.all_history(), config, now=now)
            write_learning_snapshot(snapshot)
        print(f"GPW settle: changed={changed}; resolved={snapshot['resolved_trades']}")
        return 0

    if args.mode == "validate":
        validate_control_state()
        print("GPW control loop: current publication and learning state are valid.")
        return 0

    payload = control_once(attempts=max(1, min(args.attempts, 3)))
    print(
        "GPW control loop: "
        f"date={payload.get('date')} decision={payload.get('decision')} "
        f"generated_at={payload.get('generated_at', 'n/a')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
