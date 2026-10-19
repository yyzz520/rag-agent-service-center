from functools import lru_cache
from hashlib import md5

from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from app.rag.vector_store import get_retriever, get_vectorstore


def tokenize(text: str) -> list[str]:
    """中文按字符+相邻双字切分（bigram）。

    直接用空格分词对中文无效（"总负债"和"负债合计"会被当成完全不相关的词）。
    字符单字 + 双字 组合能让"总负债"↔"负债合计"在"负债"上产生词面命中。
    数字、空白、标点一律剔除，避免财务报表里的数字干扰关键词匹配。
    """
    chars = [
        c for c in text
        if not c.isspace() and not c.isdigit()
        and c not in "：:；;，,。.（）()【】[]《》<>%％￥、\"'“”"
    ]
    tokens = list(chars)
    tokens += [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    return tokens


@lru_cache
def _build_bm25(_fingerprint: str, _texts: tuple[str, ...]):
    return BM25Okapi([tokenize(t) for t in _texts])


def _get_bm25():
    """返回 (BM25 索引, 全量 Document 列表)。索引按分块 id 指纹缓存，增删分块后自动重建。"""
    raw = get_vectorstore().get(include=["documents", "metadatas"])
    docs = [
        Document(page_content=c, metadata=m)
        for c, m in zip(raw["documents"], raw["metadatas"])
    ]
    fingerprint = md5("|".join(raw["ids"]).encode()).hexdigest()
    return _build_bm25(fingerprint, tuple(d.page_content for d in docs)), docs


def hybrid_candidates(
    query: str,
    vec_k: int = 30,
    bm_k: int = 30,
    allowed_doc_ids: list[str] | None = None,
) -> list[tuple[Document, float]]:
    """向量 + BM25 双路检索，Reciprocal Rank Fusion 融合，按得分倒序返回。

    allowed_doc_ids（文档 uuid 列表）同时约束两路：向量一路靠 Chroma 元数据 where
    过滤，BM25 一路对全库打分后只保留属于这些文档的候选——两路口径一致（隔离硬指标）。
    为 None 时全库检索（仅内部管理口径，业务接口不会传 None）。
    """
    if allowed_doc_ids is not None and not allowed_doc_ids:
        return []  # 空作用域 = 无文档可检索，直接空召回（空列表是假值，不早返回会退化成全库）
    where = {"doc_id": {"$in": allowed_doc_ids}} if allowed_doc_ids else None
    vec_docs = get_retriever(k=vec_k, where=where).invoke(query)
    bm25, all_docs = _get_bm25()

    bm_scores = bm25.get_scores(tokenize(query))
    if allowed_doc_ids:
        allowed = set(allowed_doc_ids)
        candidate_ids = [
            i for i in range(len(bm_scores))
            if all_docs[i].metadata.get("doc_id") in allowed
        ]
    else:
        candidate_ids = list(range(len(bm_scores)))
    bm_ranked = sorted(candidate_ids, key=lambda i: -bm_scores[i])[:bm_k]

    rrf: dict[str, tuple[Document, float]] = {}
    for rank, d in enumerate(vec_docs):
        cur = rrf.get(d.page_content)
        rrf[d.page_content] = (d, (cur[1] if cur else 0) + 1.0 / (60 + rank))
    for rank, i in enumerate(bm_ranked):
        d = all_docs[i]
        cur = rrf.get(d.page_content)
        rrf[d.page_content] = (d, (cur[1] if cur else 0) + 1.0 / (60 + rank))

    return sorted(rrf.values(), key=lambda x: -x[1])
