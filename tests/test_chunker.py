from langchain_core.documents import Document

from app.rag.chunker import chunk_documents


def test_structure_chunking_builds_parents_and_children():
    text = """# 总则
第一条 目的与适用范围。

## 召回分级
一级召回须在 24 小时内通知。
| 批次 | 处置 |
| --- | --- |
| QS-1 | 优先发放 |
| QS-2 | 正常销售 |
"""
    chunks = chunk_documents([
        Document(page_content=text, metadata={"doc_id": "d1", "source": "x.md"})
    ])
    parents = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    parent_ids = {p.metadata["parent_id"] for p in parents}

    assert len(parents) >= 2
    assert children
    assert all(c.metadata["parent_id"] in parent_ids for c in children)
    assert all(p.metadata["chunk_id"] == p.metadata["parent_id"] for p in parents)
    child_ids = {c.metadata["chunk_id"] for c in children}
    assert len(child_ids) == len(children)
    assert all(c.metadata["chunk_id"] != c.metadata["parent_id"] for c in children)
    assert any("召回分级" in p.page_content for p in parents)
    assert any("QS-1" in c.page_content and "批次" in c.page_content for c in children)
    assert all(len(c.page_content) <= 800 for c in children)


def test_no_heading_falls_back_to_single_parent():
    text = "普通段落一。" * 60 + "\n" + "普通段落二。" * 60
    chunks = chunk_documents([
        Document(page_content=text, metadata={"doc_id": "d2", "source": "x.txt"})
    ])
    parents = [c for c in chunks if c.metadata["chunk_level"] == "parent"]
    children = [c for c in chunks if c.metadata["chunk_level"] == "child"]
    assert len(parents) == 1
    assert len(children) >= 1
    assert all(c.metadata["parent_id"] == parents[0].metadata["parent_id"] for c in children)
