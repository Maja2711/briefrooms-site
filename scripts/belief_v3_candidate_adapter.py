"""Shadow-only evidence adapter for Belief Core v3 READY candidates.

Only candidates backed by already available market proxies are wired here.
Candidates that require new/real sources remain absent by construction.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from belief_adapter_contract import (
    AdapterResult,
    EvidenceAssessment,
    Observation,
    clamp,
    observation_to_evidence,
    strength_from_return,
)
from belief_core import BeliefDefinition, iso_z
from belief_market_data_adapter import MarketSnapshot
from belief_v3_candidate_library import V3_CANDIDATE_LIBRARY, V3_GOVERNANCE

READY_CANDIDATE_IDS: Tuple[str, ...] = tuple(
    row["belief_id"]
    for row in V3_CANDIDATE_LIBRARY
    if row["source_status"] == "READY_EXISTING_MARKET_PROXY"
)
WAITING_CANDIDATE_IDS: Tuple[str, ...] = tuple(
    row["belief_id"]
    for row in V3_CANDIDATE_LIBRARY
    if row["source_status"] != "READY_EXISTING_MARKET_PROXY"
)

_ROWS = {row["belief_id"]: row for row in V3_CANDIDATE_LIBRARY}

CANDIDATE_DEFINITIONS: Tuple[BeliefDefinition, ...] = tuple(
    BeliefDefinition(
        belief_id=row["belief_id"],
        claim=row["claim"],
        prior_probability=.50,
        half_life_hours=24,
        entity=row["entity"],
        domain=row["domain"],
        tags=("v3_candidate", "shadow", "challenger"),
        horizon_hours=24,
        outcome_rule=row["outcome_rule"],
    )
    for row in V3_CANDIDATE_LIBRARY
    if row["belief_id"] in READY_CANDIDATE_IDS
)


def candidate_market_symbol(belief_id: str) -> str:
    if belief_id.startswith("spx."):
        return "SPY"
    if belief_id.startswith("eurusd."):
        return "EURUSD=X"
    if belief_id.startswith("btc."):
        return "BTC-USD"
    raise KeyError(belief_id)


def candidate_outcome_spec(belief_id: str, snapshot: MarketSnapshot) -> Dict[str, object]:
    """Resolve every wired candidate against its target asset, prospectively.

    The candidate probability asks whether the declared environment is
    supportive for the target asset into the horizon; the binary outcome is
    therefore whether that target asset is higher at T1 than at frozen T0.
    """
    if belief_id not in READY_CANDIDATE_IDS:
        raise KeyError(belief_id)
    symbol = candidate_market_symbol(belief_id)
    if symbol not in snapshot.bars:
        raise KeyError(symbol)
    return {"kind": "price_above", "symbol": symbol, "reference": snapshot.latest(symbol)}


class V3CandidateEvidenceAdapter:
    name = "belief_v3_candidate_market_proxy"
    version = "1.0.0"

    def run(self, snapshot: MarketSnapshot) -> AdapterResult:
        observed_at = iso_z(snapshot.observed_at("SPY"))
        observations: List[Observation] = []
        evidence = []

        def add(metric: str, entity: str, value: float, unit: str, cluster: str, source_ref: str) -> Observation:
            row = Observation.make(
                adapter=self.name,
                metric=metric,
                entity=entity,
                observed_at=observed_at,
                value=value,
                unit=unit,
                source="Derived from Yahoo Finance OHLCV",
                source_type="derived",
                source_ref=source_ref,
                reliability=.74,
                independence_cluster=cluster,
                tags=("belief_v3_candidate", "market_proxy", self.version),
                metadata={
                    "candidate_library_version": V3_GOVERNANCE["library_version"],
                    "shadow_only": True,
                    "production_write_authority": False,
                },
            )
            observations.append(row)
            return row

        tlt = snapshot.return_over_bars("TLT", 13)
        credit = snapshot.ratio_return("HYG", "LQD", 13)
        spy = snapshot.return_over_bars("SPY", 13)
        uup = snapshot.return_over_bars("UUP", 13)
        vix_level = snapshot.latest("^VIX")
        vix_ret = snapshot.return_over_bars("^VIX", 13)

        tlt_obs = add("tlt_return_1d", "TLT", tlt, "return", "v3:market:TLT:rates", f"derived:yahoo:TLT:{observed_at}:v3")
        credit_obs = add("hyg_lqd_relative_1d", "HYG/LQD", credit, "return", "v3:market:HYG-LQD:credit", f"derived:yahoo:HYG-LQD:{observed_at}:v3")
        spy_obs = add("spy_return_1d", "SPY", spy, "return", "v3:market:SPY:risk", f"derived:yahoo:SPY:{observed_at}:v3")
        uup_obs = add("uup_return_1d", "UUP", uup, "return", "v3:market:UUP:usd", f"derived:yahoo:UUP:{observed_at}:v3")
        vix_score = .65 * ((20.0 - vix_level) / 8.0) + .35 * (-vix_ret / .15)
        vix_obs = add("vix_support_score", "VIX", vix_score, "score", "v3:market:VIX:risk", f"derived:yahoo:VIX:{observed_at}:v3")

        def ev(obs: Observation, belief_id: str, direction: int, strength: float, evidence_type: str, note: str):
            evidence.append(observation_to_evidence(
                obs,
                EvidenceAssessment(
                    belief_id,
                    direction,
                    clamp(strength, .08, 1.0),
                    evidence_type,
                    note,
                ),
            ))

        # Candidate 1: rates proxy for SPX. TLT up is treated as easing long-rate
        # pressure. This is deliberately a single-proxy candidate until tested.
        ev(
            tlt_obs,
            "spx.rates.supportive",
            1 if tlt >= 0 else -1,
            strength_from_return(tlt, .015),
            "rates_proxy",
            f"TLT 1d return={tlt:.4%}",
        )

        # Cross-asset candidates use the same point-in-time observations but
        # remain separate beliefs with separate prospective outcomes.
        for belief_id in ("spx.cross_asset_risk.supportive", "btc.cross_asset_risk.supportive"):
            ev(credit_obs, belief_id, 1 if credit >= 0 else -1, strength_from_return(credit, .008), "credit", f"HYG/LQD 1d={credit:.4%}")
            ev(spy_obs, belief_id, 1 if spy >= 0 else -1, strength_from_return(spy, .015), "equity_risk", f"SPY 1d={spy:.4%}")
            ev(uup_obs, belief_id, 1 if uup <= 0 else -1, strength_from_return(uup, .008), "usd", f"UUP 1d={uup:.4%}")
            ev(vix_obs, belief_id, 1 if vix_score >= 0 else -1, abs(vix_score), "volatility", f"VIX={vix_level:.2f}; 1d={vix_ret:.2%}")
            ev(tlt_obs, belief_id, 1 if tlt >= 0 else -1, strength_from_return(tlt, .015), "duration", f"TLT 1d={tlt:.4%}")

        # EUR/USD risk-regime candidate deliberately excludes TLT to avoid
        # pretending that a US duration proxy is the missing EUR rate curve.
        belief_id = "eurusd.risk_regime.supportive"
        ev(credit_obs, belief_id, 1 if credit >= 0 else -1, strength_from_return(credit, .008), "credit", f"HYG/LQD 1d={credit:.4%}")
        ev(spy_obs, belief_id, 1 if spy >= 0 else -1, strength_from_return(spy, .015), "equity_risk", f"SPY 1d={spy:.4%}")
        ev(uup_obs, belief_id, 1 if uup <= 0 else -1, strength_from_return(uup, .008), "usd", f"UUP 1d={uup:.4%}")
        ev(vix_obs, belief_id, 1 if vix_score >= 0 else -1, abs(vix_score), "volatility", f"VIX={vix_level:.2f}; 1d={vix_ret:.2%}")

        return AdapterResult(self.name, tuple(observations), tuple(evidence))
