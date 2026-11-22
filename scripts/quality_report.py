"""质量报告：从 trace JSONL 聚合各阶段 p50/p95 耗时与 token 消耗。

用法：make quality-report [DAYS=7] 或 python scripts/quality_report.py --days 7 --json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.rag.observability import load_records, summarize  # noqa: E402


def trace_path() -> Path:
    return Path(settings.persist_dir).parent / "trace" / "trace.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=7, help="只统计最近 N 天（默认 7）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    cutoff = time.time() - args.days * 86400
    records = [r for r in load_records(trace_path()) if (r.get("ts") or 0) >= cutoff]
    summary = summarize(records)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"最近 {args.days:g} 天 · trace 记录 {len(records)} 条")
    print("\n阶段耗时（ms）")
    print(f"{'span':32} {'count':>6} {'p50':>8} {'p95':>8} {'max':>8}")
    for name, s in summary["spans"].items():
        print(f"{name:32} {s['count']:>6} {s['p50']:>8} {s['p95']:>8} {s['max']:>8}")
    a = summary["answers"]
    print("\n问答消耗")
    print(f"答案数 {a['count']} · 模型调用 {a['model_calls']} · 工具调用 {a['tool_calls']}")
    print(f"token 总量 {a['tokens_total']} · 每题 p50 {a['tokens_p50']} · p95 {a['tokens_p95']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
