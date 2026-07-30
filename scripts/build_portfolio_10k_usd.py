from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/investments/portfolio_10k.json"
TARGET = ROOT / "data/investments/portfolio_10k_usd.json"
STARTING_CAPITAL_USD = 10_000.0


def number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def usd_pln_at_entry(source: dict, entry_date: str | None) -> float:
    snapshots = source.get("snapshots") or []
    dated = [
        s for s in snapshots
        if s.get("date") == entry_date and number(s.get("reporting_usd_pln")) > 0
    ]
    if dated:
        return number(dated[-1]["reporting_usd_pln"])
    for position in source.get("positions") or []:
        if position.get("currency") == "USD" and position.get("entry_date") == entry_date:
            rate = number(position.get("entry_fx_to_pln"))
            if rate > 0:
                return rate
    return current_usd_pln(source)


def current_usd_pln(source: dict) -> float:
    direct = number((source.get("reporting_fx") or {}).get("usd_pln"))
    if direct > 0:
        return direct
    for position in source.get("positions") or []:
        if position.get("currency") == "USD":
            rate = number(position.get("current_fx_to_pln"))
            if rate > 0:
                return rate
    raise ValueError("USD/PLN rate is unavailable")


def local_to_usd(local_to_pln: float, usd_to_pln: float) -> float:
    if local_to_pln <= 0 or usd_to_pln <= 0:
        return 1.0
    return local_to_pln / usd_to_pln


def convert_position(position: dict, source: dict, usd_pln_now: float) -> dict:
    item = copy.deepcopy(position)
    target_weight = number(item.get("target_weight"))
    entry_value_usd = STARTING_CAPITAL_USD * target_weight
    fee_ratio = number(item.get("entry_fee_pln")) / max(number(item.get("entry_value_pln")), 1e-9)
    entry_fee_usd = entry_value_usd * fee_ratio
    invested_usd = max(0.0, entry_value_usd - entry_fee_usd)

    entry_usd_pln = usd_pln_at_entry(source, item.get("entry_date"))
    entry_local_usd = local_to_usd(number(item.get("entry_fx_to_pln"), entry_usd_pln), entry_usd_pln)
    current_local_usd = local_to_usd(number(item.get("current_fx_to_pln"), usd_pln_now), usd_pln_now)
    entry_price = number(item.get("entry_price"))
    current_price = number(item.get("current_price"), entry_price)
    quantity = invested_usd / max(entry_price * entry_local_usd, 1e-9)
    dividends_usd = number(item.get("dividends_pln")) / usd_pln_now
    current_value_usd = quantity * current_price * current_local_usd + dividends_usd
    pnl_usd = current_value_usd - entry_value_usd
    pnl_percent = pnl_usd / entry_value_usd if entry_value_usd else 0.0

    item.update({
        "reporting_currency": "USD",
        "quantity": round(quantity, 8),
        "entry_fx_to_usd": round(entry_local_usd, 8),
        "current_fx_to_usd": round(current_local_usd, 8),
        "entry_notional_usd": round(invested_usd, 2),
        "entry_fee_usd": round(entry_fee_usd, 2),
        "entry_value_usd": round(entry_value_usd, 2),
        "current_value_usd": round(current_value_usd, 2),
        "pnl_usd": round(pnl_usd, 2),
        "pnl_percent": round(pnl_percent, 8),
        "dividends_usd": round(dividends_usd, 2),
        # Compatibility aliases for the existing public renderer. In this USD
        # dataset these numeric fields are denominated in the declared base currency.
        "entry_notional_pln": round(invested_usd, 2),
        "entry_fee_pln": round(entry_fee_usd, 2),
        "entry_value_pln": round(entry_value_usd, 2),
        "current_value_pln": round(current_value_usd, 2),
        "pnl_pln": round(pnl_usd, 2),
        "dividends_pln": round(dividends_usd, 2),
    })
    return item


