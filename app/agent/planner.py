"""Plan-and-Execute：生成计划 → 人工审批 → 按步执行工具 → 合成答案。

- 计划和各步结果都在 checkpointer 的 state 里（thread_id=conversation_id），进度可恢复；
- 计划审批用 LangGraph interrupt：一次批准覆盖计划内全部步骤（含外部工具）；
- 步骤数有配置上限，执行失败只记录、不自动 replan（明确边界，避免死循环）；
- 演示场景使用项目自带的虚构医药/仓储数据，不涉及真实业务材料。
"""
import json
import re
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from app.agent import toolkit
from app.ai.providers import primary_llm
from app.config import settings
from app.rag.prompts import get_prompt
from app.rag.trace import current_usage


class PlanState(TypedDict):
    messages: Annotated[list, add_messages]
    plan: list[dict]
    step_index: int
    results: list[dict]


def _text(msg) -> str:
    content = getattr(msg, "content", "")
    return content if isinstance(content, str) else str(content)


def _question(messages: list) -> str:
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "human":
            return _text(msg).strip()
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_plan(raw, tools: dict) -> list[dict]:
    steps: list[dict] = []
    seen: set[tuple] = set()
    for item in (raw or [])[: settings.agent_plan_max_steps]:
        if not isinstance(item, dict):
            continue
        name = item.get("tool")
        args = item.get("args") or {}
        if name not in tools or not isinstance(args, dict):
            continue
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue  # 同一工具同参数只保留一次
        seen.add(key)
        steps.append({
            "goal": str(item.get("goal", ""))[:200],
            "tool": name,
            "args": args,
        })
    return steps


def _tool_spec(name: str, tool) -> str:
    schema = getattr(tool, "args", None)
    if not schema:
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is not None and hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
    spec = f"- {name}: {tool.description}"
    if schema and settings.agent_plan_include_schema:
        spec += f"\n  参数 JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
    return spec


def generate_plan(question: str, tools: dict, llm) -> list[dict]:
    specs = "\n".join(_tool_spec(name, tool) for name, tool in tools.items())
    prompt = get_prompt("plan").text.format(
        tools=specs, question=question, max_steps=settings.agent_plan_max_steps
    )
    msg = llm.invoke(prompt)
    rec = current_usage()
    if rec is not None:
        rec.record(msg)
    match = re.search(r"\{.*\}", _text(msg), re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return _validate_plan(data.get("steps"), tools)


def synthesize(question: str, results: list[dict], llm) -> str:
    prompt = get_prompt("plan_synth").text.format(
        question=question, results=json.dumps(results, ensure_ascii=False, indent=2)
    )
    msg = llm.invoke(prompt)
    rec = current_usage()
    if rec is not None:
        rec.record(msg)
    return _text(msg)


def build_plan_agent(
    allowed_doc_ids: list[str] | None,
    *,
    checkpointer,
    llm=None,
    conversation_id: str = "",
    tool_registry: dict | None = None,
    scope_doc: str | None = None,
):
    model = llm or primary_llm(0.0)
    tools = tool_registry if tool_registry is not None else toolkit.tool_map(allowed_doc_ids)
    scope = conversation_id or "plan"

    def planner(state: PlanState):
        question = _question(state.get("messages") or [])
        if not question:
            return {"plan": [], "messages": [AIMessage(content="缺少问题，无法规划。")]}
        plan = generate_plan(question, tools, model)
        if not plan:
            return {"plan": [], "messages": [
                AIMessage(content="无法生成可执行计划，请把问题描述得更具体。")
            ]}
        return {"plan": plan, "step_index": 0, "results": []}

    def approval(state: PlanState):
        if not state.get("plan"):
            return {}
        decision = interrupt({
            "kind": "plan_approval",
            "conversation_id": scope,
            "doc": scope_doc,  # 审批时必须带同一个检索范围，避免悄悄扩大
            "created_at": _now_iso(),
            "expires_in": settings.agent_approval_timeout,
            "steps": state["plan"],
        })
        decision = decision if isinstance(decision, dict) else {}
        kind = decision.get("type")
        if kind == "approve":
            return {}
        if kind == "edit":
            plan = _validate_plan(decision.get("steps"), tools)
            if plan:
                return {"plan": plan, "step_index": 0, "results": []}
            return {"plan": [], "messages": [
                AIMessage(content="修改后的计划无效，未执行任何步骤。")
            ]}
        return {"plan": [], "messages": [
            AIMessage(content="计划未获批准，未执行任何步骤。")
        ]}

    def executor(state: PlanState):
        index = state.get("step_index", 0)
        plan = state.get("plan") or []
        if index >= len(plan):
            return {}
        step = plan[index]
        tool = tools.get(step["tool"])
        try:
            result = str(tool.invoke(step.get("args") or {})) if tool else "工具不存在，未执行"
        except Exception as exc:
            result = f"步骤执行失败：{exc}"
        rec = current_usage()
        if rec is not None:
            rec.count_tool()
        results = [*(state.get("results") or []), {
            "step": index + 1,
            "goal": step.get("goal", ""),
            "tool": step.get("tool"),
            "result": result,
        }]
        return {"results": results, "step_index": index + 1}

    def synth(state: PlanState):
        question = _question(state.get("messages") or [])
        answer = synthesize(question, state.get("results") or [], model)
        return {"messages": [AIMessage(content=answer)]}

    def after_approval(state: PlanState) -> str:
        return "execute" if state.get("plan") else "end"

    def after_execute(state: PlanState) -> str:
        return "execute" if state.get("step_index", 0) < len(state.get("plan") or []) else "synth"

    graph = StateGraph(PlanState)
    graph.add_node("planner", planner)
    graph.add_node("approval", approval)
    graph.add_node("execute", executor)
    graph.add_node("synth", synth)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "approval")
    graph.add_conditional_edges("approval", after_approval, {"execute": "execute", "end": END})
    graph.add_conditional_edges("execute", after_execute, {"execute": "execute", "synth": "synth"})
    graph.add_edge("synth", END)
    return graph.compile(checkpointer=checkpointer)
