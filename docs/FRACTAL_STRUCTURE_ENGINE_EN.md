# FSE — Fractal Structure Engine

**Module ID:** `IN-09`  
**Mode:** `SHADOW_ONLY` / research challenger  
**Authority:** zero production authority

**Current methodology:** `FSE-CORE-3.0` / `FSE-PHASE-3.0` / `FSE-ADAPTIVE-3.0`; projection `FSE-FAST-2.0`. The repair contract in section 12 supersedes historical timing, horizon and cohort-credit rules below. ASD 2.0 search and abstention: section 13.

## 1. Purpose

FSE describes market geometry across multiple scales and deliberately separates two questions:

1. **Structural Risk** — is market structure becoming unstable, cross-scale inconsistent or heavy-tailed?
2. **Fractal Memory** — does the current multiscale fingerprint resemble prior structures whose forward distribution had a measurable direction and risk profile?

FSE is not the classic “Fractals” indicator, does not search for visual Mandelbrot figures, and is not a standalone trading engine.

## 2. Data and scales

The first implementation uses Yahoo Chart OHLC as a secondary research source for:

- EUR/USD: `EURUSD=X`,
- BTC/USD: `BTC-USD`,
- S&P 500: `SPY` as a liquid index proxy.

Scales:

`5m -> 15m -> 1h -> 4h -> 1D`

4h is deterministically resampled from 1h. Each scale uses up to the most recent 256 bars.

## 3. Structural Risk

Conceptually:

[
Risk_t=f(scale, persistence, multifractality, tails)
]

v1 derives four normalized components:

- **scale_instability** — dispersion of realized volatility after time scaling,
- **persistence_extreme** — distance of Hurst `h(2)` from 0.5 plus disagreement across scales,
- **multifractality** — proxy `max(h(q))-min(h(q))` for `q={1,2,3}`,
- **tails** — excess kurtosis plus the 95th-percentile / median absolute-return ratio.

Score:

[
Risk =
0.28,ScaleInstability+
0.18,Persistence+
0.24,Multifractality+
0.30,Tails
]

The weights are **research priors**, not claimed optimal parameters.

### Regime Detector

FSE classifies structure as:

- `STABLE`,
- `TRENDING`,
- `MEAN_REVERTING`,
- `TRANSITION`,
- `TURBULENT`.

Regime is not direction. `TURBULENT` may coexist with a valid LONG or SHORT thesis.

FSE also emits research-only risk geometry, such as lower sizing or a different stop distance, always with:

`production_applied=false`.

It cannot change live sizing or stops without a later explicit integration into the risk policy owned by the relevant source engine.

## 4. Fractal Memory

The fingerprint does not compare nominal price levels. The path is normalized by local ATR:

[
x_i=
rac{P_i-P_0}{ATR}
]

The current fingerprint is compared with historical structures through normalized RMS distance. Similarity:

[
S=
rac{1}{1+RMS}
]

Nearest analogues generate a weighted forward distribution:

- `P(up)`,
- median forward return,
- median adverse excursion,
- top/mean structural similarity.

### Two memory stages

**Bootstrap:** historical 1h analogues provide the initial distribution but are not treated as prospective performance evidence.

**Prospective Fractal Memory:** each current full feature vector is frozen before outcome. After at least 12 resolved snapshots, analogues may be selected from FSE's own prospectively accumulated memory.

## 5. Freeze and verification

Private durable state:

- `fse_snapshots.jsonl`,
- `fse_resolutions.jsonl`.

Both are append-only and hash-chained.

A snapshot freezes, among other fields:

- timestamp and reference price,
- `risk_score`,
- regime,
- `P(up)`,
- ATR,
- feature vector,
- zero authority.

Outcome is bound only after the **next four 1h market bars**, rather than four wall-clock hours. This avoids weekend and closed-session distortion.

## 6. HSE2 — formal hypotheses

FSE is the seventh research producer for Hypothesis Shadow Engine 2.0.

For each instrument it exposes two separate measurements:

### A. Directional Fractal Memory

[
Brier=(P(up)-Y_{up})^2
]

edge versus a 50/50 baseline:

[
Edge_{dir}=0.25-Brier
]

### B. Structural Risk

Frozen `risk_score` is interpreted as probability of a large move. In v1 a large-move event is:

[
|R_{4h}| geq 0.75 	imes ATR_{1h,frozen}
]

and:

[
Edge_{risk}=0.25-Brier_{risk}
]

Each hypothesis uses formal `N=40`. HSE2 credits only evidence that arrives after its own freeze boundary. Historical FSE bootstrap data receives no HSE2 credit.

## 7. Authority and possible future promotion path

