from app.rag import agent_eval
from app.rag.agent_eval import _metrics


def test_agent_metrics_include_negative_and_forbidden_rates():
    results = [
        {"routed": True, "answer_ok": True, "tools_used": ["search_knowledge_base"], "tokens": 100, "negative": False, "refused": False, "forbid_tools": False},
        {"routed": True, "answer_ok": True, "tools_used": [], "tokens": 50, "negative": False, "refused": False, "forbid_tools": True},
        {"routed": True, "answer_ok": True, "tools_used": [], "tokens": 80, "negative": True, "refused": True, "forbid_tools": False},
        {"routed": False, "answer_ok": False, "tools_used": ["calculator"], "tokens": 70, "negative": True, "refused": False, "forbid_tools": False},
    ]
    m = _metrics(results)
    assert m["tasks"] == 4
    assert m["tool_route_accuracy"] == 0.75
    assert m["completion_rate"] == 0.75
    assert m["negative_total"] == 2
    assert m["negative_refused"] == 1
    assert m["negative_refusal_rate"] == 0.5
    assert m["forbidden_tool_rate"] == 0.0
    assert m["avg_tokens"] == 75.0


def test_doc_ids_for_name_resolves_by_source(monkeypatch):
    class _FakeStore:
        def get(self, include):
            return {"metadatas": [
                {"source": "uploads/清源缓释片说明书.pdf", "doc_id": "d1"},
                {"source": "uploads/其他.pdf", "doc_id": "d2"},
            ]}

    monkeypatch.setattr(agent_eval, "get_vectorstore", lambda: _FakeStore())
    assert agent_eval._doc_ids_for_name("清源缓释片说明书.pdf") == ["d1"]
