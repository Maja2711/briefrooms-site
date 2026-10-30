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

## Follow-up implementation (same day)
- WES LIMIT EPE now explicitly validates the frozen target's authorization start, expiry, current quote age, and future timestamp tolerance for all instruments.
- BTC and S&P canonical live LIMIT touches remain eligible for EPE verification; historical bar touches fail with a distinct reason `historical_target_touch_not_live_executable_quote` rather than a generic missing contract. No fake historical fills are created.
- The observer now emits `PENDING_ENTRY_QUOTE_STALE_OR_MISSING` on missing fresh marks with active entry decisions, and records `EPE_HISTORICAL_TOUCH_NOT_EXECUTABLE` separately.
- The observer proposes a monotone 50%-of-peak favorable stop in `profit_protection_shadow` from prospective valid price marks only. It has **no** authorization to modify a stop or close a position. A proposal is *not* an executable or cost-adjusted quote.
- Regression tests `tests/test_wes_limit_contract_hardening.py` cover BTC and S&P canonical fills, expired plans and historical bar rejection. These are included in push and scheduled workflow validation.
- Historical missed trades and actual Fed-day market reversal cannot be reconstructed from these tests alone; full archived workflow logs and verified executable quotes remain required before live profit-stop authority.
