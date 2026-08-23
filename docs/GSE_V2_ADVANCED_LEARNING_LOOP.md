# GSE v2 Advanced Historical Learning Loop

## Purpose

GSE v2 is the research layer that asks a stricter question than GSE v1:

> Given the current geopolitical scenario and the market regime that existed
> immediately before the forecast, what happened after comparable historical
> geopolitical episodes, and does that information improve a frozen GSE v1
> forecast out of sample?

The layer is intentionally separate from GSE v1, Belief Core, WES, BRACE and execution. It can learn, score challengers and propose better research parameters, but it cannot auto-apply those proposals or change a production decision.

## Advanced loop

The first v2 overlay used a scenario/asset/horizon hit rate with Beta shrinkage and a bounded sample-count weight. The advanced loop adds pre-event market-regime snapshots, explicit event feature vectors, geopolitical episode clustering, source-reliability and recency weighting, similarity kernels, hierarchical shrinkage, effective-sample-size accounting, uncertainty intervals, strict point-in-time walk-forward and leave-cluster-out validation, chronological holdout policy proposals, prospective paired v1-v2 verification, exact-slice calibration, readiness gates and a hash-chained append-only learning ledger.

## Historical catalogue v2

`data/gse/historical_event_catalog.json` remains outcome-free. It adds `event_cluster_id`, `source_reliability` and ex-ante geopolitical descriptors: severity, surprise, global scope, military, energy, shipping, sanctions, food and China/Taiwan relevance. These descriptors are not selected from later market performance.

The curated catalogue expands from 6 to 18 primary-institutional anchors spanning 2014-2024. Correlated headlines from one crisis share a cluster, so one shock cannot inflate effective N.

## Market regime

For every event and live forecast the loop reconstructs only information available strictly before the relevant calendar date. Core regime assets are SPX, USD, US10Y, Brent and Gold. Features are 5/20/60-session returns and 20-session realized volatility. Same-day close is excluded.

## Similarity and effective N

Within an exact scenario-family × asset × horizon slice, analogues are weighted by market-regime similarity, event-feature similarity, source reliability and recency decay. One `event_cluster_id` contributes at most one episode.

Weighted observations use `N_eff = (sum w)^2 / sum(w^2)`, so many weak analogues cannot masquerade as many independent strong samples.

Sparse exact slices shrink toward asset×horizon, scenario×horizon and global-horizon priors, themselves shrunk toward 50%. Each estimate exposes a 90% interval, epistemic confidence and top neighbours. Uncertainty directly attenuates the overlay.

## Fixed active research policy

The preregistered policy is `similarity_temperature=0.85`, `prior_strength=8`, `max_overlay_weight=0.20`, `weight_per_effective_cluster=0.025`. The final v2 probability is bounded and cannot reverse frozen v1 direction.

The loop can grid-search challenger temperature/prior values, but writes only a proposal. `automatically_applied=false` and `active_policy_unchanged=true` are invariants.

## Historical validation

For each historical test event only responses with `response_complete_at <= test_event_at` are eligible and the entire current event cluster is excluded. Regime-aware performance is compared against an unweighted analogue baseline using Brier score, log loss, calibration bias and directional hit rate. Results are reported overall, by horizon, scenario and asset.

This is deliberately not called a historical replay of GSE v1 because point-in-time GSE v1 evidence/scenario state did not exist for those old dates.

## Chronological holdout

The earlier ~70% of event clusters form training data for challenger-policy ranking and the latest ~30% stay as holdout. A proposal may become `eligible_for_human_shadow_review` only when the holdout improves over the fixed research policy. No proposal is auto-promoted.

## Prospective paired validation

Every new frozen GSE v1 forecast may get a separate advanced-v2 candidate. When v1 is verified, advanced v2 uses the same realized binary outcome. This creates a clean paired experiment with Brier/log-loss deltas and calibration bias by asset, horizon and exact asset×horizon.

## Promotion gate

A human promotion review is permitted only when historical walk-forward N >= 30, regime-aware history beats unweighted history, prospective paired N >= 30, prospective delta Brier <= -0.005, prospective log loss does not deteriorate and absolute prospective calibration bias <= 0.10. Even then `automatic_promotion=false`.

## Learning ledger

Every cycle appends a hash-chained row to `gse_v2_learning_ledger.jsonl`, hashing the enriched library, historical report, policy proposal and prospective calibration plus new candidate/verification counts. A broken chain fails closed.

## Runtime outputs

Private `gse-shadow-state` gains: `gse_v2_enriched_library.json`, `gse_v2_historical_walkforward.json`, `gse_v2_policy_proposal.json`, `gse_v2_regime_forecasts.jsonl`, `gse_v2_regime_verifications.jsonl`, `gse_v2_regime_calibration.json`, `gse_v2_learning_state.json`, `gse_v2_learning_ledger.jsonl`.

## Cadence

GSE v1 evidence remains hourly and frozen forecasts every 6 hours. Advanced prospective verification runs after GSE cycles. Historical regime library refreshes weekly or immediately after catalogue/library changes. Walk-forward, policy proposal and ledger update on every learning cycle.

## Hard boundary

The advanced loop asserts `automatic_tuning_enabled=false`, `policy_proposal_auto_apply_enabled=false`, `automatic_promotion_enabled=false`, `decision_engine_connected=false`, `belief_core_connected=false`, `trade_execution_enabled=false`, `policy_output_enabled=false`, `v1_forecast_modified=false`.

Therefore history accelerates learning without giving a backtest direct authority over Belief Core, WES, BRACE or execution. The intended path remains: grow historical coverage → historical walk-forward → prospective paired validation → reviewed shadow-policy promotion → GSE v1 vs promoted v2 → only then consider a separate v2-to-Belief adapter and later WITH/WITHOUT evidence for WES/BRACE.
