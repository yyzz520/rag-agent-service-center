from app.rag.observability import percentile, summarize


def test_percentile_edges():
    assert percentile([], 95) == 0.0
    assert percentile([10], 95) == 10.0
    assert percentile([10, 20, 30, 40], 50) == 25.0


def test_summarize_spans_and_tokens():
    records = [
        {"kind": "span", "name": "retrieval", "ms": 10},
        {"kind": "span", "name": "retrieval", "ms": 30},
        {"kind": "span", "name": "generation", "ms": 100},
        {"kind": "answer", "usage": {"total_tokens": 100, "model_calls": 2, "tool_calls": 1}},
        {"kind": "answer", "usage": {"total_tokens": 300, "model_calls": 1, "tool_calls": 0}},
    ]
    out = summarize(records)
    assert out["spans"]["retrieval"]["count"] == 2
    assert out["spans"]["generation"]["p95"] == 100.0
    assert out["answers"] == {
        "count": 2,
        "tokens_total": 400,
        "tokens_p50": 200.0,
        "tokens_p95": 290.0,
        "model_calls": 3,
        "tool_calls": 1,
    }
