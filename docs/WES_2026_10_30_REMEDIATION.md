# WES reliability remediation — 2026-10-30

## Scope and authority
This deployment adds **prospective read-only** monitoring for EURUSD, BTCUSD and S&P 500. It does **not** modify any existing frozen stop, take-profit, position, EPE authorization, risk decision or entry threshold. WES remains paper-only.

## Running components
- `scripts/wes_risk_observatory.py` runs after canonical live-price refresh and after the governed WES SL/TP monitor in `wes-live-execution.yml`, which is scheduled every five minutes.
- Its persisted output is `data/investments/wes_risk_observatory.json`. GitHub Actions emits warnings for monitored anomalies.
- Per-instrument entry funnel captures admission, confirmations, trigger delta, EPE state and missing canonical target contracts, independently from valid NO_TRADE.
- Open positions have last fresh quote age and a prospective (not historical) favorable-excursion peak in R units. Peak is scoped to week, instrument, entry timestamp and price, and survives temporary quote outages. No stale quote can advance it.
- A giveback warning requires peak >= 0.5R and a decline >= max(0.25R, 50% of that peak).

## Explicit non-goals / remaining blockers
- This is **not** a certified profit-protection executor. It cannot issue a close or move stop on a SHADOW Fed/ECB assessment. Those permissions remain disabled by the existing CB policy.
- No historical price replay is used to manufacture historical peaks or trades. A five-minute sampling observer is not equivalent to tick-level MFE.
- A historical `NO_FILL` with `missing_canonical_live_target_contract` is surfaced for investigation. It is not fixed by assuming Yahoo OHLC is a fresh executable canonical quote. An execution-funnel replay with real run logs is required to distinguish a missing contract from a correctly rejected entry.
- A fresh live price is only a monitoring mark; the monitor does not assert executable bid/ask or realized P&L.
- Before enabling automatic profit protection, certify a separate order/exit authority using post-decision timestamps, spread/slippage, replay parity, per-position immutable decisions, and prospective observation. Do not enable Fed/ECB close or stop upgrade on uncalibrated data.

## Verification
- `python -m unittest discover -s tests -p test_wes_risk_observatory.py -v`
- `python scripts/wes_risk_observatory.py` (reads current weekly state and canonical prices)
- Review GitHub Actions warnings and the persisted JSON after a scheduled execution.
