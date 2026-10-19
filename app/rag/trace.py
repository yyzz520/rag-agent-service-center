"""检索/回答 trace：记录每次问答召回的块和答案，供离线评估。

数据以 JSONL 追加到 data/trace/trace.jsonl（跟随 persist_dir，重启不丢）。
每次问答在入口生成 trace_id，用 ContextVar 贯穿链路——同一轮的检索/回答记录共享同一
trace_id（类似分布式追踪的 trace_id 线程本地传递）；agent 的记录再带 conversation_id，
能按会话归拢。评估按 chunk_id（内容哈希，跨运行稳定）与 golden set 比对。
"""
import hashlib
import json
import time
import uuid
from contextvars import ContextVar, Token
from pathlib import Path

from app.config import settings
from app.rag.usage import UsageRecorder

_trace_dir = Path(settings.persist_dir).parent / "trace"
_trace_path = _trace_dir / "trace.jsonl"

# 当前请求的追踪上下文：API 入口 begin_trace()，finally 里 end_trace() 复位。
# 同步接口跑在线程池、线程会被复用，所以必须用 ContextVar 而非全局变量。
_trace_ctx: ContextVar[dict | None] = ContextVar("trace_ctx", default=None)


def begin_trace(
    *,
    source: str,
    conversation_id: str | None = None,
    trace_id: str | None = None,
) -> Token:
    """开启一条链路追踪：trace_id 缺省自动生成，会话里所有记录共享它。"""
    ctx = {
        "trace_id": trace_id or uuid.uuid4().hex[:12],
        "source": source,  # 'rag' | 'agent'
        "conversation_id": conversation_id,
        # 本次问答的 LLM token/步数记账：各调用点 record()，log_answer 收尾并进 answer 记录
        "usage": UsageRecorder(),
    }
    return _trace_ctx.set(ctx)


def end_trace(token: Token) -> None:
    _trace_ctx.reset(token)


def current_usage() -> UsageRecorder | None:
    """当前请求的 token 记账器（无请求上下文/非请求线程内返回 None，调用点跳过即可）。"""
    ctx = _trace_ctx.get()
    return ctx.get("usage") if ctx else None


def _ctx_fields() -> dict:
    ctx = _trace_ctx.get()
    if not ctx:
        return {"trace_id": None, "source": None, "conversation_id": None}
    return {
        "trace_id": ctx["trace_id"],
        "source": ctx["source"],
        "conversation_id": ctx["conversation_id"],
    }


def chunk_id(doc) -> str:
    """用内容哈希当稳定 chunk 标识：同一段文本每次算出来一样，可跨运行比对。"""
    return hashlib.md5(doc.page_content.encode("utf-8")).hexdigest()[:12]


def _append(record: dict) -> None:
    _trace_dir.mkdir(parents=True, exist_ok=True)
    merged = {**_ctx_fields(), **record}
    with _trace_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(merged, ensure_ascii=False) + "\n")
    rotate_jsonl_if_needed(_trace_path)


def rotate_jsonl_if_needed(path: Path) -> None:
    """追加型 JSONL 轮转：超过阈值字节时按行数截尾保留最近 N 条，防磁盘无界增长。

    只读判断用 stat 大小（O(1)），超限才整读重写一次；写失败静默跳过不影响业务。
    """
    try:
        if not path.exists() or path.stat().st_size < settings.jsonl_rotate_bytes:
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        keep = max(1, settings.jsonl_keep_entries)
        if len(lines) > keep:
            path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def log_retrieval(
    question: str,
    rewritten_queries: list[str],
    docs: list,
    rerank_details: dict | None = None,
    cache_hit: bool = False,
) -> None:
    """记录一次问答召回的块（含来源/页码）与 rerank 打分快照，供评估召回质量。

    rerank_details 按每个改写查询记录 rerank 选中块：rrf_rank 是 RRF 融合后的名次，
    rerank_score 是 bge-reranker 的相关性分——两者合起来能看出"为什么这块被选中"。
    cache_hit=True 时是命中 Redis 缓存直接返回，没有本次的改写/rerank 过程可记。
    """
    record = {
        "ts": time.time(),
        "kind": "retrieval",
        "question": question,
        "rewritten_queries": rewritten_queries,
        "recalled_chunks": [
            {
                "chunk_id": chunk_id(d),
                "source": d.metadata.get("source"),
                "page": d.metadata.get("page"),
            }
            for d in docs
        ],
    }
    if rerank_details:
        record["rerank_details"] = rerank_details
    if cache_hit:
        record["cache_hit"] = True
    _append(record)


def log_answer(question: str, answer: str) -> None:
    """记录最终答案，供评估"答案质量"，与同 trace_id 的 retrieval 记录配对。

    顺带把本次问答的 LLM 消耗（token/模型调用/工具步数）并进来——每 run 记账落点。
    """
    record: dict = {
        "ts": time.time(),
        "kind": "answer",
        "question": question,
        "answer": answer,
    }
    usage = current_usage()
    if usage is not None and usage.model_calls:
        record["usage"] = usage.summary()
    _append(record)


def read_trace() -> list[dict]:
    if not _trace_path.exists():
        return []
    return [json.loads(line) for line in _trace_path.read_text(encoding="utf-8").splitlines()]
