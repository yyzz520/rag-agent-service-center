from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate

from app.ai.providers import primary_llm
from app.rag import cache
from app.rag.trace import current_usage


@lru_cache
def get_rewrite_llm():
    return primary_llm(0.0)


@lru_cache
def _build_rewrite_model():
    """改写查询直接输出 AIMessage：既取文本也要拿 usage_metadata 记账。"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是信息检索查询优化器。把用户问题改写成 2-3 个最适合检索的关键词查询，每行一个，不要编号和多余文字。"
                   "要求：保留原文措辞，同时补充常见同义表述和文档里可能用的术语。"
                   "例如「某某集团的总负债是多少」→\n总负债\n负债合计\n某某集团 负债"),
        ("human", "问题：{question}"),
    ])
    return prompt | get_rewrite_llm()


def rewrite_query(question: str) -> list[str]:
    """把问句改写成多个检索查询，第一个始终是原文，保证不丢失原意。

    结果走 Redis 缓存（key 只跟问句有关 → 跨用户共享）。只缓存成功结果：
    模型调用失败时落在这里的是降级成"只有原文"的列表，缓存它会把一次偶发失败
    固化成长期行为，之后命中缓存就再也拿不到改写结果了。
    """
    cached = cache.get_rewrite(question)
    if cached is not None:
        return cached
    variants = [question]
    ok = False
    try:
        msg = _build_rewrite_model().invoke({"question": question})
        rec = current_usage()
        if rec is not None:
            rec.record(msg)
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        variants += [v.strip() for v in text.splitlines() if v.strip()]
        ok = True
    except Exception:
        pass
    if ok:
        cache.set_rewrite(question, variants)
    return variants
