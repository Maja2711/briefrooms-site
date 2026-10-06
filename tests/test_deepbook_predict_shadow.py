from pathlib import Path
import importlib.util

ROOT=Path(__file__).parents[1]
JS=ROOT/"scripts"/"deepbook_predict_shadow.mjs"
SCORE=ROOT/"scripts"/"deepbook_predict_shadow_score.py"

def load_score():
    spec=importlib.util.spec_from_file_location("deepbook_score",SCORE)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def test_shadow_invariants():
    src=JS.read_text(encoding="utf-8")
    for x in ["visible_in_decision_lab:false","belief_core_authority:false","execution_authority:false","automatic_promotion:false"]:
        assert x in src

def test_no_public_projection_wiring():
    src=JS.read_text(encoding="utf-8")
    assert "decision_lab_public" not in src

def test_score_hit_miss_and_brier_without_learning():
    m=load_score()
    state={"snapshots":[{"captured_at":"2026-01-01T00:00:00Z","quotes":[
      {"market_id":"a","expiry_ms":1000,"strike":100,"up_probability":0.8},
      {"market_id":"b","expiry_ms":2000,"strike":100,"up_probability":0.7}]}],
      "settlements":{"1000":{"price":110},"2000":{"price":90}}}
    rows=m.score(state); s=m.summary(rows)
    assert [x["hit"] for x in rows]==[True,False]
    assert round(s["accuracy"],6)==0.5
    assert round(s["brier"],6)==0.265
    assert round(s["brier_skill_vs_50"],6)==-0.06

def test_unsettled_quote_is_never_scored():
    m=load_score()
    state={"snapshots":[{"quotes":[{"expiry_ms":1000,"strike":100,"up_probability":0.9}]}],"settlements":{}}
    assert m.score(state)==[]

def test_validation_script_has_no_model_writeback():
    src=SCORE.read_text(encoding="utf-8")
    for forbidden in ["decision_lab_public.json","belief_core","execution_authority=True","automatic_promotion=True"]:
        assert forbidden not in src
