#!/usr/bin/env python3
"""Semantic Architecture Reconciliation 1.8 guard.

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
aris_doc = read("docs/BELIEF_ARIS_SHADOW.md")
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
if directional.get("version") != "WES-1.1.0":
    errors.append("WES directional admission version must be WES-1.1.0")
if directional.get("require_for_all_new_entries") is not True:
    errors.append("WES 1.1 must require directional admission for all new entries")
cc = ((weekly_policy.get("strategy_tournament") or {}).get("champion_challenger") or {})
if "inverse_v2" not in set(cc.get("challenger_shadow_methods") or []):
    errors.append("WES inverse_v2 must remain Challenger/Shadow")
if "inverse_v2" in set(cc.get("execution_methods") or []):
    errors.append("WES inverse_v2 regained execution authority without promotion")
if cc.get("challenger_execution_enabled") is not False:
    errors.append("WES Challenger execution must remain disabled")
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

for token in ("**Wersja mapy:** 1.8", "IN-08", "EP-09", "LE-10", "PROBATIONARY_CONTROL", "NO_TRADE", "FULL", "WES 1.1.0"):
    require(pl_map, token, "PL Architecture Map")
for token in ("**Map version:** 1.8", "IN-08", "EP-09", "LE-10", "PROBATIONARY_CONTROL", "NO_TRADE", "FULL", "WES 1.1.0"):
    require(en_map, token, "EN Architecture Map")
require(stock_doc, "PRODUCTION CHAMPION — FULL", "Stock Trading architecture")
require(stock_doc, "Market Relationship / Trigger", "Stock Trading architecture")
require(stock_doc, "stock-trading-v2", "Stock Trading branch authority")
require(weekly_doc, "NO_TRADE", "Weekly methodology")
require(weekly_doc, "WES 1.1", "Weekly methodology")
require(weekly_doc, "inverse_v2", "Weekly methodology")
require(gse_doc, "Active v2 runtime", "GSE architecture")
require(aris_doc, "research_shadow", "ARIS shadow architecture")
require(auto_doc, "Production materialization from this legacy PR35/PR36 loop is retired", "Autonomous Policy docs")
require(agents, "stock-trading-v2", "root AGENTS branch authority")

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
    print("Architecture Reconciliation 1.8 FAILED:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)

print("Architecture Reconciliation 1.8 passed.")
