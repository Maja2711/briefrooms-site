from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("overlay", ROOT / "scripts/brace_portfolio_material_overlay.py")
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_thesis_review_caps_score_in_reduce_range():
    result = module.apply({
        "final_score": 61.0, "thesis_score": 60.0, "risk_score": 55.0,
        "positive_factors": [], "negative_factors": [], "conditions_for_change": [],
    }, {
        "report_count": 1, "negative_count": 1, "positive_count": 0,
        "requires_thesis_review": True, "critical_count": 0, "high_negative_count": 1,
    })
    assert result["final_score"] == 42.0
    assert "material_thesis_review" in result["negative_factors"]


def test_critical_report_reaches_exit_range():
    result = module.apply({
        "final_score": 70.0, "thesis_score": 70.0, "risk_score": 60.0,
        "positive_factors": [], "negative_factors": [], "conditions_for_change": [],
    }, {
        "report_count": 1, "negative_count": 1, "positive_count": 0,
        "requires_thesis_review": True, "critical_count": 1, "high_negative_count": 1,
    })
    assert result["final_score"] <= 25.0
