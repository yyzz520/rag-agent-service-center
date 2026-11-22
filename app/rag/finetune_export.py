"""数据回流：评测通过样本 → SFT JSONL；多轮评测对比 → DPO 偏好对。

只导出：
- 正样本：答案正确且引用正确；
- 负样本：干净拒答（没带引用）。

导出前做最小清洗：去重、长度过滤、手机号/身份证/邮箱/密钥脱敏、引用标记清理。
生产上还差人工复核，且不要无条件灌回模型自己的回答，否则错误会被一起放大。
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
PREFERENCES = EVAL_DIR / "preferences.jsonl"

REFUSAL_ANSWER = "资料中没有相关内容，无法回答。"

_CITATION = re.compile(r"\s*\[\d+\]")
_MIN_LEN = 8
_MAX_LEN = 4000
_PII_PATTERNS = (
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[邮箱]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"), "[密钥]"),
)


def redact_pii(text: str) -> str:
    for pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _clean(text: str) -> str:
    return redact_pii(text.strip())


def _usable(text: str) -> bool:
    return _MIN_LEN <= len(text) <= _MAX_LEN


def build_samples(items: list[dict]) -> list[dict]:
    samples: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if item.get("judge_error"):
            continue
        question = _clean(item.get("question", ""))
        if not question:
            continue
        if item.get("category") == "positive":
            if not item.get("correct") or not item.get("citation_correct"):
                continue
            answer = _clean(_CITATION.sub("", item.get("answer", "")))
        elif item.get("category") == "negative":
            if item.get("reason") != "refused":
                continue
            answer = REFUSAL_ANSWER
        else:
            continue
        if not _usable(answer):
            continue
        key = (question, answer)
        if key in seen:
            continue
        seen.add(key)
        samples.append({"instruction": question, "input": "", "output": answer})
    return samples


def _redact_messages(messages: list[dict]) -> list[dict]:
    """轨迹里消息正文脱敏；工具调用参数保持原样（结构化字段，不塞回 prompt）。"""
    out: list[dict] = []
    for m in messages:
        if isinstance(m, dict) and isinstance(m.get("content"), str):
            m = {**m, "content": redact_pii(m["content"])}
        out.append(m)
    return out


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
        cleaned = _redact_messages(trajectory)
        key = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        samples.append({"messages": cleaned})
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


def all_generation_items() -> list[tuple[float, list[dict]]]:
    """全部生成评测历史，按落盘顺序返回 [(ts, items)]，供跨轮凑偏好对。"""
    if not GEN_REPORTS.exists():
        return []
    runs: list[tuple[float, list[dict]]] = []
    for line in GEN_REPORTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            report = json.loads(line)
        except json.JSONDecodeError:
            continue
        runs.append((report.get("ts", 0.0), report.get("items", [])))
    return runs


def build_preference_pairs(runs: list[tuple[float, list[dict]]]) -> list[dict]:
    """同一题在多轮评测里一正一误，才能凑成一对 DPO 样本；只有一侧的题跳过。

    正样本：答对且引用正确做 chosen，答错做 rejected（保留原文，去掉 [n] 编号）；
    负样本：干净拒答做 chosen，拒答带引用/没拒答做 rejected。
    """
    chosen: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for _ts, items in runs:
        for item in items:
            if item.get("judge_error"):
                continue
            question = (item.get("question") or "").strip()
            answer = (item.get("answer") or "").strip()
            if not question or not answer:
                continue
            category = item.get("category")
            if category == "positive" and item.get("correct") and item.get("citation_correct"):
                chosen.setdefault(question, _clean(_CITATION.sub("", answer)))
            elif category == "positive":
                rejected.setdefault(question, _clean(answer))
            elif category == "negative" and item.get("reason") == "refused":
                chosen.setdefault(question, REFUSAL_ANSWER)
            elif category == "negative":
                rejected.setdefault(question, _clean(answer))
    pairs = []
    for question, good in chosen.items():
        bad = rejected.get(question)
        if not bad or bad == good or not _usable(good) or not _usable(bad):
            continue
        pairs.append({"question": question, "chosen": good, "rejected": bad})
    return sorted(pairs, key=lambda p: p["question"])


def export_latest() -> dict:
    items = latest_generation_items()
    samples = build_samples(items)
    agent_items = latest_agent_items()
    agent_samples = build_agent_samples(agent_items)
    pairs = build_preference_pairs(all_generation_items())
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    with AGENT_OUTPUT.open("w", encoding="utf-8") as f:
        for sample in agent_samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    with PREFERENCES.open("w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    return {
        "ts": time.time(),
        "total_items": len(items),
        "samples": len(samples),
        "path": str(OUTPUT),
        "agent_total_items": len(agent_items),
        "agent_samples": len(agent_samples),
        "agent_path": str(AGENT_OUTPUT),
        "preference_pairs": len(pairs),
        "preference_path": str(PREFERENCES),
    }
