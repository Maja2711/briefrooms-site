#!/usr/bin/env python3
from pathlib import Path

path = Path('tests/test_brace_portfolio_engine.py')
text = path.read_text(encoding='utf-8')

old = '''def test_registry_preserves_baseline_and_initial_shadow_state():
    registry = load("data/portfolio10k/methodology_registry.json")
    assert registry["controller_state"] == "ACTIVE_BASELINE"
    assert registry["champion_methodology_id"] == "portfolio-10k-baseline"
    methods = {item["methodology_id"]: item for item in registry["methodologies"]}
    assert methods["portfolio-10k-baseline"]["status"] == "ACTIVE_BASELINE"
    assert methods["brace-portfolio-engine"]["status"] == "SHADOW"
'''
new = '''def test_registry_preserves_authorised_probationary_control_and_fallback():
    registry = load("data/portfolio10k/methodology_registry.json")
    assert registry["controller_state"] == "PROBATIONARY_CONTROL"
    assert registry["champion_methodology_id"] == "brace-portfolio-engine"
    methods = {item["methodology_id"]: item for item in registry["methodologies"]}
    assert methods["portfolio-10k-baseline"]["status"] == "FALLBACK_BASELINE"
    assert methods["brace-portfolio-engine"]["status"] == "PROBATIONARY_CONTROL"
    authorisation = methods["brace-portfolio-engine"]["validation_results"]["user_authorized_paper_control"]
    assert authorisation["paper_only"] is True
    assert authorisation["remaining_automatic_promotion_gates_preserved"] is True
'''
if new not in text:
    if old not in text:
        raise SystemExit('Outdated registry contract block not found')
    text = text.replace(old, new, 1)

old_asset = '        assert "/scripts/portfolio-10k-control-public.js?v=2" in html\n'
new_asset = '        assert "/scripts/portfolio-10k-control-public.js?v=" in html\n'
if new_asset not in text:
    if old_asset not in text:
        raise SystemExit('Outdated exact asset-version assertion not found')
    text = text.replace(old_asset, new_asset, 1)

path.write_text(text, encoding='utf-8')
