from app.rag.eval_gate import DEFAULT_BASELINE, check


def test_gate_passes_above_thresholds():
    failures = check(
        {"recall@1": 0.6, "mrr": 0.8},
        {"correct_rate": 0.9, "citation_rate": 1.0, "refusal_rate": 0.9},
        DEFAULT_BASELINE,
    )
    assert failures == []


def test_gate_reports_all_below_threshold_metrics():
    failures = check(
        {"recall@1": 0.4, "mrr": 0.5},
        {"correct_rate": 0.5, "citation_rate": 0.5, "refusal_rate": 0.5},
        DEFAULT_BASELINE,
    )
    assert len(failures) == 5
    assert {f["metric"] for f in failures} == {
        "recall@1",
        "mrr",
        "correct_rate",
        "citation_rate",
        "refusal_rate",
    }


def test_gate_treats_missing_metric_as_failure():
    failures = check({}, {"correct_rate": 0.9}, DEFAULT_BASELINE)
    assert any(f["metric"] == "recall@1" for f in failures)
    assert any(f["metric"] == "refusal_rate" for f in failures)
