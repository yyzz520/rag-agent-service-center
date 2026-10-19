"""RAG 问答：检索 → 编号 → 注入 prompt（要求引用 [n]）→ 生成。

返回 answer 和 sources（编号 ↔ 原文片段），前端据 [n] 做溯源展示。
"""

from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate

from app.ai.providers import backup_llm, primary_llm
from app.rag.retrieval import get_rerank_retriever
from app.rag.trace import chunk_id, current_usage

_ANSWER_TEMPERATURE = 0.1


@lru_cache
def get_llm():
    """主供应商 LLM（缓存单例）。fallback 用 get_backup_llm()。"""
    return primary_llm(_ANSWER_TEMPERATURE)


@lru_cache
def get_backup_llm():
    return backup_llm(_ANSWER_TEMPERATURE)


_CITATION_SYSTEM = (
    '你是知识库问答助手。请严格依据下面带编号的资料回答；资料里没有的内容，'
    '直接说"资料中没有相关内容"，不要编造。'
    "回答时在依据某份资料的句子后标注来源编号，例如 [3]；"
    "同一句依据多份资料时全部列出，例如 [1][4]。"
)


def _format_sources(docs: list) -> tuple[str, list[dict]]:
    """给召回分块编号，返回 (带编号的资料文本, 供前端展示的溯源列表)。"""
    context_parts: list[str] = []
    sources: list[dict] = []
    for i, d in enumerate(docs, start=1):
        context_parts.append(f"[{i}]\n{d.page_content}")
        src = d.metadata.get("source")
        sources.append({
            "id": i,
            "chunk_id": chunk_id(d),
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
        ("system", _CITATION_SYSTEM + "\n\n资料：\n{context}"),
        ("human", "问题：{question}"),
    ])
    return prompt | (llm or get_llm())


def retrieve_context(
    question: str, allowed_doc_ids: list[str] | None = None
) -> dict | None:
    """只做检索，返回 {"context", "sources"}；无命中返回 None。流式与一次性共用这一份检索逻辑。"""
    docs = get_rerank_retriever(allowed_doc_ids=allowed_doc_ids).invoke(question)
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
    msg = make_answer_chain(llm).invoke(
        {"context": ctx["context"], "question": question}
    )
    rec = current_usage()
    if rec is not None:
        rec.record(msg)
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    return {"answer": text, "sources": ctx["sources"]}
