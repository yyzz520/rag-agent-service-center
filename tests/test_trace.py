from langchain_core.documents import Document

from app.rag.trace import chunk_id


def test_chunk_id_is_content_stable():
    a = Document(page_content="相同内容", metadata={"source": "a.pdf"})
    b = Document(page_content="相同内容", metadata={"source": "b.pdf"})
    c = Document(page_content="不同内容", metadata={"source": "a.pdf"})
    assert chunk_id(a) == chunk_id(b)
    assert chunk_id(a) != chunk_id(c)
