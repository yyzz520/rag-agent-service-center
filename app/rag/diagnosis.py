"""规则化 badcase 诊断：把评测结果归因到解析/召回/精排/生成/拒答/Agent 路由。

只读最近一次评测报告，不调模型、不重新跑检索。诊断是"给下一步改哪层"的线索，
不是因果断言；置信度统一按规则命中给。
"""
import time

from app.rag.agent_eval import read_agent_reports
from app.rag.eval import read_reports
from app.rag.gen_eval import read_gen_reports

_SUGGESTIONS = {
    "retrieval_miss": "正确块没进候选池：先查解析/切块质量，再看召回参数（top-k/查询改写）。",
    "rerank_rank": "已召回但排得不够前：检查 rerank、RRF 权重和候选池大小。",
    "generation_incorrect_cited": "召回了依据但答案错/不完整：检查 prompt、上下文拼接和生成约束。",
    "generation_incorrect_uncited": "答案既不对也没引用：先查是否真的召回，再看生成与引用约束。",
    "refusal_failed": "负样本没拒答：检查系统提示词和检索无命中时的兜底话术。",
    "refusal_citation": "拒答了但仍带引用：拒答分支应禁止输出 [n] 引用。",
    "citation_wrong": "答案对但引用指向错误来源：检查 [n] 编号映射和 sources 返回。",
    "citation_missing": "答案对但没有引用：检查生成提示词是否强制标 [n]。",
    "judge_error": "裁判调用失败：评测本身的问题，先修 judge，不要当成模型效果差。",
    "agent_routing": "Agent 工具路由不对：检查工具描述、expect_tools 和提示词。",
    "agent_completion_retrieval_suspect": "Agent 调了知识库仍答错：优先查检索召回/切块/来源，再查生成。",
    "agent_completion_generation_or_routing": "Agent 没调知识库就答错：查工具路由、生成或工具返回。",
}


def _add(items: list[dict], layer: str, category: str, question: str, evidence: dict) -> None:
    items.append({
        "layer": layer,
        "category": category,
        "question": question,
        "evidence": evidence,
        "suggestion": _SUGGESTIONS.get(category, ""),
    })


def diagnose_retrieval(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        rank = item.get("rank")
        if not item.get("hit"):
            _add(out, "retrieval", "retrieval_miss", item.get("question", ""), {"rank": rank})
        elif rank and rank > 1:
            _add(out, "retrieval", "rerank_rank", item.get("question", ""), {"rank": rank})
    return out


def diagnose_generation(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        question = item.get("question", "")
        if item.get("category") == "negative":
            reason = item.get("reason")
            if reason == "refused_but_cited":
                _add(out, "generation", "refusal_citation", question, {"reason": reason})
            elif reason == "not_refused":
                _add(out, "generation", "refusal_failed", question, {"reason": reason})
            continue
        if item.get("judge_error"):
            _add(out, "generation", "judge_error", question, {"reason": item.get("reason")})
        elif not item.get("correct"):
            category = "generation_incorrect_cited" if item.get("cited") else "generation_incorrect_uncited"
            _add(out, "generation", category, question, {
                "cited": item.get("cited"),
                "reason": item.get("reason"),
            })
        elif not item.get("cited"):
            _add(out, "generation", "citation_missing", question, {"cited": item.get("cited")})
        elif not item.get("citation_correct"):
            _add(out, "generation", "citation_wrong", question, {
                "cited_ids": item.get("cited_ids"),
                "citation_correct": item.get("citation_correct"),
            })
    return out


def diagnose_agent(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        question = item.get("question", "")
        if not item.get("routed"):
            _add(out, "agent", "agent_routing", question, {
                "tools_used": item.get("tools_used"),
                "expect_tools": item.get("expect_tools"),
            })
        if not item.get("answer_ok"):
            tools = item.get("tools_used") or []
            category = (
                "agent_completion_retrieval_suspect"
                if "search_knowledge_base" in tools
                else "agent_completion_generation_or_routing"
            )
            _add(out, "agent", category, question, {
                "tools_used": tools,
                "answer": (item.get("answer") or "")[:200],
            })
    return out


def _latest(runs: list[dict]) -> dict:
    return runs[-1] if runs else {}


def run_diagnosis() -> dict:
    retrieval_run = _latest(read_reports())
    generation_run = _latest(read_gen_reports())
    agent_run = _latest(read_agent_reports())

    retrieval = diagnose_retrieval(retrieval_run.get("items", []))
    generation = diagnose_generation(generation_run.get("items", []))
    agent = diagnose_agent(agent_run.get("items", []))
    all_items = retrieval + generation + agent
    summary: dict[str, int] = {}
    for item in all_items:
        summary[item["category"]] = summary.get(item["category"], 0) + 1

    return {
        "ts": time.time(),
        "summary": summary,
        "items": all_items,
        "runs": {
            "retrieval": {"note": retrieval_run.get("note"), "metrics": retrieval_run.get("metrics")},
            "generation": {"note": generation_run.get("note"), "metrics": generation_run.get("metrics")},
            "agent": {"note": agent_run.get("note"), "metrics": agent_run.get("metrics")},
        },
    }
