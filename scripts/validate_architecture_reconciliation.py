#!/usr/bin/env python3
"""Semantic Architecture Reconciliation 1.28 guard.

Checks runtime facts that a version-only documentation check cannot protect:
active phase/status, branch authority, Trigger wiring, WES NO_TRADE, and
single-writer Stock Trading production promotion.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def read(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        errors.append(f"missing required file: {path}")
        return ""
    return target.read_text(encoding="utf-8")


def load_json(path: str) -> dict:
    try:
        value = json.loads(read(path))
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must contain a JSON object")
        return {}
    return value


def require(text: str, token: str, label: str) -> None:
    if token not in text:
        errors.append(f"{label} missing required token: {token}")


def forbid(text: str, token: str, label: str) -> None:
    if token in text:
        errors.append(f"{label} contains forbidden token: {token}")


def git_show(ref: str, path: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{path}"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        errors.append(f"cannot read {ref}:{path}: {exc.output.strip()}")
        return ""


parser = argparse.ArgumentParser()
parser.add_argument("--research-ref", default="origin/stock-trading-v2")
args = parser.parse_args()

pl_map = read("docs/ARCHITECTURE_MAP_PL.md")
en_map = read("docs/ARCHITECTURE_MAP_EN.md")
stock_doc = read("docs/stock-trading-v2-architecture.md")
weekly_doc = read("docs/weekly_trading_methodology_v4.md")
gse_doc = read("docs/GEOPOLITICAL_SCENARIO_ENGINE.md")
auto_doc = read("docs/AUTONOMOUS_POLICY_PROMOTION_V1.md")
agents = read("AGENTS.md")

stock_state = load_json("data/investments/stock_trading_v2_production_state.json")
brace_registry = load_json("data/portfolio10k/methodology_registry.json")
weekly_policy = load_json("data/investments/multi_instrument_exposure_policy.json")
auto_cfg = load_json("data/investments/autonomous_policy_closed_loop_config.json")

main_discovery = read(".github/workflows/stock-trading-v2-continuous-discovery.yml")
main_learning = read(".github/workflows/stock-trading-v2-learning-loop.yml")
main_validation = read(".github/workflows/stock-trading-v2-validation.yml")
main_component_promotion = read(".github/workflows/stock-trading-component-promotion.yml")
legacy_closed_loop = read(".github/workflows/autonomous-policy-closed-loop.yml")

if stock_state.get("phase") != "FULL":
    errors.append(f"Stock Trading v2 phase is not FULL: {stock_state.get('phase')}")
if stock_state.get("champion_engine") != "v2":
    errors.append("Stock Trading v2 Champion runtime is not v2")
if brace_registry.get("controller_state") != "PROBATIONARY_CONTROL":
    errors.append(f"BRACE controller state drift: {brace_registry.get('controller_state')}")
if weekly_policy.get("mandatory_monday_position") is not False:
    errors.append("WES mandatory_monday_position must remain false")
if weekly_policy.get("continuous_position_required") is not False:
    errors.append("WES continuous_position_required must remain false")
if ((weekly_policy.get("no_trade") or {}).get("enabled")) is not True:
    errors.append("WES NO_TRADE gate must remain enabled")
directional = weekly_policy.get("directional_admission") or {}
if directional.get("version") != "WES-1.3.1":
    errors.append("WES directional admission version must be WES-1.3.1")
if directional.get("require_for_all_new_entries") is not True:
    errors.append("WES 1.3.1 must require directional admission for all new entries")
cc = ((weekly_policy.get("strategy_tournament") or {}).get("champion_challenger") or {})
if "inverse_v2" not in set(cc.get("challenger_shadow_methods") or []):
    errors.append("WES inverse_v2 must remain Challenger/Shadow")
if "inverse_v2" in set(cc.get("execution_methods") or []):
    errors.append("WES inverse_v2 regained execution authority without promotion")
if cc.get("challenger_execution_enabled") is not False:
    errors.append("WES Challenger execution must remain disabled")
entry_engine = weekly_policy.get("entry_price_engine") or {}
if entry_engine.get("version") != "WES-1.3.1":
    errors.append("WES entry price engine version must be WES-1.3.1")
if entry_engine.get("require_for_all_new_entries") is not True:
    errors.append("WES 1.3.1 must require an execution plan for all new entries")
if entry_engine.get("order_style") != "adaptive_market_or_price_improving_limit":
    errors.append("WES 1.3.1 entry execution must remain adaptive MARKET-or-LIMIT")
if entry_engine.get("target_refresh_policy") != "keep_limit_price_frozen_reaffirm_time_only_cancel_on_thesis_break_or_promote_same_thesis_to_market":
    errors.append("WES 1.3.1 LIMIT target must stay frozen while same-thesis persistence extends time only")
persistent_plan = entry_engine.get("persistent_plan") or {}
if persistent_plan.get("enabled") is not True or persistent_plan.get("preserve_frozen_target") is not True:
    errors.append("WES 1.3.1 persistent LIMIT plan must remain enabled with frozen-target preservation")
market_entry = entry_engine.get("market_entry") or {}
if market_entry.get("enabled") is not True:
    errors.append("WES 1.3.1 strong-trend MARKET entry must remain enabled")
scoring = market_entry.get("scoring") or {}
if float(scoring.get("minimum_score") or 0.0) <= 0:
    errors.append("WES 1.3.1 MARKET/LIMIT composite scoring threshold missing")
if scoring.get("model") != "weekly_primary_daily_confirmation_modifier":
    errors.append("WES 1.3.1 execution scoring must remain Weekly-primary with Daily modifier")
primary_weights = scoring.get("primary_weights") or {}
if "daily" in primary_weights:
    errors.append("WES 1.3.1 Daily must not regain a primary MARKET/LIMIT weight")
if float(primary_weights.get("weekly") or 0.0) <= 0:
    errors.append("WES 1.3.1 Weekly primary execution weight missing")
daily_modifier = scoring.get("daily_confirmation_modifier") or {}
if float(daily_modifier.get("aligned_bonus_max") or 0.0) > 0.10:
    errors.append("WES 1.3.1 Daily aligned bonus exceeds bounded modifier contract")
if float(daily_modifier.get("opposed_penalty_max") or 0.0) > 0.12:
    errors.append("WES 1.3.1 Daily opposed penalty exceeds bounded modifier contract")
if market_entry.get("require_weekly_primary_alignment") is not True:
    errors.append("WES 1.3.1 MARKET entry must require Weekly-primary alignment")
if market_entry.get("block_strong_daily_opposition") is not True:
    errors.append("WES 1.3.1 strong opposed Daily veto must remain enabled")
if market_entry.get("block_opposed_momentum") is not True:
    errors.append("WES 1.3.1 MARKET entry must hard-block opposed momentum")
if market_entry.get("allow_limit_to_market_promotion") is not True:
    errors.append("WES 1.3.1 same-thesis LIMIT-to-MARKET promotion must remain enabled")
if market_entry.get("block_immediate_market_after_stop") is not True:
    errors.append("WES 1.3.1 immediate MARKET re-entry after stop must remain blocked")
delta_bypass = directional.get("absolute_strength_delta_bypass") or {}
if delta_bypass.get("enabled") is not True or delta_bypass.get("require_daily_weekly_confirmation") is not True:
    errors.append("WES 1.3.1 absolute-strength delta bypass must require Daily+Weekly confirmation")
watchdog = weekly_policy.get("wes_cycle_watchdog") or {}
if watchdog.get("enabled") is not True or watchdog.get("auto_dispatch_recovery") is not True:
    errors.append("WES 1.3.1 independent cycle watchdog and recovery dispatch must remain enabled")
if auto_cfg.get("automatic_materialization_enabled") is not False:
    errors.append("legacy Autonomous Policy Loop regained Stock Trading materialization authority")
if auto_cfg.get("production_authority") != "RETIRED_TO_STOCK_TRADING_COMPONENT_PROMOTION":
    errors.append("legacy Autonomous Policy Loop retired-authority marker missing")

for token in (
    "briefrooms_market_relationship_trigger.py",
    "briefrooms_trigger_deep_belief.py",
    "market_relationship_trigger_config.json",
):
    require(main_discovery, token, "main continuous discovery")
for token in (
    "briefrooms_market_relationship_outcomes.py",
    "briefrooms_trigger_deep_belief_learning.py",
    "stock_trading_v2_challenger_factory.py",
):
    require(main_learning, token, "main closed learning loop")
forbid(main_learning, "stock_trading_v2_auto_promote.py", "main closed learning loop")
forbid(main_learning, "push origin HEAD:main", "main closed learning loop")

require(main_component_promotion, "stock_trading_component_bound_promotion.py", "component promotion")
require(main_component_promotion, "Check out production Champion", "component promotion")
require(legacy_closed_loop, "LEGACY_STOCK_POLICY_PRODUCTION_WRITE_DISABLED", "legacy closed loop")
require(legacy_closed_loop, "contents: read", "legacy closed loop")
forbid(legacy_closed_loop, "Apply statistically proven autonomous policy calibration", "legacy closed loop")
forbid(legacy_closed_loop, "git push origin HEAD:main", "legacy closed loop")

for token in ("**Wersja mapy:** 1.28", "IN-08", "EP-09", "LE-10", "LE-11", "L3-04", "PROBATIONARY_CONTROL", "NO_TRADE", "FULL", "WES 1.3.1"):
    require(pl_map, token, "PL Architecture Map")
for token in ("**Map version:** 1.28", "IN-08", "EP-09", "LE-10", "LE-11", "L3-04", "PROBATIONARY_CONTROL", "NO_TRADE", "FULL", "WES 1.3.1"):
    require(en_map, token, "EN Architecture Map")
require(stock_doc, "PRODUCTION CHAMPION — FULL", "Stock Trading architecture")
require(stock_doc, "Market Relationship / Trigger", "Stock Trading architecture")
require(stock_doc, "stock-trading-v2", "Stock Trading branch authority")
require(weekly_doc, "NO_TRADE", "Weekly methodology")
require(weekly_doc, "WES 1.3.1", "Weekly methodology")
require(weekly_doc, "Adaptive MARKET versus LIMIT decision", "Weekly methodology")
require(weekly_doc, "inverse_v2", "Weekly methodology")
require(gse_doc, "Active v2 runtime", "GSE architecture")
require(auto_doc, "Production materialization from this legacy PR35/PR36 loop is retired", "Autonomous Policy docs")
require(agents, "stock-trading-v2", "root AGENTS branch authority")
require(agents, "Instrument-scoped change isolation", "root AGENTS scoped-change authority")

daily_doc = read("docs/DAILY_TRADING_ARCHITECTURE.md")
daily_runtime = read("scripts/daily_eurusd_spot_v19.py")
daily_decision = read("scripts/daily_eurusd_belief_decision.py")
daily_workflow = read(".github/workflows/daily-eurusd-monitor.yml")
daily_realtime_workflow = read(".github/workflows/daily-eurusd-realtime-lifecycle.yml")
daily_cycle_watchdog = read("scripts/daily_eurusd_cycle_watchdog.py")
daily_cycle_watchdog_workflow = read(".github/workflows/daily-eurusd-cycle-watchdog.yml")
weekly_workflow = read(".github/workflows/investments-weekly.yml")
weekly_wes_workflow = read(".github/workflows/investments-wes.yml")
weekly_risk_workflow = read(".github/workflows/investments-exposure-watch.yml")
weekly_freshness_workflow = read(".github/workflows/investments-weekly-freshness-watchdog.yml")
wes_cycle_watchdog_workflow = read(".github/workflows/wes-cycle-watchdog.yml")
investment_quotes_workflow = read(".github/workflows/investment-room-quotes.yml")
belief_live_workflow = read(".github/workflows/belief-core-shadow-live.yml")
belief_events_workflow = read(".github/workflows/belief-core-events-live.yml")
epistemic_projection_workflow = read(".github/workflows/belief-epistemic-state.yml")
require(wes_cycle_watchdog_workflow, "scripts/wes_cycle_watchdog.py", "WES 1.3.1 cycle watchdog")
require(wes_cycle_watchdog_workflow, "actions/workflows/investments-wes.yml/dispatches", "WES 1.3.1 automatic recovery dispatch")
require(daily_cycle_watchdog, "DEFAULT_MAX_AGE_MINUTES = 15", "Daily EURUSD 15-minute liveness threshold")
require(daily_cycle_watchdog, "EXPECTED_ENGINE_VERSION = \"eurusd-daily-spot-v1.9.0\"", "Daily EURUSD watchdog engine-version contract")
require(daily_cycle_watchdog_workflow, "scripts/daily_eurusd_cycle_watchdog.py", "Daily EURUSD cycle watchdog")
require(daily_cycle_watchdog_workflow, "daily-eurusd-monitor.yml", "Daily EURUSD automatic recovery target")
require(daily_cycle_watchdog_workflow, "actions/workflows/${workflow}/dispatches", "Daily EURUSD automatic recovery dispatch")
require(daily_cycle_watchdog_workflow, "belief-core-shadow-live.yml", "Daily EURUSD upstream Belief recovery dispatch")
require(investment_quotes_workflow, "belief-core-shadow-live.yml/dispatches", "Quotes-to-Belief explicit dispatch")
require(epistemic_projection_workflow, "workflow_run:", "Belief-to-Epistemic workflow_run chain")
require(epistemic_projection_workflow, "Belief Core Live Shadow Collection", "Belief-live-to-Epistemic workflow_run source")
require(epistemic_projection_workflow, "Belief Core News Macro Shadow Collection", "Belief-macro-to-Epistemic workflow_run source")
forbid(belief_live_workflow, "belief-epistemic-state.yml/dispatches", "Belief-live duplicate Epistemic dispatch")
forbid(belief_events_workflow, "belief-epistemic-state.yml/dispatches", "Belief-macro duplicate Epistemic dispatch")
require(epistemic_projection_workflow, 'BELIEF_INPUT_MAX_AGE_MINUTES: "45"', "Epistemic recovery fresh Belief input ceiling")
require(epistemic_projection_workflow, "daily-eurusd-monitor.yml/dispatches", "Epistemic-to-Daily explicit dispatch")
weekly_live_prices_workflow = read(".github/workflows/weekly-live-prices.yml")
stock_v2_workflow = read(".github/workflows/stock-trading-v2-production.yml")
stock_portfolio_workflow = read(".github/workflows/stock-trading-portfolio.yml")
event_intelligence_workflow = read(".github/workflows/investment-event-intelligence-production.yml")
push_sync_script = read("scripts/sync_trading_push_commit.sh")
daily_fast_lifecycle = read("scripts/daily_eurusd_fast_lifecycle.py")
trading_push_worker = read("workers/trading-push/src/index.js")
epistemic_consumer = read("scripts/epistemic_consumer_interface.py")
for token in (
    "Daily EUR/USD v1.9 — Belief-first final decision",
    "RAW / PRIMARY / MARKET SOURCES",
    "BELIEF CORE",
    "FINAL LONG / SHORT / FLAT",
    "zero v1.9 production direction **and timing** authority",
):
    require(daily_doc, token, "Daily EURUSD v1.9 architecture")
for token in (
    "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE",
    "BELIEF_FIRST_V1",
    '"FSE": {"production_direction_authority": False, "production_timing_authority": False}',
    "SHADOW_AFTER_BELIEF_FIRST_MIGRATION",
):
    require(daily_runtime, token, "Daily EURUSD v1.9 runtime")
for token in (
    "RAW_SOURCES",
    "SPECIALIZED_ADAPTERS",
    "EVIDENCE_ASSESSMENT",
    "BELIEF_CORE",
    "epistemic-consumer-interface-v1",
    "DAILY_EURUSD",
    "epistemic_aggregate_authoritative",
    "legacy_raw_score_direction_authority",
):
    require(daily_decision, token, "Daily EURUSD Belief decision")
require(epistemic_consumer, '"DAILY_EURUSD": EURUSD_BELIEF_IDS', "CF-07 Daily EURUSD consumer")
require(epistemic_consumer, "EURUSD_PROFILE_WEIGHTS", "CF-07 Daily EURUSD aggregate")
require(epistemic_consumer, "authoritative_daily_eurusd_epistemic_projection", "CF-07 Daily EURUSD aggregate")
require(epistemic_consumer, "consumer_may_override_probability: bool = False", "CF-07 authority")
require(daily_workflow, "scripts/daily_eurusd_spot_v19.py", "Daily EURUSD production workflow")
require(daily_workflow, "BELIEF_EPISTEMIC_STATE=", "Daily EURUSD production workflow")
require(daily_workflow, "DAILY_EURUSD_REQUIRE_EPISTEMIC=1", "Daily EURUSD production workflow")
require(daily_workflow, "EURUSD_V19_BELIEF_FIRST_OK", "Daily EURUSD production workflow")
require(daily_doc, "### Realtime open-position lifecycle", "Daily EURUSD realtime architecture")
require(daily_fast_lifecycle, '"entry_authority": False', "Daily EURUSD realtime exit-only runtime")
require(daily_fast_lifecycle, '"direction_authority": False', "Daily EURUSD realtime exit-only runtime")
require(daily_fast_lifecycle, "v18._evaluate_position", "Daily EURUSD realtime lifecycle rule reuse")
require(daily_realtime_workflow, "Watch persisted OPEN position every five seconds", "Daily EURUSD realtime workflow")
require(daily_realtime_workflow, "scripts/daily_eurusd_realtime_watch.sh", "Daily EURUSD realtime workflow")
require(daily_workflow, "sync_trading_push_commit.sh", "Daily EURUSD immediate notification handoff")
require(daily_workflow, "daily", "Daily EURUSD notification channel")
require(daily_workflow, "daily-eurusd-realtime-lifecycle.yml", "Daily EURUSD watcher recovery")
require(trading_push_worker, "async syncTradingCommit(commitSha, requestedEngines)", "Trading push all-channel commit-bound sync")
require(trading_push_worker, "transitionDescriptors", "Trading push exact commit-parent transition diff")
require(trading_push_worker, "direct_ingest_disabled_use_commit_sync", "Trading push injection boundary")
require(trading_push_worker, 'url.hostname === "internal" && path === "/ingest"', "Trading push internal-only recovery ingest")
require(push_sync_script, "/sync-trading", "Shared trading push handoff")
for workflow_text, label in (
    (weekly_workflow, "Weekly maintenance immediate notification"),
    (weekly_wes_workflow, "WES immediate notification"),
    (weekly_risk_workflow, "Weekly risk immediate notification"),
    (weekly_freshness_workflow, "Weekly freshness recovery immediate notification"),
    (weekly_live_prices_workflow, "Weekly live-price/risk immediate notification"),
):
    require(workflow_text, "sync_trading_push_commit.sh", label)
    require(workflow_text, "weekly", label)
for workflow_text, label in (
    (stock_v2_workflow, "Stock v2 admission immediate notification"),
    (stock_portfolio_workflow, "Stock portfolio lifecycle immediate notification"),
):
    require(workflow_text, "sync_trading_push_commit.sh", label)
    require(workflow_text, "stock", label)
require(event_intelligence_workflow, "sync_trading_push_commit.sh", "Event Intelligence immediate notification")
require(event_intelligence_workflow, "weekly", "Event Intelligence Weekly notification")
require(event_intelligence_workflow, "stock", "Event Intelligence Stock notification")
require(belief_live_workflow, "belief_core_live.py", "Belief EURUSD live collector")
forbid(daily_workflow, "python scripts/daily_eurusd_spot_v18.py", "Daily EURUSD production workflow")

research_agents = git_show(args.research_ref, "AGENTS.md")
research_discovery = git_show(args.research_ref, ".github/workflows/stock-trading-v2-continuous-discovery.yml")
research_learning = git_show(args.research_ref, ".github/workflows/stock-trading-v2-learning-loop.yml")
research_validation = git_show(args.research_ref, ".github/workflows/stock-trading-v2-validation.yml")

require(research_agents, "research/evidence runtime branch", "research AGENTS")
forbid(research_learning, "stock_trading_v2_auto_promote.py", "research learning loop")
forbid(research_learning, "push origin HEAD:main", "research learning loop")
require(research_discovery, "briefrooms_market_relationship_trigger.py", "research discovery")
require(research_learning, "briefrooms_trigger_deep_belief_learning.py", "research learning")

for label, main_text, research_text in (
    ("continuous discovery", main_discovery, research_discovery),
    ("closed learning loop", main_learning, research_learning),
    ("v2 validation", main_validation, research_validation),
):
    if main_text != research_text:
        errors.append(f"default-branch {label} drifted from research-branch runtime definition")

if errors:
    print("Architecture Reconciliation 1.24 FAILED:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)

print("Architecture Reconciliation 1.24 passed.")
