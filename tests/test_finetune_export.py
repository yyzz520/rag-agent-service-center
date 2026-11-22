from app.rag.finetune_export import build_agent_samples, build_samples


def test_build_samples_keeps_only_passed_and_clean_refusal():
    items = [
        {"category": "positive", "question": "q1", "answer": "答案 [1]", "correct": True, "citation_correct": True},
        {"category": "positive", "question": "q2", "answer": "错答案 [1]", "correct": False, "citation_correct": True},
        {"category": "positive", "question": "q3", "answer": "答案 [2]", "correct": True, "citation_correct": False},
        {"category": "negative", "question": "n1", "answer": "资料中没有", "reason": "refused"},
        {"category": "negative", "question": "n2", "answer": "资料中没有 [1]", "reason": "refused_but_cited"},
    ]
    samples = build_samples(items)
    assert samples == [
        {"instruction": "q1", "input": "", "output": "答案"},
        {"instruction": "n1", "input": "", "output": "资料中没有相关内容，无法回答。"},
    ]


def test_build_samples_dedupes_identical_pairs():
    items = [
        {"category": "positive", "question": "q1", "answer": "答案 [1]", "correct": True, "citation_correct": True},
        {"category": "positive", "question": "q1", "answer": "答案 [1]", "correct": True, "citation_correct": True},
    ]
    assert len(build_samples(items)) == 1


def test_build_agent_samples_filters_failed_and_bad_routing():
    trajectory = [
        {"role": "user", "content": "3*7"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "calculator", "args": {"expression": "3*7"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "calculator", "content": "21"},
        {"role": "assistant", "content": "21"},
    ]
    items = [
        {"answer_ok": True, "routed": True, "trajectory": trajectory},
        {"answer_ok": False, "routed": True, "trajectory": trajectory},
        {"answer_ok": True, "routed": False, "trajectory": trajectory},
        {"answer_ok": True, "routed": True, "trajectory": trajectory},
    ]
    samples = build_agent_samples(items)
    assert len(samples) == 1
    assert samples[0]["messages"][0] == {"role": "user", "content": "3*7"}
    assert samples[0]["messages"][1]["tool_calls"][0]["name"] == "calculator"
