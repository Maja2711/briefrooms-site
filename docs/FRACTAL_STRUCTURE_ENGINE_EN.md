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
