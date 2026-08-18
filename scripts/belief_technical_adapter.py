from __future__ import annotations

from typing import List, Optional, Sequence

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, clamp, observation_to_evidence
from belief_core import iso_z
from belief_market_data_adapter import Bar, MarketSnapshot


class TechnicalEvidenceAdapter:
    name = "technical_evidence"
    version = "1.0.0"

    def run(self, snapshot: MarketSnapshot) -> AdapterResult:
        symbol = "SPY"
        rows = snapshot.bars[symbol]
        observed_at = iso_z(snapshot.observed_at(symbol))
        source_ref = f"derived:yahoo:{symbol}:{observed_at}:technical-v1"
        obs: List[Observation] = []
        def add(metric: str, value, unit: str, cluster: str, metadata=None) -> Observation:
            row = Observation.make(adapter=self.name, metric=metric, entity=symbol, observed_at=observed_at,
                value=value, unit=unit, source="Derived from Yahoo Finance OHLCV", source_type="derived",
                source_ref=source_ref, reliability=.78, independence_cluster=cluster,
                tags=("technical", self.version), metadata=metadata)
            obs.append(row); return row

        mom_3h = snapshot.return_over_bars(symbol, 6)
        mom_1d = snapshot.return_over_bars(symbol, 13)
        mom_5d = snapshot.return_over_bars(symbol, 65)
        rsi = self._rsi([x.close for x in rows], 14)
        ma20 = self._mean([x.close for x in rows[-20:]])
        ma50 = self._mean([x.close for x in rows[-50:]])
        close = rows[-1].close
        dist_ma20 = 0.0 if not ma20 else close / ma20 - 1.0
        dist_ma50 = 0.0 if not ma50 else close / ma50 - 1.0
        breakout = self._breakout_score(rows, 20)
        vwap = self._session_vwap(snapshot.session_bars(symbol))
        vwap_dist = 0.0 if not vwap else close / vwap - 1.0
        atr_pct = self._atr_pct(rows, 14)
        support, resistance = self._support_resistance(rows, 20)
        trend_score = (.24 * clamp(mom_3h/.008,-1,1) + .24 * clamp(mom_1d/.015,-1,1)
            + .14 * clamp(mom_5d/.04,-1,1) + .14 * clamp(dist_ma20/.02,-1,1)
            + .10 * clamp(dist_ma50/.04,-1,1) + .08 * clamp(vwap_dist/.01,-1,1) + .06 * breakout)
        add("momentum_3h", mom_3h, "return", "derived:SPY:technical")
        add("momentum_1d", mom_1d, "return", "derived:SPY:technical")
        add("momentum_5d", mom_5d, "return", "derived:SPY:technical")
        add("rsi_14", rsi, "index", "derived:SPY:technical")
        add("distance_ma20", dist_ma20, "return", "derived:SPY:technical")
        add("distance_ma50", dist_ma50, "return", "derived:SPY:technical")
        add("breakout_20", breakout, "score", "derived:SPY:technical")
        add("vwap_distance", vwap_dist, "return", "derived:SPY:technical", {"vwap_proxy":"volume-weighted typical price from available intraday OHLCV"})
        add("atr_14_pct", atr_pct, "return", "derived:SPY:technical")
        add("support_20", support, "price", "derived:SPY:levels")
        add("resistance_20", resistance, "price", "derived:SPY:levels")
        composite = add("trend_composite", trend_score, "score", "derived:SPY:technical")
        evidence = observation_to_evidence(composite, EvidenceAssessment(
            "spx.trend.bullish", 1 if trend_score >= 0 else -1, clamp(abs(trend_score),.10,1.0),
            "technical_trend", f"SPY technical composite={trend_score:.3f}; 3h={mom_3h:.3%}, 1d={mom_1d:.3%}, 5d={mom_5d:.3%}, RSI14={rsi:.1f}, VWAPdist={vwap_dist:.3%}"))
        return AdapterResult(self.name, tuple(obs), (evidence,))

    @staticmethod
    def _mean(values: Sequence[float]) -> Optional[float]: return None if not values else sum(values)/len(values)

    @staticmethod
    def _rsi(closes: Sequence[float], period: int) -> float:
        if len(closes) <= period: return 50.0
        diffs=[b-a for a,b in zip(closes[-period-1:-1],closes[-period:])]
        gains=sum(max(x,0.0) for x in diffs)/period; losses=sum(max(-x,0.0) for x in diffs)/period
        if losses <= 1e-12: return 100.0 if gains > 0 else 50.0
        rs=gains/losses; return 100.0-100.0/(1.0+rs)

    @staticmethod
    def _breakout_score(rows: Sequence[Bar], lookback: int) -> float:
        prior=list(rows[-lookback-1:-1])
        if not prior: return 0.0
        highs=[x.high if x.high is not None else x.close for x in prior]; lows=[x.low if x.low is not None else x.close for x in prior]
        close=rows[-1].close; hi,lo=max(highs),min(lows)
        if close > hi: return 1.0
        if close < lo: return -1.0
        mid=(hi+lo)/2.0; return clamp((close-mid)/max(1e-9,(hi-lo)/2.0),-1.0,1.0)

    @staticmethod
    def _session_vwap(rows: Sequence[Bar]) -> Optional[float]:
        num=den=0.0
        for row in rows:
            if row.volume is None or row.volume <= 0: continue
            typical=((row.high if row.high is not None else row.close)+(row.low if row.low is not None else row.close)+row.close)/3.0
            num += typical*row.volume; den += row.volume
        return None if den <= 0 else num/den

    @staticmethod
    def _atr_pct(rows: Sequence[Bar], period: int) -> float:
        chunk=list(rows[-period:])
        if len(chunk) < 2: return 0.0
        tr=[]; prev=chunk[0].close
        for row in chunk[1:]:
            high=row.high if row.high is not None else row.close; low=row.low if row.low is not None else row.close
            tr.append(max(high-low,abs(high-prev),abs(low-prev))); prev=row.close
        return 0.0 if not tr or chunk[-1].close == 0 else (sum(tr)/len(tr))/chunk[-1].close

    @staticmethod
    def _support_resistance(rows: Sequence[Bar], lookback: int):
        chunk=list(rows[-lookback:])
        if not chunk: return 0.0,0.0
        lows=[x.low if x.low is not None else x.close for x in chunk]; highs=[x.high if x.high is not None else x.close for x in chunk]
        return min(lows),max(highs)
