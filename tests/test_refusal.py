from app.rag.refusal import finalize


def test_refusal_drops_citations_and_sources():
    sources = [{"id": 1, "source": "a.pdf"}]
    answer, out = finalize("资料中没有相关内容[1]，无法回答 [2]。", sources)
    assert answer == "资料中没有相关内容，无法回答。"
    assert out == []


def test_normal_answer_keeps_citations_and_sources():
    sources = [{"id": 1, "source": "a.pdf"}]
    answer, out = finalize("流程分为三步 [1]。", sources)
    assert answer == "流程分为三步 [1]。"
    assert out is sources


def test_long_text_with_marker_is_not_refusal():
    text = "没有找到" + "细节" * 200
    sources = [{"id": 1}]
    answer, out = finalize(text, sources)
    assert answer == text
    assert out is sources
