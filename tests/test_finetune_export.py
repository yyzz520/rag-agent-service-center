from app.rag.finetune_export import (
    build_agent_samples,
    build_preference_pairs,
    build_samples,
)


def test_build_samples_keeps_only_passed_and_clean_refusal():
    items = [
        {"category": "positive", "question": "q1", "answer": "这是一个正确答案 [1]", "correct": True, "citation_correct": True},
        {"category": "positive", "question": "q2", "answer": "这个答案是错的 [1]", "correct": False, "citation_correct": True},
        {"category": "positive", "question": "q3", "answer": "答案对但引用错 [2]", "correct": True, "citation_correct": False},
        {"category": "negative", "question": "n1", "answer": "资料中没有", "reason": "refused"},
        {"category": "negative", "question": "n2", "answer": "资料中没有 [1]", "reason": "refused_but_cited"},
    ]
    samples = build_samples(items)
    assert samples == [
        {"instruction": "q1", "input": "", "output": "这是一个正确答案"},
        {"instruction": "n1", "input": "", "output": "资料中没有相关内容，无法回答。"},
    ]


def test_build_samples_dedupes_identical_pairs():
    items = [
        {"category": "positive", "question": "q1", "answer": "这是一个正确答案 [1]", "correct": True, "citation_correct": True},
        {"category": "positive", "question": "q1", "answer": "这是一个正确答案 [1]", "correct": True, "citation_correct": True},
    ]
    assert len(build_samples(items)) == 1


def test_build_samples_filters_short_and_long_answers():
    items = [
        {"category": "positive", "question": "q1", "answer": "太短 [1]", "correct": True, "citation_correct": True},
        {"category": "positive", "question": "q2", "answer": "长" * 5000 + " [1]", "correct": True, "citation_correct": True},
    ]
    assert build_samples(items) == []


def test_build_samples_redacts_pii():
    items = [
        {
            "category": "positive",
            "question": "张三 13812345678 的邮箱是什么",
            "answer": "联系邮箱 zhangsan@example.com，备用密钥 sk-abcdef1234567890 [1]",
            "correct": True,
            "citation_correct": True,
        },
    ]
    [sample] = build_samples(items)
    assert "13812345678" not in sample["instruction"]
    assert "[手机号]" in sample["instruction"]
    assert "zhangsan@example.com" not in sample["output"]
    assert "sk-abcdef1234567890" not in sample["output"]


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


def test_build_preference_pairs_pairs_same_question_across_runs():
    run1 = [
        {"category": "positive", "question": "q1", "answer": "这是正确的答案内容 [1]", "correct": True, "citation_correct": True},
        {"category": "positive", "question": "q2", "answer": "只答对过一次的答案 [1]", "correct": True, "citation_correct": True},
    ]
    run2 = [
        {"category": "positive", "question": "q1", "answer": "这是错误的答案内容 [1]", "correct": False, "citation_correct": True},
        {"category": "negative", "question": "n1", "answer": "资料中没有", "reason": "refused"},
    ]
    run3 = [
        {"category": "negative", "question": "n1", "answer": "资料中没有相关 [1]", "reason": "refused_but_cited"},
    ]
    pairs = build_preference_pairs([(1.0, run1), (2.0, run2), (3.0, run3)])
    assert pairs == [
        {"question": "n1", "chosen": "资料中没有相关内容，无法回答。", "rejected": "资料中没有相关 [1]"},
        {"question": "q1", "chosen": "这是正确的答案内容", "rejected": "这是错误的答案内容 [1]"},
    ]
