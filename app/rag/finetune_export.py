"""把通过评测的生成样本导出成微调 JSONL（instruction/input/output）。

只导出：
- 正样本：答案正确且引用正确；
- 负样本：干净拒答（没带引用）。

这是本项目的数据回流实现：生产上还要人工复核、去重、隐私脱敏、版本化；
不要直接把模型自己的回答无条件回灌，否则会把错误一起放大。
"""
import json
import re
import time
from pathlib import Path

EVAL_DIR = Path("data/eval")
GEN_REPORTS = EVAL_DIR / "gen_reports.jsonl"
AGENT_REPORTS = EVAL_DIR / "agent_reports.jsonl"
OUTPUT = EVAL_DIR / "finetune.jsonl"
AGENT_OUTPUT = EVAL_DIR / "agent_finetune.jsonl"

_CITATION = re.compile(r"\s*\[\d+\]")


def build_samples(items: list[dict]) -> list[dict]:
    samples: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if item.get("judge_error"):
            continue
        question = item.get("question", "").strip()
        if not question:
            continue
        if item.get("category") == "positive":
            if not item.get("correct") or not item.get("citation_correct"):
                continue
            answer = _CITATION.sub("", item.get("answer", "")).strip()
        elif item.get("category") == "negative":
            if item.get("reason") != "refused":
                continue
            answer = "资料中没有相关内容，无法回答。"
        else:
            continue
        if not answer:
            continue
        key = (question, answer)
        if key in seen:
            continue
        seen.add(key)
        samples.append({"instruction": question, "input": "", "output": answer})
    return samples


def build_agent_samples(items: list[dict]) -> list[dict]:
    """只导出成功且路由正确的 Agent 轨迹（messages + tool_calls）。"""
    samples: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not item.get("answer_ok") or not item.get("routed"):
            continue
        trajectory = item.get("trajectory")
        if not trajectory:
            continue
        key = json.dumps(trajectory, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        samples.append({"messages": trajectory})
    return samples


def latest_generation_items() -> list[dict]:
    if not GEN_REPORTS.exists():
        return []
    lines = [line for line in GEN_REPORTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return []
    return json.loads(lines[-1]).get("items", [])


def latest_agent_items() -> list[dict]:
    if not AGENT_REPORTS.exists():
        return []
    lines = [line for line in AGENT_REPORTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return []
    return json.loads(lines[-1]).get("items", [])


def export_latest() -> dict:
    items = latest_generation_items()
    samples = build_samples(items)
    agent_items = latest_agent_items()
    agent_samples = build_agent_samples(agent_items)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    with AGENT_OUTPUT.open("w", encoding="utf-8") as f:
        for sample in agent_samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    return {
        "ts": time.time(),
        "total_items": len(items),
        "samples": len(samples),
        "path": str(OUTPUT),
        "agent_total_items": len(agent_items),
        "agent_samples": len(agent_samples),
        "agent_path": str(AGENT_OUTPUT),
    }
