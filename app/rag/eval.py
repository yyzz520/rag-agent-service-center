"""评测闭环：黄金问题集 → 自动重放检索 → recall@k / MRR → 报告落盘（支持改参重跑对比）。

对库里文档预置一组"黄金问题"（golden set），每条的期望是"该召回哪个文档里的哪段内容"。
评测时把线上同一条检索链路原样跑一遍（改写 → 混合 → rerank），看期望的那块有没有进
最终候选、排第几，聚合成两个业界标准、可解释的指标：
  - recall@k：期望块进入 top-k 的比例（k = 本次最终给 LLM 的 max_docs）
  - MRR：平均倒数排名，衡量期望块排得有多靠前
这两个指标只重放检索、不让 LLM 生成答案，所以稳定、便宜、不受生成波动影响。

改参对比的核心是：黄金问题集固定（输入不变）→ 只有检索参数在变 → 分数的任何变化
都能归因到参数。POST /rag/eval 的 body 可直接覆盖检索参数（candidate_pool /
per_query_top / max_docs / use_rerank），Swagger 上不用改代码就能"同一组题、两套参数、
跑两遍、GET /rag/eval 对比分数"。use_rerank=False 是最大杠杆：关掉 rerank 精排、
只看纯 RRF 粗筛的效果，rerank 到底把分数拉高多少一对比就出来。
每次运行把实际生效的参数快照 + 逐题结果 + 指标追加到 reports.jsonl。
"""
import json
import time
from pathlib import Path

from app.config import settings
from app.rag import cache
from app.rag.retrieval import RAGRetriever
from app.rag.trace import rotate_jsonl_if_needed  # 与 trace 共用同一轮转口径

EVAL_DIR = Path(settings.persist_dir).parent / "eval"  # data/eval，跟随 persist_dir
GOLDEN_PATH = EVAL_DIR / "golden.json"
REPORTS_PATH = EVAL_DIR / "reports.jsonl"

# RAGRetriever 上允许被 POST /eval 覆盖的参数（其余字段不放开，避免乱改破坏链路）
PARAM_KEYS = ("candidate_pool", "per_query_top", "max_docs", "use_rerank")


def load_golden() -> list[dict]:
    """黄金问题集：每项 {question, doc?, expect_source, expect_contains}。

    expect_source 用源文件名（uploads/xx），expect_contains 用该块内独特的内容短语，
    而不是硬编码 chunk_id——重入库后块 ID 会变，短语在则期望仍成立。
    """
    if not GOLDEN_PATH.exists():
        return []
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _resolve_doc_ids(doc_id: str | None) -> list[str] | None:
    """黄金问题里的 doc 字段现在就是 uuid（doc_id）本身；不填 = 全库（管理口径）。"""
    if not doc_id:
        return None
    return [doc_id]


def _retriever_for(item: dict, overrides: dict) -> RAGRetriever:
    """构造本次评测要用的检索器：黄金问题限定范围 + 参数覆盖 + 不污染线上 trace。"""
    return RAGRetriever(
        allowed_doc_ids=_resolve_doc_ids(item.get("doc")),
        record_trace=False,  # 评测重放不该污染线上问答的 trace 配对日志
        **{k: overrides[k] for k in PARAM_KEYS if overrides.get(k) is not None},
    )


def _replay(item: dict, retriever: RAGRetriever) -> dict:
    """对一条黄金问题原样重放检索，返回是否召回及命中的名次。"""
    docs = retriever.invoke(item["question"])
    expect_source = item["expect_source"]
    expect_contains = item["expect_contains"]
    for rank, d in enumerate(docs, start=1):
        source = d.metadata.get("source") or ""
        if expect_source in source and expect_contains in d.page_content:
            return {
                "question": item["question"],
                "expect": f"{expect_source}｜{expect_contains}",
                "hit": True,
                "rank": rank,
            }
    return {
        "question": item["question"],
        "expect": f"{expect_source}｜{expect_contains}",
        "hit": False,
        "rank": None,
    }


def _metrics(results: list[dict], k: int) -> dict:
    """把逐题结果聚合成指标。k = 本次 max_docs（recall@{k} 的衡量线跟着当前参数走）。"""
    total = len(results)
    hits = [r for r in results if r["hit"]]
    rank_leq = lambda t: sum(1 for r in results if r["hit"] and r["rank"] <= t)

    metrics = {f"recall@{k}": round(rank_leq(k) / total, 3)}
    if k != 1:
        metrics["recall@1"] = round(rank_leq(1) / total, 3)
    if k >= 5:
        metrics["recall@5"] = round(rank_leq(5) / total, 3)
    metrics["mrr"] = round(sum(1.0 / r["rank"] for r in hits) / total, 3)
    metrics["hits"] = len(hits)
    metrics["total"] = total
    return metrics


def run_eval(note: str | None = None, overrides: dict | None = None) -> dict:
    """跑一轮评测，报告追加到 reports.jsonl 并返回。

    overrides 只取 PARAM_KEYS 里的非空值；缺省项沿用 RAGRetriever 类默认值，
    参数快照从实际构造出的检索器读取（永远与真实执行一致）。
    """
    golden = load_golden()
    if not golden:
        raise ValueError(f"没有黄金问题集：请先建 {GOLDEN_PATH}")
    overrides = overrides or {}
    probe = RAGRetriever(**{k: overrides[k] for k in PARAM_KEYS if overrides.get(k) is not None})
    params = {k: getattr(probe, k) for k in PARAM_KEYS}

    results = []
    # 整层旁路缓存：评测要跑的是"当前参数下检索链路真实的表现"。命中改写/embedding
    # 缓存会让第二轮起分数虚高且不可复现——那样测出来的是缓存命中率，不是检索质量。
    with cache.bypass():
        for item in golden:
            retriever = _retriever_for(item, overrides)
            results.append(_replay(item, retriever))

    report = {
        "ts": time.time(),
        "note": note,
        "metrics": _metrics(results, params["max_docs"]),
        "params": params,
        "items": results,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    rotate_jsonl_if_needed(REPORTS_PATH)
    return report


def read_reports() -> list[dict]:
    """历史评测报告（新的在后），供改参数重跑后对比。"""
    if not REPORTS_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in REPORTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
