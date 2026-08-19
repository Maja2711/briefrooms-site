# BRACE-SPX ↔ Belief Core Read-Only Bridge

## Purpose

This bridge creates prospective, point-in-time **Engine–Belief Observations** for BRACE-SPX Generation 6 and Belief Core.

It is a measurement layer only. It does not change BRACE-SPX or Belief Core and cannot influence any investment decision.

## Data flow

```text
Frozen BRACE-SPX G6 shadow state
              +
Latest complete frozen SPX Belief forecast set
available at the BRACE-SPX timestamp
              ↓
Engine–Belief Observation
              ↓
agreement / conflict / neutral / unavailable
```

## Hard safety contract

All bridge controls are hard-off:

- `belief_influence = false`
- `exposure_change = false`
- `score_change = false`
- `veto = false`
- `sizing_change = false`
- `candidate_ranking_change = false`
- `trade_execution = false`
- `policy_output = false`
- `automatic_tuning = false`
- `with_without_evaluation = false`
- `bounded_modifier = false`

PR #6 cannot authorize bounded influence.

## BRACE-SPX state

Generation 6 has no authorized single champion. The bridge therefore never fabricates a single BRACE-SPX trade decision.

During G6 warm-up:

```text
available = false
stance = unavailable
reason = brace_spx_warmup_no_opinion
```

After G6 itself reaches `shadow_active_no_orders` and emits exactly eight predeclared candidate snapshots, the bridge summarizes their already-produced `target_exposure_next_session` values as a **parallel candidate consensus**:

- mean exposure >= 0.60 → `risk_on`
- mean exposure <= 0.40 → `defensive`
- otherwise → `neutral`

No champion is selected and candidate rankings are not changed.

## Belief state

The bridge uses the latest **complete frozen forecast set** containing all five predeclared SPX-relevant beliefs that was known at the BRACE-SPX timestamp and was still active at that timestamp:

- `spx.trend.bullish`
- `spx.breadth.healthy`
- `spx.volatility.benign`
- `spx.liquidity.supportive`
- `spx.financial_conditions.supportive`

The Belief stance is a descriptive equal-weight mean of the five supportive probabilities:

- mean >= 0.60 → `risk_on`
- mean <= 0.40 → `defensive`
- otherwise → `neutral`

This aggregation is telemetry only. It is not a new Belief Core probability and is not fed back into Belief Core.

The frozen Belief set must be no more than 18 hours old at the BRACE-SPX timestamp. Future or expired forecasts are excluded.

## Relationship classification

When both states are available:

- same directional stance → agreement
- opposite directional stance → conflict
- at least one neutral stance → neutral

A relationship is marked strong only when the smaller of BRACE-SPX consensus confidence and Belief forecast confidence is at least 0.65.

This relationship is descriptive only. It has no alpha claim in PR #6.

## Prospective-only activation

Historical reconstruction is prohibited.

The first production run creates `activated_at` and captures no observation. Only BRACE-SPX states created at or after that activation can create records.

Each unique BRACE-SPX `updated_at` can create at most one Engine–Belief Observation. Later Belief updates cannot rewrite the already frozen pair. Workflow retries are idempotent.

## Private state

The bridge stores its own cumulative private artifact:

`brace-spx-belief-bridge-state`

containing:

- `bridge_state.json`
- `engine_belief_observations.jsonl`
- `BRACE_SPX_BELIEF_BRIDGE_REPORT.json`

The workflow reads Belief Core artifacts and the BRACE-SPX research branch but has only read permissions for repository content and Actions data. It does not commit to `main` or `brace-spx-research`.

## What comes later

A later reviewed PR may use these frozen observations for Engine–Belief calibration and `WITH vs WITHOUT BELIEF` counterfactual analysis.

That later work must remain separate from PR #6 and must not reinterpret unavailable or pre-activation states as valid samples.
