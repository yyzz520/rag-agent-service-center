from app.rag.splitter import get_splitter


def test_splitter_respects_chunk_size():
    splitter = get_splitter(chunk_size=50, chunk_overlap=10)
    text = "第一条规定。第二条规定。第三条规定。第四条规定。第五条规定。" * 20
    chunks = splitter.split_text(text)
    assert chunks
    assert all(0 < len(c) <= 50 for c in chunks)
