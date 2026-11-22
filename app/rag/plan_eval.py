"""Plan-and-Execute 窄场景评测：固定任务 → 真跑规划 → 自动批准 → 执行 → 合成。

与检索/生成评测的分工：这里测的是"多步任务能不能规划对、执行完、答对"，
指标是任务成功率 / 平均步数 / token / 耗时——真调 LLM 和工具，有花费。
评测用 MemorySaver + 独立 thread，不碰线上会话；计划自动 approve（HITL 另测）。
"""
import json
import time
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent.planner import build_plan_agent
from app.config import settings
from app.rag import manifest, prompts
from app.rag.stats import bootstrap_ci
from app.rag.trace import begin_trace, current_usage, end_trace, rotate_jsonl_if_needed

EVAL_DIR = Path(settings.persist_dir).parent / "eval"
TASKS_PATH = EVAL_DIR / "plan_tasks.json"
REPORTS_PATH = EVAL_DIR / "plan_reports.jsonl"


def load_tasks() -> list[dict]:
    if not TASKS_PATH.exists():
        return []
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def _interrupt_payload(result: dict) -> dict | None:
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", interrupts[0])
    return value if isinstance(value, dict) else {"raw": value}


def _text(msg) -> str:
    content = getattr(msg, "content", "")
    return content if isinstance(content, str) else str(content)


def _score(task: dict, plan: list[dict], results: list[dict], answer: str) -> dict:
    tools = {r.get("tool") for r in results}
    missing_tools = [t for t in task.get("expect_tools", []) if t not in tools]
    missing_phrases = [p for p in task.get("expect_answer_contains", []) if p not in answer]
    steps_ok = len(plan) <= task.get("max_steps", settings.agent_plan_max_steps)
    reasons = []
    if missing_tools:
        reasons.append("missing_tools")
    if missing_phrases:
        reasons.append("missing_answer_phrases")
    if not steps_ok:
        reasons.append("too_many_steps")
    return {
        "ok": not reasons,
        "missing_tools": missing_tools,
        "missing_phrases": missing_phrases,
        "steps_ok": steps_ok,
        "reasons": reasons,
    }


def _metrics(items: list[dict]) -> dict:
    total = len(items)
    success = [1.0 if i["score"]["ok"] else 0.0 for i in items]
    ok = int(sum(success))
    done = [i for i in items if not i.get("error")]
    steps = [i["plan_steps"] for i in done]
    tokens = [i["tokens"] for i in done]
    ms = [i["ms"] for i in done]
    failures: dict[str, int] = {}
    for item in items:
        if item.get("error"):
            failures["error"] = failures.get("error", 0) + 1
            continue
        for reason in item["score"]["reasons"]:
            failures[reason] = failures.get(reason, 0) + 1
    return {
        "total": total,
        "success": ok,
        "success_rate": round(ok / total, 3) if total else 0.0,
        "avg_steps": round(sum(steps) / len(steps), 2) if steps else 0.0,
        "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else 0.0,
        "avg_ms": round(sum(ms) / len(ms), 1) if ms else 0.0,
        "failures": failures,
        "ci95": {"success_rate": bootstrap_ci(success)},
    }


def _run_task(task: dict, index: int, agent_factory) -> dict:
    thread = f"plan-eval-{index}"
    token = begin_trace(source="agent", conversation_id=thread)
    started = time.perf_counter()
    try:
        agent = agent_factory(None, checkpointer=MemorySaver(), conversation_id=thread)
        config = {
            "configurable": {"thread_id": thread},
            "recursion_limit": max(30, settings.agent_plan_max_steps * 3 + 10),
        }
        result = agent.invoke(
            {"messages": [HumanMessage(task["question"])]}, config=config
        )
        payload = _interrupt_payload(result)
        plan = payload.get("steps", []) if payload else []
        if payload is not None:
            result = agent.invoke(Command(resume={"type": "approve"}), config=config)
        results = result.get("results") or []
        messages = result.get("messages") or []
        answer = _text(messages[-1]).strip() if messages else ""
        usage = current_usage()
        summary = usage.summary() if usage is not None else None
        return {
            "question": task["question"],
            "plan": plan,
            "plan_steps": len(plan),
            "results": [
                {**r, "result": str(r.get("result", ""))[:500]} for r in results
            ],
            "answer": answer[:2000],
            "tokens": int((summary or {}).get("total_tokens") or 0),
            "model_calls": int((summary or {}).get("model_calls") or 0),
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "score": _score(task, plan, results, answer),
        }
    except Exception as exc:
        return {
            "question": task["question"],
            "plan": [],
            "plan_steps": 0,
            "results": [],
            "answer": "",
            "tokens": 0,
            "model_calls": 0,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
            "score": {"ok": False, "reasons": ["error"], "missing_tools": [], "missing_phrases": [], "steps_ok": False},
        }
    finally:
        end_trace(token)


def run_plan_eval(note: str | None = None, agent_factory=None) -> dict:
    tasks = load_tasks()
    if not tasks:
        raise ValueError(f"没有计划评测集：请先建 {TASKS_PATH}")
    factory = agent_factory or build_plan_agent
    items = [_run_task(task, i, factory) for i, task in enumerate(tasks)]
    report = {
        "ts": time.time(),
        "note": note,
        "prompt_version": prompts.versions(("plan", "plan_synth")),
        "dataset": manifest.dataset_manifest(TASKS_PATH),
        "metrics": _metrics(items),
        "items": items,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    rotate_jsonl_if_needed(REPORTS_PATH)
    return report


def read_plan_reports() -> list[dict]:
    if not REPORTS_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in REPORTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
