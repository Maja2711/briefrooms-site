import importlib.util
from pathlib import Path

P=Path(__file__).parents[1]/"scripts"/"deepbook_predict_shadow.py"
spec=importlib.util.spec_from_file_location("dbshadow",P)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def test_normalize_probability():
    q=m.normalize_quote({"asset":"btc","premium_per_notional":0.61,"expiry":"2026-09-25T00:00:00Z","lower":100000})
    assert q["asset"]=="BTC"
    assert q["probability"]==0.61

def test_shadow_invariants_are_explicit_in_source():
    src=P.read_text(encoding="utf-8")
    assert '"visible_in_decision_lab"]=False' in src
    assert '"belief_core_authority"]=False' in src
    assert '"execution_authority"]=False' in src
    assert '"automatic_promotion"]=False' in src
