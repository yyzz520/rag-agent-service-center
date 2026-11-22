from app.rag.stats import bootstrap_ci, stability


def test_bootstrap_ci_is_deterministic_and_bounds_mean():
    values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0]
    a = bootstrap_ci(values)
    b = bootstrap_ci(values)
    assert a == b
    assert a["lo"] <= a["mean"] <= a["hi"]
    assert 0.0 <= a["lo"] and a["hi"] <= 1.0


def test_bootstrap_ci_handles_empty():
    assert bootstrap_ci([]) == {"mean": 0.0, "lo": 0.0, "hi": 0.0}


def test_stability_reports_spread():
    out = stability([0.8, 0.9, 1.0])
    assert out["runs"] == 3
    assert out["min"] == 0.8 and out["max"] == 1.0
    assert out["mean"] == 0.9
    assert out["std"] > 0
