from langchain_core.documents import Document

from app.rag import context


def test_expand_parents_uses_parent_content_and_dedupes(monkeypatch):
    class FakeStore:
        def get(self, ids, include):
            return {"ids": ids, "documents": [f"parent-{i}" for i in ids]}

    monkeypatch.setattr(context, "get_vectorstore", lambda: FakeStore())
    docs = [
        Document(page_content="child-a", metadata={"parent_id": "p1", "doc_id": "d1"}),
        Document(page_content="child-b", metadata={"parent_id": "p1", "doc_id": "d1"}),
        Document(page_content="no-parent", metadata={"doc_id": "d1"}),
    ]
    out = context.expand_parents(docs)
    assert len(out) == 2
    assert out[0].page_content == "parent-p1"
    assert out[0].metadata["doc_id"] == "d1"
    assert out[1].page_content == "no-parent"


def test_expand_parents_keeps_child_when_parent_missing(monkeypatch):
    class EmptyStore:
        def get(self, ids, include):
            return {"ids": [], "documents": []}

    monkeypatch.setattr(context, "get_vectorstore", lambda: EmptyStore())
    docs = [Document(page_content="child", metadata={"parent_id": "missing"})]
    out = context.expand_parents(docs)
    assert out[0].page_content == "child"


def test_expanded_parent_uses_parent_chunk_id(monkeypatch):
    class Store:
        def get(self, ids, include):
            return {
                "ids": ["p1"],
                "documents": ["parent-content"],
                "metadatas": [{"doc_id": "d1", "chunk_id": "p1", "chunk_level": "parent"}],
            }

    monkeypatch.setattr(context, "get_vectorstore", lambda: Store())
    child = Document(
        page_content="child",
        metadata={"doc_id": "d1", "parent_id": "p1", "chunk_id": "c1", "chunk_level": "child"},
    )
    out = context.expand_parents([child])
    assert out[0].metadata["chunk_level"] == "parent"
    assert out[0].metadata["chunk_id"] == "p1"