FSE v1 explicitly disables:

- production policy writeback,
- ranking writeback,
- sizing writeback,
- stop-loss writeback,
- source-model writeback,
- execution,
- automatic promotion.

Possible future path:

`FSE -> HSE2 prospective evidence -> statistical gate / challenger governance -> explicit per-engine bridge -> engine-owned RiskPolicy`

A global FSE risk actuator bypassing EURUSD, WES, Stock Trading or BRACE risk ownership is forbidden. Per the canonical architecture, **risk remains per-engine**.

## 8. v1 limitations

- Yahoo is a secondary source, not an execution feed.
- `SPY` is an S&P 500 proxy, not futures or the index itself.
- 4h is resampled from 1h.
- `multifractality_proxy` is not full MF-DFA; it is a generalized-Hurst `h(q)` spread.
- Regime thresholds and risk weights are initial hypotheses.
- Structural similarity does not establish causality.
- Historical bootstrap initializes memory; it is not alpha validation.
- Production sizing/SL influence remains disabled pending prospective validation.

## 9. FSE-PHASE — Phase Engine / Intrabar / Cross-Scale

The FSE-PHASE-1.0 extension remains part of module IN-09; it is not a separate production engine.

Pipeline:

FSE core -> Phase Engine -> Intrabar Formation -> Cross-Scale Alignment -> Phase Fractal Memory -> prospective HSE2 -> P Calibration Challenger -> manual promotion gate

### 9.1 Scales

The Phase Engine analyses:

1m -> 5m -> 15m -> 1h -> 4h -> 1d -> 1w

- 1m, 5m, 15m, 1h, 1d come from the secondary Yahoo Chart research feed.
- 4h is deterministic 1h aggregation.
- 1w is ISO-week aggregation from daily bars.
- Monthly / multi-year bars are not part of the first Phase Engine version; long context is represented by rolling daily history rather than literal 10Y/100Y candles.

### 9.2 Phase Engine

Each scale receives a deterministic description:

- structure_id,
- phase: CONSOLIDATION / DEVELOPMENT / EXPANSION / MATURATION / TRANSITION / REVERSAL,
- direction,
- confidence,
- path efficiency,
- trend z-score,
- volatility ratio,
- curvature.

structure_id is a stable hash of a quantized structural fingerprint. It is not a claim that a universal fractal law has been discovered; it is a research identity for structurally similar states.

### 9.3 Phase Fractal Memory

The current partial path is matched against prefixes of historical motifs on the completion grid:

40% / 55% / 70% / 85%.

The best-matching stage produces:

- phase_progress,
- P(up remaining),
- top/mean similarity,
- median remaining return,
- median remaining bars,
- analogue count.

This is an analogue-based stage estimate, not a deterministic assumption that every market follows the same phases.

### 9.4 Intrabar Formation

A higher scale is observed internally through a child scale:

- 5m <- 1m,
- 15m <- 5m,
- 1h <- 15m,
- 4h <- 1h,
- 1d <- 1h,
- 1w <- 1d.

FSE publishes formation_progress, microstructure direction, efficiency, position within the current range and range fraction. Expected child-bar counts are inferred from historical medians, so SPY sessions are not treated like 24/7 BTC.

### 9.5 Cross-Scale Alignment

Adjacent scales are compared by:

- directional agreement,
- similarity of efficiency / volatility ratio / curvature,
- confidence,
- an informational fast-scale lead score.

Synthetic outputs:

- alignment_score,
- cascade_state = COHERENT / MIXED / FRACTURED,
- dominant_scale.

Alignment is research Evidence. It does not create an executable trade signal.

### 9.6 P Calibration / Regime-Phase Challenger

The challenger uses:

- P_base from Deep Fractal Memory,
- P_phase from 1h Phase Fractal Memory,
- cross-scale alignment,
- Structural Risk regime.

It emits a separate P_challenger. The change is hard-capped at ±6 pp versus P_base.

TURBULENT and TRANSITION apply explicit confidence shrinkage. These parameters are a frozen research prior, not a trained production calibrator.

Source-model probability is never overwritten.

### 9.7 Prospective HSE2

From FSE-PHASE-1.0, FSE exposes two additional hypotheses:

1. Phase Fractal Memory vs 50/50 — metric: prospective directional Brier edge.
2. Regime-Phase P Calibration Challenger vs frozen FSE v2 base P — metric: Brier_base - Brier_challenger.

Each experiment uses N=40. Only resolutions originating from snapshots frozen under FSE-PHASE-1.0 count. Older FSE v2 snapshots remain in the durable ledger but receive no credit for the new hypotheses.

### 9.8 Promotion

The public promotion_gate may only reach:

