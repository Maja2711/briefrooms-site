from pathlib import Path
ROOT=Path(__file__).parents[1]
JS=ROOT/"scripts"/"deepbook_predict_shadow.mjs"
def test_shadow_invariants():
    src=JS.read_text(encoding="utf-8")
    for x in ["visible_in_decision_lab:false","belief_core_authority:false","execution_authority:false","automatic_promotion:false"]:
        assert x in src
def test_no_public_projection_wiring():
    src=JS.read_text(encoding="utf-8")
    assert "decision_lab_public" not in src
