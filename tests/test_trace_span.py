import json

from app.rag import trace


def test_span_records_duration_and_trace_id(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "_trace_dir", tmp_path)
    monkeypatch.setattr(trace, "_trace_path", tmp_path / "trace.jsonl")

    token = trace.begin_trace(source="test", trace_id="t123")
    try:
        with trace.span("retrieval", question_len=3):
            pass
    finally:
        trace.end_trace(token)

    rows = [
        json.loads(line)
        for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    spans = [r for r in rows if r.get("kind") == "span"]
    assert len(spans) == 1
    assert spans[0]["name"] == "retrieval"
    assert spans[0]["trace_id"] == "t123"
    assert spans[0]["question_len"] == 3
    assert spans[0]["ms"] >= 0
