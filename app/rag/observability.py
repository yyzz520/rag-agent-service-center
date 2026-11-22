"""从 trace JSONL 聚合阶段耗时与 token：运维/面试用的质量报告。

/metrics 只有请求级计数；这里按 span 名字聚合 p50/p95，能回答"慢在解析、检索还是生成"，
以及"每个答案平均烧多少 token、调了几次模型/工具"。
"""
import json
from pathlib import Path


def percentile(values: list[float], p: float) -> float:
    """线性插值分位数（与 numpy percentile 默认口径一致）。"""
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    pos = (p / 100) * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 1)


def summarize(records: list[dict]) -> dict:
    spans: dict[str, list[float]] = {}
    answers: list[dict] = []
    for record in records:
        kind = record.get("kind")
        if kind == "span" and record.get("name"):
            spans.setdefault(record["name"], []).append(float(record.get("ms") or 0.0))
        elif kind == "answer":
            answers.append(record.get("usage") or {})

    span_stats = {
        name: {
            "count": len(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "max": round(max(values), 1),
        }
        for name, values in sorted(spans.items())
    }
    tokens = [int(u.get("total_tokens") or 0) for u in answers]
    return {
        "spans": span_stats,
        "answers": {
            "count": len(tokens),
            "tokens_total": sum(tokens),
            "tokens_p50": percentile(tokens, 50),
            "tokens_p95": percentile(tokens, 95),
            "model_calls": sum(int(u.get("model_calls") or 0) for u in answers),
            "tool_calls": sum(int(u.get("tool_calls") or 0) for u in answers),
        },
    }


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
