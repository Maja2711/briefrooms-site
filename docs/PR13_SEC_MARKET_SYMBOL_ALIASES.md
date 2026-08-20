# PR13 — reviewed SEC market-symbol aliases

## Purpose

PR13 intentionally fails closed when a BRACE market symbol is not present in the SEC ticker map. The production audit on 2026-08-20 identified two active candidate/watchlist entities with valid SEC reporting but local-market symbols that do not match their SEC reporting symbols:

| BRACE market symbol | SEC reporting symbol | Expected CIK | SEC reporting regime |
| --- | --- | ---: | --- |
| `NOVO-B.CO` | `NVO` | `353278` | foreign private issuer (20-F / 6-K) |
| `SAP.DE` | `SAP` | `1000184` | foreign private issuer (20-F / 6-K) |

## Contract

The alias layer is an explicit reviewed allowlist only.

It does **not**:

- strip `.DE`, `.CO` or other exchange suffixes,
- infer ADR tickers,
- fuzzy-match issuer names,
- invent CIKs,
- backfill historical evidence.

For an alias to activate, the live SEC ticker map must contain the reviewed SEC reporting symbol and its CIK must equal the frozen expected CIK. Any mismatch fails closed.

## Anti-hindsight

Resolving a previously unresolved source does not reopen or move the existing PR13 collection window. Filings accepted at or before the original window boundary remain cursor/reference history only. Only filings prospectively available after the existing boundary may become PR13 observations.

## Governance

Alias contract version: `sec-market-symbol-alias-v1`.

This change is source-resolution plumbing only. It does not assign Belief polarity, create forecasts, influence BRACE, change ranking/exposure/sizing, or affect promotion authority.
