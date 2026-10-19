"""agent 行为评测（真跑 LLM）：固定任务集 → 真跑一轮 ReAct → 工具路由/完成度/步数/消耗。

与 eval.py 的分工：eval.py 只重放检索链路（不起 LLM，稳定、便宜、可反复跑）；这里测的是
**agent 的行为**——该调工具时有没有调、有没有调对、答案对不对，这些只有真跑模型才测得出来。
代价是每次运行都烧真 token，所以任务集刻意压到个位数且固定不变：指标可比性来自"题不变"，
不是来自"跑得多"。同题多跑看稳定性这轮不做（成本 ×N），作为已知缺口记在文档里。

与线上会话完全隔离：每个任务用一次性 thread_id，跑完即弃，不碰任何真实会话的 checkpointer。
检索结果缓存照常生效（只缓检索、工具仍会被真实调用），所以不会扭曲"工具路由"这个指标。
"""
import json
import time
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage

from app.agent.graph import build_agent, open_sync_checkpointer
from app.config import settings
from app.rag.trace import begin_trace, current_usage, end_trace, rotate_jsonl_if_needed

EVAL_DIR = Path(settings.persist_dir).parent / "eval"  # data/eval，与 RAG 评测同目录
TASKS_PATH = EVAL_DIR / "agent_tasks.json"
REPORTS_PATH = EVAL_DIR / "agent_reports.jsonl"


def load_tasks() -> list[dict]:
    """任务集：每项 {question, expect_tools?, expect_answer_contains?, forbid_tools?, doc?}。

    expect_tools 是"必须用到的工具"，按子集判定（多调别的工具不算错）；
    forbid_tools=True 用于闲聊类任务，要求一个工具都不调——这类最容易暴露"逢问必检索"
    的过度调用。两处都省略 expect_answer_contains 时不校验答案内容。
    """
    if not TASKS_PATH.exists():
        return []
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def _text(msg) -> str:
    content = getattr(msg, "content", "")
    return content if isinstance(content, str) else str(content)


def _run_task(task: dict) -> dict:
    """跑一条任务：全新 thread_id（不污染真实会话），返回工具路由与答案是否达标。"""
    question = task["question"]
    allowed = [task["doc"]] if task.get("doc") else None
    conv = f"agent-eval-{uuid.uuid4().hex[:12]}"
    token = begin_trace(source="agent_eval", conversation_id=conv)
    try:
        with open_sync_checkpointer() as saver:
            agent = build_agent(allowed, checkpointer=saver)
            config = {
                "configurable": {"thread_id": conv},
                "recursion_limit": settings.agent_recursion_limit,
            }
            # 全新线程：invoke 返回的消息就是本轮全部消息，不需要像线上那样切尾部
            result = agent.invoke({"messages": [HumanMessage(content=question)]}, config=config)
            messages = result["messages"]
            answer = _text(messages[-1])
            tools_used = [m.name for m in messages if getattr(m, "type", "") == "tool"]
            # 记账：每条 AIMessage 带 usage_metadata（一次模型调用一条）
            usage = current_usage()
            summary = None
            if usage is not None:
                for m in messages:
                    if getattr(m, "type", "") == "ai" and getattr(m, "usage_metadata", None):
                        usage.record(m)
                usage.tool_calls = len(tools_used)
                summary = usage.summary()
    finally:
        end_trace(token)

    expect_tools = task.get("expect_tools") or []
    forbid = bool(task.get("forbid_tools"))
    return {
        "question": question,
        "tools_used": tools_used,
        "expect_tools": expect_tools,
        "forbid_tools": forbid,
        "routed": set(expect_tools).issubset(tools_used) and not (forbid and tools_used),
        "answer": answer,
        "answer_ok": (task.get("expect_answer_contains") or "") in answer,
        "tokens": (summary or {}).get("total_tokens", 0),
    }


def _metrics(results: list[dict]) -> dict:
    total = len(results)
    if not total:
        return {}
    return {
        "tool_route_accuracy": round(sum(r["routed"] for r in results) / total, 3),
        "completion_rate": round(sum(r["answer_ok"] for r in results) / total, 3),
        "avg_tool_steps": round(sum(len(r["tools_used"]) for r in results) / total, 2),
        "avg_tokens": round(sum(r["tokens"] for r in results) / total, 1),
        "tasks": total,
    }


def run_agent_eval(note: str | None = None) -> dict:
    """跑一轮 agent 评测，报告追加到 agent_reports.jsonl 并返回。

    无法覆盖参数：agent 的行为由 prompt + 工具集决定，不像检索有可直接调的数值参数。
    要对照就改代码/prompt 后重跑，看 GET /rag/agent-eval 里前后两次的指标差。
    """
    tasks = load_tasks()
    if not tasks:
        raise ValueError(f"没有 agent 任务集：请先建 {TASKS_PATH}")
    results = [_run_task(t) for t in tasks]
    report = {
        "ts": time.time(),
        "note": note,
        "metrics": _metrics(results),
        "items": results,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    rotate_jsonl_if_needed(REPORTS_PATH)
    return report


def read_agent_reports() -> list[dict]:
    """历史 agent 评测报告（新的在后）。"""
    if not REPORTS_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in REPORTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