def convert_benchmark(benchmark: dict, source: dict, usd_pln_now: float) -> dict:
    item = copy.deepcopy(benchmark)
    entry_usd_pln = usd_pln_at_entry(source, item.get("entry_date"))
    entry_local_usd = local_to_usd(number(item.get("entry_fx_to_pln"), entry_usd_pln), entry_usd_pln)
    current_local_usd = local_to_usd(number(item.get("current_fx_to_pln"), usd_pln_now), usd_pln_now)
    fee_ratio = number(item.get("entry_fee_pln")) / max(number(source.get("starting_capital_pln")), 1e-9)
    fee_usd = STARTING_CAPITAL_USD * fee_ratio
    units = (STARTING_CAPITAL_USD - fee_usd) / max(number(item.get("entry_price")) * entry_local_usd, 1e-9)
    current_value_usd = units * number(item.get("current_price")) * current_local_usd
    return_percent = current_value_usd / STARTING_CAPITAL_USD - 1
    item.update({
        "reporting_currency": "USD",
        "units": round(units, 8),
        "entry_fx_to_usd": round(entry_local_usd, 8),
        "current_fx_to_usd": round(current_local_usd, 8),
        "entry_fee_usd": round(fee_usd, 2),
        "current_value_usd": round(current_value_usd, 2),
        "current_value_pln": round(current_value_usd, 2),
        "return_percent": round(return_percent, 8),
    })
    return item


def build() -> dict:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    usd_pln_now = current_usd_pln(source)
    positions = [convert_position(p, source, usd_pln_now) for p in source.get("positions") or []]
    total_value_usd = sum(number(p.get("current_value_usd")) for p in positions) + number(source.get("cash_pln")) / usd_pln_now
    for position in positions:
        position["current_weight"] = round(number(position.get("current_value_usd")) / total_value_usd, 8) if total_value_usd else 0.0

    benchmark = convert_benchmark(source.get("benchmark") or {}, source, usd_pln_now)
    now = datetime.now(timezone.utc).isoformat()
    payload = copy.deepcopy(source)
    payload.update({
        "schema_version": "1.1.0-usd",
        "portfolio_id": "briefrooms-xtb-10k-usd",
        "name_en": "10K Investing — USD model portfolio",
        "base_currency": "USD",
        "reporting_currency": "USD",
        "starting_capital_usd": STARTING_CAPITAL_USD,
        "cash_usd": round(number(source.get("cash_pln")) / usd_pln_now, 2),
        "total_value_usd": round(total_value_usd, 2),
        "total_return_percent": round(total_value_usd / STARTING_CAPITAL_USD - 1, 8),
        "benchmark_return_percent": benchmark.get("return_percent"),
        "generated_from": "portfolio_10k.json instruments, prices and target weights; independently rebased to USD 10,000",
        "generated_at": now,
        "source_portfolio_id": source.get("portfolio_id"),
        "starting_capital_pln": STARTING_CAPITAL_USD,
        "cash_pln": round(number(source.get("cash_pln")) / usd_pln_now, 2),
        "total_value_pln": round(total_value_usd, 2),
        "positions": positions,
        "benchmark": benchmark,
        "snapshots": [
            {
                "date": source.get("launch_date"),
                "timestamp_utc": source.get("launch_date"),
                "event": "independent_usd_portfolio_launch",
                "total_value_usd": STARTING_CAPITAL_USD,
                "benchmark_value_usd": STARTING_CAPITAL_USD,
                "total_value_pln": STARTING_CAPITAL_USD,
                "benchmark_value_pln": STARTING_CAPITAL_USD,
                "cash_pln": 0.0,
            },
            {
                "date": source.get("last_market_session"),
                "timestamp_utc": source.get("last_updated_at"),
                "event": "usd_portfolio_valuation",
                "total_value_usd": round(total_value_usd, 2),
                "benchmark_value_usd": benchmark.get("current_value_usd"),
                "total_value_pln": round(total_value_usd, 2),
                "benchmark_value_pln": benchmark.get("current_value_usd"),
                "cash_pln": round(number(source.get("cash_pln")) / usd_pln_now, 2),
            },
        ],
    })
    return payload


def main() -> None:
    payload = build()
    TARGET.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)}: {payload['total_value_usd']:.2f} USD")


if __name__ == "__main__":
    main()
