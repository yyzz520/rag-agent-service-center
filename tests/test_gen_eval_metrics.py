from app.rag.gen_eval import _metrics


def test_generation_metrics_refusal_and_citation():
    results = [
        {"category": "positive", "correct": True, "cited": True, "judge_error": False},
        {"category": "positive", "correct": False, "cited": True, "judge_error": False},
        {"category": "negative", "correct": True, "cited": False, "reason": "refused"},
        {"category": "negative", "correct": False, "cited": True, "reason": "refused_but_cited"},
    ]
    m = _metrics(results)
    assert m["positive_total"] == 2
    assert m["correct_rate"] == 0.5
    assert m["citation_rate"] == 1.0
    assert m["negative_total"] == 2
    assert m["refusal_rate"] == 0.5
    assert m["refusal_any_rate"] == 1.0
    assert m["negative_cited_rate"] == 0.5
    assert m["judge_errors"] == 0
