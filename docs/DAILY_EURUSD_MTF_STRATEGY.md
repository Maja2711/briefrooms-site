# EUR/USD Daily MTF strategy — design contract

H1 is the primary execution timeframe. D1 is a countertrend/structural-risk guard. W1 is background context, not a direction vote.

Regimes: strong up/down trend; flat horizontal range; transition/uncertain. Native Belief Core remains the only LONG/SHORT/FLAT authority. Each entry must be supported by completed H1 candles, D1 context, and an entry-time snapshot.

Record 24H and 72H mean H1 high-low range in pips, median, 75th percentile, H1 true range and source age. Stop and target calculation must be symmetric across LONG/SHORT, reflect spread and current volatility and never widen the hard stop of an already-open position.

Trend entry: confirmed H1 trend; avoid entry against a strong D1 opposing trend; trend management favors holding through normal H1 candle noise.
Range entry: after a confirmed outer-band touch/bounce; target mean reversion; avoid entries too close to the middle with poor reward/risk.
Transition: no new entry. W1 only adds background regime and is not a veto.

All new geometry requires independent EPE price verification, fail-closed freshness and an immutable native decision manifest. Paper-only until an independently sourced executable feed (e.g. Saxo) is integrated. No rewrite of historical trading records.
