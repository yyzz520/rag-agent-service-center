"""模型供应商统一抽象（M3 加固）。

所有 LLM / Embedding 客户端都从这构造：统一 request_timeout 与 max_retries
（SDK 内部对 408/409/429/5xx 做指数退避重试），不再各自散落 new 一个。

主备供应商：`.env` 配了 backup_base_url/api_key/llm_model 三件套才算启用备用，
否则 has_backup()=False、所有 fallback 分支不生效——这是默认状态，行为与改造前一致。

fallback 边界（诚实口径）：这里只对**无状态可重放的调用**做"主挂→重试备份"，
由 rag/agent 路由层在异常处触发。Embedding 换供应商会让已入库向量全部失效
（需重建向量库），所以 Embedding 只在构造层给足重试，不做供应商级切换。
"""
from functools import lru_cache

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings


def _chat(base_url: str, api_key: str, model: str, temperature: float) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_retries=settings.llm_max_retries,
        request_timeout=settings.llm_timeout_seconds,
    )


def primary_llm(temperature: float) -> ChatOpenAI:
    return _chat(
        settings.base_url, settings.siliconflow_api_key, settings.llm_model, temperature
    )


def has_backup() -> bool:
    return bool(
        settings.backup_base_url
        and settings.backup_api_key
        and settings.backup_llm_model
    )


def backup_llm(temperature: float) -> ChatOpenAI | None:
    if not has_backup():
        return None
    return _chat(
        settings.backup_base_url,
        settings.backup_api_key,
        settings.backup_llm_model,
        temperature,
    )


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    """Embedding 客户端（懒加载单例）。换供应商需重建向量库，故只保证重试/超时。"""
    return OpenAIEmbeddings(
        base_url=settings.base_url,
        api_key=settings.siliconflow_api_key,
        model=settings.embed_model,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
    )
