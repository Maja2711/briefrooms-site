from __future__ import annotations

from statistics import median
from typing import List, Sequence
from zoneinfo import ZoneInfo

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, observation_to_evidence, strength_from_return
from belief_core import iso_z
from belief_market_data_adapter import Bar, MarketSnapshot

NY = ZoneInfo("America/New_York")


class LiquidityEvidenceAdapter:
    name = "liquidity_evidence"
    version = "1.0.0"

    def run(self, snapshot: MarketSnapshot) -> AdapterResult:
        observed_at = iso_z(snapshot.observed_at("SPY"))
        obs: List[Observation] = []
        def add(metric: str, entity: str, value, unit: str, cluster: str, source_ref: str, metadata=None) -> Observation:
            row = Observation.make(adapter=self.name, metric=metric, entity=entity, observed_at=observed_at,
                value=value, unit=unit, source="Derived from Yahoo Finance OHLCV", source_type="derived",
                source_ref=source_ref, reliability=.76, independence_cluster=cluster,
                tags=("liquidity", self.version), metadata=metadata)
            obs.append(row); return row

        spy_rows = snapshot.bars["SPY"]
        rvol = self._relative_session_volume(spy_rows, snapshot)
        abnormal = max(0.0, rvol - 1.0)
        session = snapshot.session_bars("SPY")
        dollar_turnover = sum(x.close * float(x.volume or 0.0) for x in session) if any(x.volume is not None for x in session) else 0.0
        amihud = self._amihud_proxy(session)
        add("relative_volume", "SPY", rvol, "ratio", "derived:SPY:liquidity", f"derived:yahoo:SPY:{observed_at}:rvol")
        add("abnormal_volume", "SPY", abnormal, "ratio", "derived:SPY:liquidity", f"derived:yahoo:SPY:{observed_at}:abnormal-volume")
        add("dollar_turnover", "SPY", dollar_turnover, "currency_notional", "market:SPY:volume", f"derived:yahoo:SPY:{observed_at}:turnover")
        add("amihud_proxy", "SPY", amihud, "return_per_notional", "derived:SPY:liquidity", f"derived:yahoo:SPY:{observed_at}:amihud", {"lower_is_more_liquid": True})

        credit_rel = snapshot.ratio_return("HYG", "LQD", 13)
        hyg_1d = snapshot.return_over_bars("HYG", 13)
        credit_obs = add("hyg_lqd_relative_1d", "HYG/LQD", credit_rel, "return", "market:HYG-LQD:credit", f"derived:yahoo:HYG-LQD:{observed_at}")
        hyg_obs = add("hyg_return_1d", "HYG", hyg_1d, "return", "market:HYG:return", f"derived:yahoo:HYG:{observed_at}:liquidity")

        ev1 = observation_to_evidence(credit_obs, EvidenceAssessment(
            "spx.liquidity.supportive", 1 if credit_rel >= 0 else -1, strength_from_return(credit_rel, .008),
            "credit_liquidity", f"HYG/LQD 1d relative return={credit_rel:.4%}"))
        ev2 = observation_to_evidence(hyg_obs, EvidenceAssessment(
            "spx.liquidity.supportive", 1 if hyg_1d >= 0 else -1, strength_from_return(hyg_1d, .012),
            "credit_liquidity", f"HYG 1d return={hyg_1d:.4%}"))
        return AdapterResult(self.name, tuple(obs), (ev1, ev2))

    @staticmethod
    def _relative_session_volume(rows: Sequence[Bar], snapshot: MarketSnapshot) -> float:
        current_date = snapshot.observed_at("SPY").astimezone(NY).date()
        by_day = {}
        for row in rows:
            day = row.timestamp.astimezone(NY).date()
            by_day.setdefault(day, 0.0)
            by_day[day] += float(row.volume or 0.0)
        current = by_day.get(current_date, 0.0)
        history = [value for day, value in sorted(by_day.items()) if day < current_date and value > 0]
        baseline = median(history[-5:]) if history else 0.0
        return 1.0 if baseline <= 0 else current / baseline

    @staticmethod
    def _amihud_proxy(rows: Sequence[Bar]) -> float:
        values = []
        for previous, current in zip(rows, rows[1:]):
            notional = current.close * float(current.volume or 0.0)
            if previous.close <= 0 or notional <= 0:
                continue
            values.append(abs(current.close / previous.close - 1.0) / notional)
        return 0.0 if not values else sum(values) / len(values)
