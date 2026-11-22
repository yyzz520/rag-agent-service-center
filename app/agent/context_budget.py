"""单轮上下文预算：超长工具结果落盘 + 截断，防一次长任务把上下文窗口顶爆。

只改本轮（最后一条 human 之后）的 ToolMessage 正文，不动 tool_call_id 配对：
- 单条超过 max_chars：完整原文写到 data/agent_tool_results/<scope>/<call_id>.txt，
  正文截断并加标记（落盘失败不阻断业务，标记里注明）；
- 本轮工具结果总量仍超预算：更早的结果进一步压到 1000 字，最近 keep_recent 条保持完整。

注意：这是"每次喂多少"的治理，不替代 checkpointer——checkpoint 保的是可恢复，
恢复时若把全量历史再喂回模型，照样会爆；两件事要配合，不能互相替代。
"""
import re
from pathlib import Path

from langchain_core.messages import RemoveMessage, ToolMessage

from app.config import settings

MARK = "…[已截断"
_OLD_LIMIT = 1000


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name))[:80] or "x"


def _turn(messages: list) -> list:
    for i in range(len(messages) - 1, -1, -1):
        if getattr(messages[i], "type", "") == "human":
            return messages[i:]
    return messages


def _text(m) -> str:
    content = getattr(m, "content", "")
    return content if isinstance(content, str) else str(content)


def _save(scope: str, call_id: str, content: str) -> str | None:
    try:
        folder = Path(settings.agent_tool_result_dir) / _safe(scope)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_safe(call_id)}.txt"
        path.write_text(content, encoding="utf-8")
        return str(path)
    except OSError:
        return None


def _replace(msg, text: str) -> list:
    return [
        RemoveMessage(id=msg.id),
        ToolMessage(
            content=text,
            tool_call_id=msg.tool_call_id,
            name=getattr(msg, "name", None),
            id=msg.id,
        ),
    ]


def apply_budget(messages: list, scope: str) -> dict | None:
    """在 pre_model_hook 里调用；返回 {"messages": [...]} 表示需要替换，None 表示无需改动。"""
    if not messages:
        return None
    turn = _turn(messages)
    tool_msgs = [m for m in turn if getattr(m, "type", "") == "tool" and m.id]
    if not tool_msgs:
        return None

    texts = {m.id: _text(m) for m in tool_msgs}
    changed: set[str] = set()
    saved: set[str] = set()
    max_chars = settings.agent_tool_result_max_chars

    # 第一遍：单条超限 → 原文落盘，正文截断
    for m in tool_msgs:
        text = texts[m.id]
        if MARK in text or len(text) <= max_chars:
            continue
        path = _save(scope, getattr(m, "tool_call_id", m.id), text)
        if path:
            saved.add(m.id)
        note = f"\n{MARK}，原始 {len(text)} 字；完整结果已落盘为 {path or '（落盘失败）'}。如需细节请缩小查询范围重新检索]"
        texts[m.id] = text[:max_chars] + note
        changed.add(m.id)

    # 第二遍：本轮总量仍超预算 → 从最早的工具结果开始压到 1000 字
    total = sum(len(texts.get(m.id, _text(m))) for m in turn)
    budget = settings.agent_max_context_chars
    keep = max(0, settings.agent_tool_result_keep_recent)
    older = tool_msgs[:-keep] if keep else list(tool_msgs)
    for m in older:
        if total <= budget:
            break
        text = texts[m.id]
        if len(text) <= _OLD_LIMIT:
            continue
        if MARK in text and len(text) <= _OLD_LIMIT + 200:
            continue  # 已截断过，标记本身占几十字，避免重复压缩来回抖动
        if m.id not in saved:
            path = _save(scope, getattr(m, "tool_call_id", m.id), text)
            if path:
                saved.add(m.id)
        new = text[:_OLD_LIMIT] + f"\n{MARK}（为控制上下文预算进一步压缩，原始 {len(text)} 字）]"
        total -= len(text) - len(new)
        texts[m.id] = new
        changed.add(m.id)

    updates: list = []
    for m in tool_msgs:
        if m.id in changed:
            updates.extend(_replace(m, texts[m.id]))
    return {"messages": updates} if updates else None