- NOT_ELIGIBLE,
- READY_FOR_MANUAL_REVIEW.

automatic_promotion=false.

Even after a positive HSE2 verdict, an explicit per-engine bridge and the owning engine's promotion gate are required. FSE cannot create a global risk or probability actuator.

## 10. FSE Intraday Fast Projection

Short-horizon FSE has a separate lightweight presentation path driven by an independent 5-minute workflow.

Pipeline:

Yahoo 1m / 7d -> 1m -> resample 5m / 15m / 1h -> Phase descriptor -> merge with latest full 4h/1d/1w context -> Cross-Scale Alignment -> `data/investments/fse_intraday_public.json`.

Rules:
- target freshness: 5 minutes; the LAB primary source is the independent Cloudflare Worker `briefrooms-fse-fast` with its own `*/5 * * * *` cron plus on-demand compute/cache, while `fse-intraday-fast.yml` and `data/investments/fse_intraday_public.json` remain the static fallback; a GitHub `schedule` outage alone can no longer stop the FSE fast presentation path;
- 1m/5m/15m/1h are refreshed from one common 1-minute feed;
- 4h/1d/1w remain sourced from the full hourly FSE-PHASE;
- the fast path creates no durable snapshots/resolutions and does not feed HSE2;
- zero production authority;
- the frontend refreshes the active FSE tab every 60 s;
- the snapshot is marked STALE after 12 minutes or when the latest source bar is older than 12 minutes, providing a buffer beyond the nominal five-minute cadence.

## 11. FSE Adaptive Learning v1 — prequential updates inside existing FSE v2

**Component:** `FSE-ADAPTIVE-1.0` inside `IN-09`; not a separate engine or competing learning/promotion fabric. It extends the existing immutable, hash-chained `fse_v2_snapshots.jsonl` / `fse_v2_resolutions.jsonl` ledger and sends two additional prospective measurements through the existing HSE2 FSE-PHASE source contract.

**Adaptive Ensemble:** uses only the frozen Deep Memory P, Phase Memory P and existing Regime-Phase Challenger P. Non-negative weights are estimated from independently resolved, non-overlapping four-market-hour outcome blocks (minimum 12), using regularized Brier losses and a decayed training window. The result is bounded to ±6 percentage points from Deep Memory, with no source-model overwrite. A repeated market timestamp reuses the immutable frozen value; a later outcome cannot retroactively retrain that snapshot.

**Separate structural-risk calibrator:** structural risk is an index, not itself P(large move). Newly frozen 1h ATR and risk-index fields make it possible to settle the actual event `abs(forward_return)>=0.75*frozen_atr_fraction`. A Beta-smoothed event-rate prior and a non-negative ridge-regularized risk-score slope yield a bounded, conditional probability only after >=40 prospective settlements and >=12 non-overlapping blocks. Until then, calibrated P is null (rather than fabricated). The comparison baseline is the **frozen empirical large-move rate**, not a 50/50 constant.

**Completed hourly candles:** the resolver requires the final forward 1h bar's close time (its opening timestamp + 1 hour) before settlement, preventing partial-bar outcomes from training either model.

**Evidence and authority:** both probabilities and their paired baselines are frozen before their outcomes and measured as paired Brier improvements; new `FSE-ADAPTIVE-1.0` hypotheses have separate N=40 HSE2 boundaries. The original FSE-PHASE metrics and contracts remain intact. `SHADOW_ONLY`; no trade, risk-policy, Belief, sizing/stop, or automatic promotion authority.

**ASD remains independent:** the hourly immutable collector continues capturing matched and unmatched prospective outcomes; no synthetic sample inflation or unverified assignment of unmatched paths. Feature-importance learning and automatic methodology proposal are subsequent milestones, not part of v1.

**Before potential production review:** paired OOS improvement, overlap-robust uncertainty/HAC, concept drift and multiple-comparisons control, plus explicit owner-specific Evolution Gate promotion. Shadow Champion is never production Champion by implication.

## 12. Version 2.0 contract — measurement and publication repair

