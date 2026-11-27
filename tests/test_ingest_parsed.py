from langchain_core.documents import Document

from app.rag.chain import _format_sources
from app.services.ingest import build_parsed_text


def test_build_parsed_text_keeps_page_boundaries():
    text = build_parsed_text([
        Document(page_content="第一页内容", metadata={"page": 3}),
        Document(page_content="无页码片段", metadata={}),
    ])
    assert "第 3 页" in text and "第一页内容" in text
    assert "片段 2" in text and "无页码片段" in text


def test_sources_carry_doc_and_chunk_ids():
    _, sources = _format_sources([
        Document(page_content="正文", metadata={
            "doc_id": "d1", "chunk_id": "v1", "parent_id": "p1",
            "chunk_level": "parent", "source": "s.pdf", "page": 2,
        })
    ])
    src = sources[0]
    assert src["doc_id"] == "d1"
    assert src["vector_id"] == "v1"
    assert src["parent_id"] == "p1"
    assert src["chunk_level"] == "parent"
