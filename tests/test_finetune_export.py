from app.rag.finetune_export import build_samples


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
