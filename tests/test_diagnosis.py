from app.rag.diagnosis import diagnose_agent, diagnose_generation, diagnose_retrieval


def test_diagnose_retrieval_miss_and_rerank():
    items = [
        {"question": "q1", "hit": False, "rank": None},
        {"question": "q2", "hit": True, "rank": 3},
        {"question": "q3", "hit": True, "rank": 1},
    ]
    out = diagnose_retrieval(items)
    assert [x["category"] for x in out] == ["retrieval_miss", "rerank_rank"]
    assert out[0]["suggestion"]
    assert out[1]["evidence"]["rank"] == 3


def test_diagnose_generation_refusal_and_generation():
    items = [
        {"category": "positive", "question": "p1", "correct": False, "cited": True, "judge_error": False},
        {"category": "positive", "question": "p2", "correct": False, "cited": False, "judge_error": True},
        {"category": "negative", "question": "n1", "reason": "refused_but_cited"},
        {"category": "negative", "question": "n2", "reason": "not_refused"},
    ]
    out = diagnose_generation(items)
    categories = [x["category"] for x in out]
    assert "generation_incorrect_cited" in categories
    assert "judge_error" in categories
    assert "refusal_citation" in categories
    assert "refusal_failed" in categories


def test_diagnose_agent_routing_and_completion():
    items = [
        {"question": "a1", "routed": False, "answer_ok": True, "tools_used": [], "expect_tools": ["search_knowledge_base"]},
        {"question": "a2", "routed": True, "answer_ok": False, "tools_used": ["calculator"], "answer": "bad"},
    ]
    out = diagnose_agent(items)
    assert [x["category"] for x in out] == ["agent_routing", "agent_completion"]
