from langchain_core.documents import Document

from app.rag import cache, context, hybrid


class _FakeRetriever:
    def invoke(self, query):
        return [
            Document(page_content="tenant-a-content", metadata={"doc_id": "A", "chunk_level": "child"}),
            Document(page_content="tenant-b-content", metadata={"doc_id": "B", "chunk_level": "child"}),
            Document(page_content="parent-of-a", metadata={"doc_id": "A", "chunk_level": "parent"}),
        ]


class _FakeBM25:
    def get_scores(self, tokens):
        return [1.0, 1.0]


def test_hybrid_filters_both_routes_and_parents(monkeypatch):
    all_docs = [
        Document(page_content="tenant-a-content", metadata={"doc_id": "A", "chunk_level": "child"}),
        Document(page_content="tenant-b-content", metadata={"doc_id": "B", "chunk_level": "child"}),
    ]
    monkeypatch.setattr(hybrid, "get_retriever", lambda k, where: _FakeRetriever())
    monkeypatch.setattr(hybrid, "_get_bm25", lambda: (_FakeBM25(), all_docs))

    out = hybrid.hybrid_candidates("q", allowed_doc_ids=["A"])

    assert out
    assert all(d.metadata["doc_id"] == "A" for d, _ in out)
    assert all(d.metadata.get("chunk_level") != "parent" for d, _ in out)


def test_empty_scope_returns_nothing_not_full_library(monkeypatch):
    monkeypatch.setattr(hybrid, "get_retriever", lambda k, where: _FakeRetriever())
    monkeypatch.setattr(hybrid, "_get_bm25", lambda: (_FakeBM25(), []))
    assert hybrid.hybrid_candidates("q", allowed_doc_ids=[]) == []


def test_retrieval_cache_key_is_scoped_by_documents():
    def key(scope):
        return cache._retrieval_key("q", scope, True, 100, 10, 16)

    assert key(["A"]) != key(["B"])
    assert key(["B", "A"]) == key(["A", "B"])  # 顺序不影响 key
    assert key(None) != key([])  # 全库管理口径 vs 空作用域，不能互相命中


def test_expand_parents_refuses_cross_document_parent(monkeypatch):
    class CrossStore:
        def get(self, ids, include):
            return {
                "ids": ids,
                "documents": ["other-tenant-parent"],
                "metadatas": [{"doc_id": "OTHER"}],
            }

    monkeypatch.setattr(context, "get_vectorstore", lambda: CrossStore())
    child = Document(page_content="child", metadata={"doc_id": "D", "parent_id": "p1"})

    out = context.expand_parents([child])

    assert out[0].page_content == "child"  # 不采用跨文档父块
