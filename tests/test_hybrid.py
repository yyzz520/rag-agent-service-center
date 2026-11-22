from langchain_core.documents import Document

from app.rag import hybrid


def test_tokenize_keeps_chinese_bigrams_and_drops_numbers_punct():
    tokens = hybrid.tokenize("总负债 500 万元，告警90%")
    assert "总" in tokens
    assert "总负" in tokens
    assert "负债" in tokens
    assert "5" not in tokens
    assert " " not in tokens
    assert "%" not in tokens


def test_empty_allowed_scope_short_circuits():
    assert hybrid.hybrid_candidates("任意问题", allowed_doc_ids=[]) == []


class _FakeRetriever:
    def __init__(self, docs):
        self._docs = docs

    def invoke(self, _query):
        return self._docs


class _FakeBM25:
    def __init__(self, scores):
        self._scores = scores

    def get_scores(self, _tokens):
        return self._scores


def test_rrf_prefers_doc_hitting_both_routes(monkeypatch):
    a = Document(page_content="A", metadata={"doc_id": "1"})
    b = Document(page_content="B", metadata={"doc_id": "2"})
    c = Document(page_content="C", metadata={"doc_id": "3"})
    all_docs = [a, b, c]

    monkeypatch.setattr(hybrid, "get_retriever", lambda **_kw: _FakeRetriever([a, b]))
    monkeypatch.setattr(
        hybrid, "_get_bm25", lambda: (_FakeBM25([0.1, 5.0, 3.0]), all_docs)
    )

    ranked = hybrid.hybrid_candidates("query")
    assert [d.page_content for d, _ in ranked][0] == "B"
    # B: 向量第 2（1/61）+ BM25 第 1（1/60）
    assert ranked[0][1] == 1 / 61 + 1 / 60
