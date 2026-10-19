import time
from functools import lru_cache

import httpx
from langchain_core.cross_encoders import BaseCrossEncoder

from app.config import settings


def _post_with_retry(url: str, *, retries: int | None = None, **kwargs) -> httpx.Response:
    """httpx 直连没有内置重试：临时故障（429/5xx/连接错误）指数退避重试。"""
    retries = settings.llm_max_retries if retries is None else retries
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.post(url, timeout=settings.llm_timeout_seconds, **kwargs)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(min(2**attempt, 8))  # 1s → 2s → 4s 封顶 8s
    assert last is not None
    raise last


class SiliconFlowCrossEncoder(BaseCrossEncoder):
    """SiliconFlow rerank API 封装，符合 LangChain 的 BaseCrossEncoder 接口。

    用法上它是个 cross encoder：把 (query, 文档) 成对打分，分数越高越相关。
    底层一次只能带一个 query，所以这里按 query 分组，每组调一次 API。
    """

    def score(self, text_pairs: list[tuple[str, str]]) -> list[float]:
        groups: dict[str, list[tuple[int, str]]] = {}
        for i, (query, doc) in enumerate(text_pairs):
            groups.setdefault(query, []).append((i, doc))

        scores = [0.0] * len(text_pairs)
        for query, items in groups.items():
            documents = [doc for _, doc in items]
            resp = _post_with_retry(
                f"{settings.base_url}/rerank",
                headers={"Authorization": f"Bearer {settings.siliconflow_api_key}"},
                json={
                    "model": settings.rerank_model,
                    "query": query,
                    "documents": documents,
                },
            )
            for r in resp.json().get("results", []):
                idx = r["index"]
                if 0 <= idx < len(items):
                    scores[items[idx][0]] = r["relevance_score"]
        return scores


@lru_cache
def get_reranker() -> SiliconFlowCrossEncoder:
    return SiliconFlowCrossEncoder()