- `Bar.timestamp` is the canonical bar open, separate from `source_observed_at`. A trailing live quote updates its proper bucket instead of creating a bar at an arbitrary instant. The adapter sorts/deduplicates data and checks finite positive prices and OHLC consistency. SPY respects the 09:30 America/New_York session, DST and available closing-time metadata. Four-hour aggregation has stable boundaries and never spans different sessions.
- Snapshots bind methodology version and canonical bar. Source observation, `bar_open_at`, actual `frozen_at` and later `outcome_closed_at` are distinct. Repeating a bar cannot change its frozen forecast. Settlement uses four forward bars and waits for the final bar to close; training respects actual outcome completion. Numeric P=0 remains zero instead of becoming fallback 0.5.
- Deep Memory historical daily context includes only daily bars completed at that historical instant, never a later daily close.
- Phase exposes descriptive `p_up_remaining` to motif completion separately from `p_up_4h` for the next four hourly bars. Only the fixed four-bar target feeds the challenger, ensemble, Brier and matching HSE2 hypothesis.
- Earlier snapshots/resolutions retain their original bytes and hashes. Corrected methods neither retrospectively settle old pending snapshots nor train on earlier cohorts; legacy counts remain explicit. HSE2 creates a fresh freeze and prospective N. Earlier FSE verdicts appear under `historical_fse_experiments/results/lessons`, outside current formal results; original epochs/evidence/results are not rewritten.
- ASD retains its own append-only state. Backend/integrity validation continues to block invalid collection; presentation tests run separately without stopping the collector. PL/EN display valid current matches and the catalogue without a permanent family-count truncation. Existing Shadow Observatory monitors the ASD catalogue and marks publications older than three hours STALE.
- Fast reports `transport_version=FSE-FAST-2.0`, bounded acquisition and per-instrument `source_status`: `market_state`, `source_reference_at`, raw age and session-adjusted `source_lag_minutes`. Stale/future data cannot count as success merely because HTTP returns 200. The frontend also tries fallback for stale/missing instruments and selects each newest compatible scale. Unavailable descriptors cannot vote in trend. Old analogue memory is never attached to a new structure.
- PL/EN script URLs receive a new version and production parity covers both central LAB renderers. Edge deployment validates actual source freshness rather than response-generation time alone.

Authority remains unchanged: SHADOW_ONLY, no execution, production-policy/Belief changes or automatic promotion. A methodology repair is not evidence of model improvement; its prospective assessment starts at its own new boundary.

## 13. structure search 3.0 / ASD 2.0

Current research versions are `FSE-CORE-3.0`, `FSE-PHASE-3.0`, `FSE-ADAPTIVE-3.0` and `FSE-ASD-2.0`; transport remains `FSE-FAST-2.0`. Analogue feature/outcome windows cannot overlap the live query, and multiple windows from one episode cannot manufacture independent support. Deep/Phase/Core require at least eight independent matches and a quality rule learned from prior data without outcome optimization. No reliable match produces explicit `ABSTAIN` and `P=null`, never fabricated 50% or directional Brier credit. Full geometry is compared with a simple exact z-normalized Euclidean shape distance profile: a query/history reference, not a full all-pairs Matrix Profile. Paired ablation freezes both probabilities against the same future target; formal N counts disjoint outcome blocks. ASD uses density cores, independent support, query exclusion and an ambiguity threshold. Selection preregisters its sample and a shared lifetime budget of 24 trials × 2 endpoints, without per-cycle resets. Existing family definitions, snapshots, outcomes and lessons retain their hashes; previous cohorts/catalogue remain historical, while current HSE has a new freeze. The module remains SHADOW_ONLY with no production EURUSD, Belief, position or promotion authority.

Implementation contract:

- Core: historical and prospective retrieval share a 32-point shape from a 64-bar window and at least eight disjoint shape/target episodes. The 22 multiscale geometry features remain descriptive audit context and do not manufacture independent shape support. Distance quality uses only prior independent nearest-neighbor geometry; the 0.75 ceiling is a frozen research prior.
- Deep: path/geometry/daily blocks are scaled separately from prior candidates. Simple compares shape over a fixed 96-bar window. Complex features remain the full arm until paired ablation supports removing them; no automatic feature deletion or promotion.
- Ablation: `full_brier_improvement_vs_simple`, positive values favor full geometry; `raw_paired_n` is separate from formal disjoint-target N. Neither Brier nor research review proves profitability after costs.
- ASD: at least six independent discovery episodes; radius/margin match quality, a fixed first sample of 40 independent outcomes, one selection budget and append-only `selection_events`. The two-endpoint test accounts for all 24 preregistered trials. Later evidence cannot revise a frozen verdict.
- Preservation: existing ledgers/definitions are not rewritten and production data is not migrated. Code rollback retains artifacts; each future methodology needs its own identity and freeze.

- ASD 2.0 timing: completed bars define the motif while a fresh quote defines the reference price. Its real source clock must be no later than freeze and at most five minutes old. Outcomes use the first four market bars opening at or after freeze; pre-freeze bars cannot enter the future target. Live execution freezes after data acquisition; an explicit replay cutoff remains fixed.
- HSE retains the highest frozen methodology version; a temporarily older publication cannot disable its newer cohort or resume older evidence collection.
