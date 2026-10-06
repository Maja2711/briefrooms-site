# FSE — Fractal Structure Engine

**Module ID:** `IN-09`  
**Mode:** `SHADOW_ONLY` / research challenger  
**Authority:** zero production authority

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
x_i=rac{P_i-P_0}{ATR}
]

The current fingerprint is compared with historical structures through normalized RMS distance. Similarity:

[
S=rac{1}{1+RMS}
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

Short-horizon FSE has a separate lightweight presentation path driven by the shared 5-minute market clock.

Pipeline:

Yahoo 1m / 7d -> 1m -> resample 5m / 15m / 1h -> Phase descriptor -> merge with latest full 4h/1d/1w context -> Cross-Scale Alignment -> `data/investments/fse_intraday_public.json`.

Rules:
- cadence: 5 minutes, through the existing `weekly-live-prices.yml` workflow;
- 1m/5m/15m/1h are refreshed from one common 1-minute feed;
- 4h/1d/1w remain sourced from the full hourly FSE-PHASE;
- the fast path creates no durable snapshots/resolutions and does not feed HSE2;
- zero production authority;
- the frontend refreshes the active FSE tab every 60 s;
- the snapshot is marked STALE after 12 minutes or when the latest source bar is older than 12 minutes.
