from __future__ import annotations

from typing import List

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, clamp, observation_to_evidence, strength_from_return
from belief_core import iso_z
from belief_market_data_adapter import MarketSnapshot


class RegimeCrossAssetAdapter:
    name = "regime_cross_asset"
    version = "1.0.0"

    def run(self, snapshot: MarketSnapshot) -> AdapterResult:
        observed_at = iso_z(snapshot.observed_at("SPY"))
        obs: List[Observation] = []
        def add(metric: str, entity: str, value, unit: str, cluster: str, source_ref: str, metadata=None) -> Observation:
            row = Observation.make(adapter=self.name, metric=metric, entity=entity, observed_at=observed_at,
                value=value, unit=unit, source="Derived cross-asset view from Yahoo Finance charts",
                source_type="derived", source_ref=source_ref, reliability=.77,
                independence_cluster=cluster, tags=("regime", "cross_asset", self.version), metadata=metadata)
            obs.append(row); return row

        rsp_rel = snapshot.ratio_return("RSP", "SPY", 13)
        iwm_rel = snapshot.ratio_return("IWM", "SPY", 13)
        vix = snapshot.latest("^VIX")
        vix_1d = snapshot.return_over_bars("^VIX", 13)
        credit = snapshot.ratio_return("HYG", "LQD", 13)
        tlt = snapshot.return_over_bars("TLT", 13)
        uup = snapshot.return_over_bars("UUP", 13)
        hyg = snapshot.return_over_bars("HYG", 13)

        rsp_obs = add("rsp_spy_relative_1d", "RSP/SPY", rsp_rel, "return", "market:RSP-SPY:breadth", f"derived:yahoo:RSP-SPY:{observed_at}")
        iwm_obs = add("iwm_spy_relative_1d", "IWM/SPY", iwm_rel, "return", "market:IWM-SPY:breadth", f"derived:yahoo:IWM-SPY:{observed_at}")
        vix_obs = add("vix_level_change", "VIX", {"level":vix,"return_1d":vix_1d}, "compound", "market:VIX:vol", f"derived:yahoo:VIX:{observed_at}")
        tlt_obs = add("tlt_return_1d", "TLT", tlt, "return", "market:TLT:rates", f"derived:yahoo:TLT:{observed_at}")
        uup_obs = add("uup_return_1d", "UUP", uup, "return", "market:UUP:usd", f"derived:yahoo:UUP:{observed_at}")
        hyg_obs = add("hyg_return_1d", "HYG", hyg, "return", "market:HYG:macro-credit", f"derived:yahoo:HYG:{observed_at}:macro")
        add("hyg_lqd_relative_1d", "HYG/LQD", credit, "return", "market:HYG-LQD:credit", f"derived:yahoo:HYG-LQD:{observed_at}:regime")

        regime = self.classify(snapshot)
        add("regime_score", "US_RISK", self.regime_score(snapshot), "score", "derived:US:regime", f"derived:regime:{observed_at}", {"regime":regime})
        add("regime_label", "US_RISK", regime, "label", "derived:US:regime", f"derived:regime:{observed_at}")

        vol_score = .65*((20.0-vix)/8.0) + .35*(-vix_1d/.15)
        evidence = [
            observation_to_evidence(rsp_obs, EvidenceAssessment("spx.breadth.healthy",1 if rsp_rel>=0 else -1,strength_from_return(rsp_rel,.012),"breadth",f"RSP/SPY 1d relative return={rsp_rel:.4%}")),
            observation_to_evidence(iwm_obs, EvidenceAssessment("spx.breadth.healthy",1 if iwm_rel>=0 else -1,strength_from_return(iwm_rel,.018),"breadth",f"IWM/SPY 1d relative return={iwm_rel:.4%}")),
            observation_to_evidence(vix_obs, EvidenceAssessment("spx.volatility.benign",1 if vol_score>=0 else -1,clamp(abs(vol_score),.10,1.0),"volatility",f"VIX={vix:.2f}, 1d change={vix_1d:.2%}")),
            observation_to_evidence(tlt_obs, EvidenceAssessment("spx.financial_conditions.supportive",1 if tlt>=0 else -1,strength_from_return(tlt,.015),"rates",f"TLT 1d return={tlt:.4%}")),
            observation_to_evidence(uup_obs, EvidenceAssessment("spx.financial_conditions.supportive",1 if uup<=0 else -1,strength_from_return(uup,.008),"usd",f"UUP 1d return={uup:.4%}; weaker USD treated as supportive")),
            observation_to_evidence(hyg_obs, EvidenceAssessment("spx.financial_conditions.supportive",1 if hyg>=0 else -1,strength_from_return(hyg,.012),"credit_liquidity",f"HYG 1d return={hyg:.4%}")),
        ]
        return AdapterResult(self.name, tuple(obs), tuple(evidence))

    @staticmethod
    def classify(snapshot: MarketSnapshot) -> str:
        vix=snapshot.latest("^VIX"); spy=snapshot.return_over_bars("SPY",13); credit=snapshot.ratio_return("HYG","LQD",13)
        if vix >= 28: return "high_vol"
        if spy <= -.012 and credit < 0: return "risk_off"
        if vix < 18 and spy > 0 and credit >= 0: return "risk_on"
        return "neutral"

    @staticmethod
    def regime_score(snapshot: MarketSnapshot) -> float:
        vix=snapshot.latest("^VIX"); spy=snapshot.return_over_bars("SPY",13); breadth=snapshot.ratio_return("RSP","SPY",13)
        credit=snapshot.ratio_return("HYG","LQD",13); uup=snapshot.return_over_bars("UUP",13)
        return clamp(.30*clamp(spy/.015,-1,1)+.20*clamp(breadth/.012,-1,1)+.20*clamp(credit/.008,-1,1)+.20*clamp((20-vix)/8,-1,1)+.10*clamp(-uup/.008,-1,1),-1,1)
