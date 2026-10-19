from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.rag import cache
from app.rag.hybrid import hybrid_candidates
from app.rag.reranker import get_reranker
from app.rag.rewriter import rewrite_query
from app.rag.trace import chunk_id, log_retrieval


class RAGRetriever(BaseRetriever):
    """完整检索链路：查询改写 → 多路混合检索 → 各路 rerank → 合并去重。

    为什么按改写后的每条查询分别走一遍混合检索+rerank，而不是合并成一个池再排一次：
    问句里的自然语言（"总负债是多少"）对重排模型是噪声，直接把"负债合计"这种
    文档里真实存在的术语拿去检索和打分，命中率要高得多。
    """

    candidate_pool: int = 100     # 每路混合检索的候选池大小（混合检索实际最多 ~60，放宽避免截断）
    per_query_top: int = 10       # 每路（rerank 或纯 RRF）取前几个
    max_docs: int = 16            # 最终返回给 LLM 的最大分块数
    allowed_doc_ids: list[str] | None = None  # 只在这些文档（uuid）内检索；None=全库（仅管理口径）
    use_rerank: bool = True       # 评测闭环可关掉 rerank，对照"纯 RRF 粗筛 vs rerank 精排"
    record_trace: bool = True     # 评测闭环重放检索时置 False，不污染线上问答的 trace 配对日志

    def _get_relevant_documents(self, query: str) -> list[Document]:
        if self.allowed_doc_ids == []:
            return []  # 当前用户还没有任何文档：直接空召回，别去碰全库
        hit = cache.get_retrieval(
            query,
            self.allowed_doc_ids,
            self.use_rerank,
            self.candidate_pool,
            self.per_query_top,
            self.max_docs,
        )
        if hit is not None:
            if self.record_trace:
                # 命中缓存：改写/rerank 这次都没跑，只有召回结果可记
                log_retrieval(query, [query], hit, cache_hit=True)
            return hit
        final: list[Document] = []
        seen: set[str] = set()
        rerank_details: dict[str, list[dict]] = {}  # 每个改写查询下 rerank 打分快照
        rewritten = rewrite_query(query)
        for q in rewritten:
            pool_pairs = hybrid_candidates(q, allowed_doc_ids=self.allowed_doc_ids)
            pool = [d for d, _ in pool_pairs][: self.candidate_pool]
            if not pool:
                continue
            # pool 本身按 RRF 分倒序，所以池内下标 +1 就是 rerank 之前的 RRF 名次。
            # use_rerank=False 时不调 rerank 模型，直接按 RRF 名次取前 per_query_top——
            # 就是"只有 RRF 粗筛"的对照，rerank_score 记为 None（表示没做过精排）。
            if self.use_rerank:
                scores = get_reranker().score([(q, d.page_content) for d in pool])
                ranked = sorted(zip(pool, scores), key=lambda x: -x[1])
            else:
                ranked = [(d, None) for d in pool]
            top = ranked[: self.per_query_top]
            rerank_details[q] = [
                {
                    "chunk_id": chunk_id(d),
                    "source": d.metadata.get("source"),
                    "page": d.metadata.get("page"),
                    "rrf_rank": pool.index(d) + 1,      # RRF 融合后的名次
                    "rerank_score": None if score is None else round(score, 4),
                }
                for d, score in top
            ]
            for d, _ in top:
                if d.page_content not in seen:
                    seen.add(d.page_content)
                    final.append(d)
        result = final[: self.max_docs]
        cache.set_retrieval(
            query,
            self.allowed_doc_ids,
            result,
            self.use_rerank,
            self.candidate_pool,
            self.per_query_top,
            self.max_docs,
        )
        if self.record_trace:
            log_retrieval(query, rewritten, result, rerank_details=rerank_details)
        return result


def get_rerank_retriever(allowed_doc_ids: list[str] | None = None):
    return RAGRetriever(allowed_doc_ids=allowed_doc_ids)
