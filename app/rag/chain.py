"""RAG 问答：检索 → 编号 → 注入 prompt（要求引用 [n]）→ 生成。

返回 answer 和 sources（编号 ↔ 原文片段），前端据 [n] 做溯源展示。
"""

from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate

from app.ai.providers import backup_llm, primary_llm
from app.rag.context import expand_parents
from app.rag.guard import sanitize_context
from app.rag.prompts import get_prompt
from app.rag.refusal import finalize
from app.rag.retrieval import get_rerank_retriever
from app.rag.trace import chunk_id, current_usage, span

_ANSWER_TEMPERATURE = 0.1


@lru_cache
def get_llm():
    """主供应商 LLM（缓存单例）。fallback 用 get_backup_llm()。"""
    return primary_llm(_ANSWER_TEMPERATURE)


@lru_cache
def get_backup_llm():
    return backup_llm(_ANSWER_TEMPERATURE)


def _format_sources(docs: list) -> tuple[str, list[dict]]:
    """给召回分块编号，返回 (带编号的资料文本, 供前端展示的溯源列表)。"""
    context_parts: list[str] = []
    sources: list[dict] = []
    for i, d in enumerate(docs, start=1):
        # 只过滤进 prompt 的上下文；sources 保留原文，前端溯源仍看到原始内容。
        context_parts.append(f"[{i}]\n<document>\n{sanitize_context(d.page_content)}\n</document>")
        src = d.metadata.get("source")
        sources.append({
            "id": i,
            "chunk_id": chunk_id(d),
            "vector_id": d.metadata.get("chunk_id"),
            "doc_id": d.metadata.get("doc_id"),
            "chunk_level": d.metadata.get("chunk_level"),
            "parent_id": d.metadata.get("parent_id"),
            "source": src,
            "page": d.metadata.get("page"),
            "content": d.page_content,
        })
    return "\n\n".join(context_parts), sources


def make_answer_chain(llm=None):
    """生成链路：检索结果已编号后走 带溯源要求的 prompt → LLM → AIMessage。

    直接吐 AIMessage 而非纯文本：调用方既取 content 也能读 usage_metadata 记账。
    流式 .astream 复用同一条链，保证流式与一次性输出同 prompt、同模型。
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", get_prompt("citation").text + "\n\n资料：\n{context}"),
        ("human", "问题：{question}"),
    ])
    return prompt | (llm or get_llm())


def retrieve_context(
    question: str, allowed_doc_ids: list[str] | None = None
) -> dict | None:
    """只做检索，返回 {"context", "sources"}；无命中返回 None。流式与一次性共用这一份检索逻辑。"""
    with span("retrieval", question_len=len(question)):
        docs = expand_parents(
            get_rerank_retriever(allowed_doc_ids=allowed_doc_ids).invoke(question)
        )
    if not docs:
        return None
    context, sources = _format_sources(docs)
    return {"context": context, "sources": sources}


def run_rag(
    question: str, allowed_doc_ids: list[str] | None = None, llm=None
) -> dict:
    """检索 + 生成，返回 {"answer", "sources"}；无命中时 sources 为空。

    allowed_doc_ids 传文档 uuid 列表时只在这些文档内检索（多租户隔离的收窄口径）。
    llm 为空用主供应商；路由层做 fallback 时传备份 LLM 整体重跑（检索是纯函数，可安全重放）。
    """
    ctx = retrieve_context(question, allowed_doc_ids)
    if ctx is None:
        return {"answer": "资料中没有相关内容，无法回答。", "sources": []}
    with span("generation"):
        msg = make_answer_chain(llm).invoke(
            {"context": ctx["context"], "question": question}
        )
    rec = current_usage()
    if rec is not None:
        rec.record(msg)
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    # 拒答不该带引用：整段拒答时去掉 [n] 并清空 sources，评测与前端都按收口后的结果算。
    answer, sources = finalize(text, ctx["sources"])
    return {"answer": answer, "sources": sources}
