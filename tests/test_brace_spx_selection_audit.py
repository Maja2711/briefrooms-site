from scripts.brace_spx_selection_audit import candidate_complexity, select_candidate


def row(candidate_id, family, feature_set, sharpe, fold_sharpes, drawdown=-0.15, turnover=1.0):
    return {
        "candidate_id": candidate_id,
        "candidate": {"family": family, "feature_set": feature_set, "max_exposure": 1.0, "params": {"x": 1}},
        "metrics": {"sharpe_excess": sharpe, "months": 108, "cagr": 0.1, "max_drawdown": drawdown, "calmar": 0.7, "annualized_turnover": turnover},
        "fold_metrics": [{"sharpe_excess": value} for value in fold_sharpes],
    }


def test_complexity_prefers_simple_family_and_features():
    simple = candidate_complexity({"family": "logistic", "feature_set": "core", "params": {"C": 1}})
    complex_ = candidate_complexity({"family": "random_forest", "feature_set": "rich", "params": {"a": 1, "b": 2}})
    assert simple < complex_


def test_selection_prefers_simpler_when_within_one_standard_error():
    experiments = [
        row("complex", "random_forest", "rich", 1.05, [0.6, 0.8, 1.1, 1.2, 0.7, 0.9]),
        row("simple", "logistic", "core", 1.00, [0.7, 0.8, 0.9, 1.0, 0.8, 0.7]),
    ]
    result = select_candidate(experiments)
    assert result["selected"]["candidate_id"] == "simple"
    assert result["equivalent_candidate_count"] == 2


def test_unstable_candidate_is_not_eligible():
    experiments = [
        row("unstable", "logistic", "core", 1.5, [1.0, -1.0, -0.5, 0.2, -0.3, 0.1]),
        row("stable", "logistic", "core", 0.8, [0.4, 0.5, 0.7, 0.8, 0.5, 0.6]),
    ]
    result = select_candidate(experiments)
    assert result["selected"]["candidate_id"] == "stable"
    assert result["eligible_count"] == 1
