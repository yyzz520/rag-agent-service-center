from app.rag.eval import _metrics


def test_metrics_overall_and_by_category():
    results = [
        {"hit": True, "rank": 1, "category": "single_doc"},
        {"hit": True, "rank": 3, "category": "table"},
        {"hit": False, "rank": None, "category": "single_doc"},
    ]
    m = _metrics(results, 16)
    assert m["total"] == 3
    assert m["hits"] == 2
    assert m["recall@1"] == 0.333
    assert m["recall@5"] == 0.667
    assert m["recall@16"] == 0.667
    assert m["mrr"] == round((1 + 1 / 3) / 3, 3)
    assert m["precision@16"] == round(2 / (3 * 16), 3)
    assert m["ndcg@16"] == 0.5
    assert m["by_category"]["single_doc"]["total"] == 2
    assert m["by_category"]["single_doc"]["recall@1"] == 0.5
    assert m["by_category"]["table"]["recall@1"] == 0.0


def test_metrics_empty_does_not_divide_by_zero():
    m = _metrics([], 16)
    assert m["total"] == 0
    assert m["hits"] == 0
    assert m["recall@16"] == 0.0
    assert m["mrr"] == 0.0
    assert m["by_category"] == {}
