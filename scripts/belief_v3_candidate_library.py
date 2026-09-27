"""Belief Core v3 candidate library.

Research-only registry layered beside the frozen Belief Core v2 library.
No candidate has production, policy, sizing, execution, tuning or promotion authority.
A candidate may collect forecasts only after its declared evidence source is available.
"""
from __future__ import annotations

V3_CANDIDATE_LIBRARY = (
    {"belief_id":"spx.rates.supportive","entity":"SPX","domain":"rates","claim":"US rates conditions are supportive for SPX into the target horizon","source_status":"READY_EXISTING_MARKET_PROXY","required_evidence":["US duration/rates market evidence"],"outcome_rule":"rates_supportive_at_target"},
    {"belief_id":"spx.cross_asset_risk.supportive","entity":"SPX","domain":"cross_asset","claim":"Cross-asset risk conditions confirm a supportive SPX environment into the target horizon","source_status":"READY_EXISTING_MARKET_PROXY","required_evidence":["credit","duration","USD","equity risk"],"outcome_rule":"cross_asset_risk_supportive_at_target"},
    {"belief_id":"eurusd.rate_differential.supportive","entity":"EURUSD","domain":"rate_differential","claim":"EUR-vs-USD rate differential is supportive for EUR/USD into the target horizon","source_status":"WAITING_REAL_SOURCE","required_evidence":["EUR rate curve","USD rate curve"],"outcome_rule":"eur_us_rate_differential_supportive_at_target"},
    {"belief_id":"eurusd.ecb_policy.supportive","entity":"EURUSD","domain":"ecb_policy","claim":"ECB policy expectations are supportive for EUR/USD into the target horizon","source_status":"WAITING_REAL_SOURCE","required_evidence":["ECB policy expectations"],"outcome_rule":"ecb_policy_supportive_at_target"},
    {"belief_id":"eurusd.euro_macro.supportive","entity":"EURUSD","domain":"euro_macro","claim":"Euro-area macro conditions are supportive for EUR/USD into the target horizon","source_status":"WAITING_REAL_SOURCE","required_evidence":["euro-area macro surprise"],"outcome_rule":"euro_macro_supportive_at_target"},
    {"belief_id":"eurusd.risk_regime.supportive","entity":"EURUSD","domain":"risk_regime","claim":"Global risk regime is supportive for EUR/USD into the target horizon","source_status":"READY_EXISTING_MARKET_PROXY","required_evidence":["equity risk","credit","volatility","USD"],"outcome_rule":"risk_regime_supportive_at_target"},
    {"belief_id":"btc.derivatives_positioning.supportive","entity":"BTC","domain":"crypto_derivatives","claim":"Crypto derivatives positioning is supportive and not excessively crowded into the target horizon","source_status":"WAITING_REAL_SOURCE","required_evidence":["funding","open interest","basis","liquidations"],"outcome_rule":"crypto_derivatives_supportive_at_target"},
    {"belief_id":"btc.exchange_flows.supportive","entity":"BTC","domain":"exchange_flows","claim":"Bitcoin exchange flows are supportive into the target horizon","source_status":"WAITING_REAL_SOURCE","required_evidence":["BTC exchange inflows/outflows"],"outcome_rule":"exchange_flows_supportive_at_target"},
    {"belief_id":"btc.stablecoin_liquidity.supportive","entity":"BTC","domain":"stablecoin_liquidity","claim":"Stablecoin liquidity is supportive for BTC into the target horizon","source_status":"WAITING_REAL_SOURCE","required_evidence":["stablecoin supply/flows"],"outcome_rule":"stablecoin_liquidity_supportive_at_target"},
    {"belief_id":"btc.onchain_flows.supportive","entity":"BTC","domain":"onchain","claim":"Bitcoin on-chain flows are supportive into the target horizon","source_status":"WAITING_REAL_SOURCE","required_evidence":["on-chain flow metrics"],"outcome_rule":"onchain_flows_supportive_at_target"},
    {"belief_id":"btc.cross_asset_risk.supportive","entity":"BTC","domain":"cross_asset","claim":"Cross-asset risk appetite is supportive for BTC into the target horizon","source_status":"READY_EXISTING_MARKET_PROXY","required_evidence":["equity risk","credit","duration","USD"],"outcome_rule":"cross_asset_risk_supportive_at_target"},
)

V3_CANDIDATE_IDS = tuple(x["belief_id"] for x in V3_CANDIDATE_LIBRARY)

V3_GOVERNANCE = {
    "library_version": "belief-core-v3-candidate-library-v1",
    "stage": "SHADOW",
    "production_decision_influence": False,
    "production_write_authority": False,
    "automatic_tuning": False,
    "automatic_promotion": False,
    "historical_backfill": False,
    "minimum_sample_for_review": 50,
    "incremental_information_required_after_minimum_sample": True,
    "forecast_contract": {
        "required": True,
        "t0_required": True,
        "probability_required": True,
        "t1_required_for_resolution": True,
        "scores": ["brier","ece","log_loss"],
        "calibration_curve": True,
        "lab_breakdown": True,
    },
    "promotion_rule": "candidate_only_manual_production_decision",
}


def public_candidate_registry():
    return [
        {
            **row,
            "stage":"SHADOW",
            "sample_n":0,
            "brier":None,
            "ece":None,
            "log_loss":None,
            "incremental_information":None,
            "review_status":"WAITING_FOR_EVIDENCE" if row["source_status"] != "READY_EXISTING_MARKET_PROXY" else "READY_TO_WIRE",
            "production_recommendation":"NIE OCENIAĆ",
            "production_write_authority":False,
            "automatic_promotion":False,
        }
        for row in V3_CANDIDATE_LIBRARY
    ]
