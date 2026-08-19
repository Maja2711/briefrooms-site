# WES-SPX ↔ Belief Core Read-Only Bridge

## Purpose

PR #8 creates a prospective, shadow-only link between a frozen WES SPX decision and the frozen Belief Core state that was actually available when that WES decision was made.

```text
frozen WES-SPX decision
        +
point-in-time frozen SPX Belief set
        ↓
WES–Belief Observation
```

It is a telemetry layer, not a trading feature.

## Hard safety

The bridge cannot change:

- WES direction,
- entry,
- TP/SL,
- raw score,
- strategy ranking,
- sizing or exposure,
- veto state,
- execution,
- learning,
- policy,
- Belief Core.

All decision-influence controls are hard `false`.

## Prospective-only capture

The first production run records `activated_at` and captures nothing historical.

A later WES source record can enter the bridge only when:

1. it is SPX (`sp500_futures`);
2. its WES decision and first source capture are after bridge activation;
3. the bridge sees it within 60 minutes of the source ledger's first capture;
4. the WES decision is frozen and directional;
5. a complete point-in-time set of the five predeclared SPX beliefs existed at the WES decision timestamp.

Missed records are never reconstructed after the capture window.

## Belief set

PR #8 reuses the same point-in-time selector already validated for the BRACE-SPX bridge:

- `spx.trend.bullish`
- `spx.breadth.healthy`
- `spx.volatility.benign`
- `spx.liquidity.supportive`
- `spx.financial_conditions.supportive`

Future, expired, stale or incomplete Belief sets cannot be used.

## Relationship

Directional classification is descriptive only:

- WES long + Belief risk-on → agreement
- WES short + Belief defensive → agreement
- WES long + Belief defensive → conflict
- WES short + Belief risk-on → conflict
- neutral Belief → neutral

Relationship strength is bounded by both the frozen WES score strength (`abs(raw_score)/100`, capped at 1) and frozen Belief confidence.

## Coverage

PR #8 is deliberately SPX-only.

```text
SPX      = full bridge scope
EUR/USD  = deferred / partial coverage
BTC      = deferred / partial coverage
```

EUR/USD and BTC should not be treated as full Engine–Belief calibration until their own Belief coverage is implemented and validated.

## Persistence

The bridge stores its cumulative state only as a private GitHub Actions artifact:

`wes-spx-belief-bridge-state`

It does not commit observations to `main` and it does not modify the existing WES/V5 or WES/BRACE-SPX ledgers.

## Next stage

PR #9 consumes only prospective calibration-eligible PR #8 observations and evaluates:

- WES outcome by agreement/conflict,
- WES-vs-V5 incremental alpha by agreement/conflict,
- a fixed hypothetical Belief risk overlay,
- WITH vs WITHOUT BELIEF.

No promotion or bounded production influence is authorized by PR #8.
