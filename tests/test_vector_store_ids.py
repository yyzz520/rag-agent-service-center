from langchain_core.documents import Document

from app.rag import vector_store


class _FakeStore:
    def __init__(self):
        self.ids = None

    def add_documents(self, docs, ids=None):
        self.ids = ids
        return ids or [str(i) for i in range(len(docs))]


def test_add_documents_uses_stable_chunk_ids(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(vector_store, "get_vectorstore", lambda: fake)
    docs = [
        Document(page_content="parent", metadata={"chunk_id": "p1"}),
        Document(page_content="child", metadata={"chunk_id": "c1"}),
    ]
    assert vector_store.add_documents(docs) == 2
    assert fake.ids == ["p1", "c1"]
