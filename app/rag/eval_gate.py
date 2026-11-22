"""评测门禁：拿最近一次评测指标和 baseline 阈值比，低于阈值返回非 0。

只读 JSONL 报告，不调模型、不重跑评测；用于提交前/发版前的回归门禁。
"""
import json
from pathlib import Path

DEFAULT_BASELINE = {
    "retrieval": {"recall@1": 0.55, "mrr": 0.70},
    "generation": {"correct_rate": 0.85, "citation_rate": 0.90, "refusal_rate": 0.75},
}

EVAL_DIR = Path("data/eval")
RETRIEVAL_PATH = EVAL_DIR / "reports.jsonl"
GENERATION_PATH = EVAL_DIR / "gen_reports.jsonl"
BASELINE_PATH = EVAL_DIR / "baseline.json"


def read_latest(path: Path) -> dict:
    if not path.exists():
        return {}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def check(
    retrieval_metrics: dict,
    generation_metrics: dict,
    baseline: dict | None = None,
) -> list[dict]:
    """返回失败项列表；每项 {layer, metric, actual, threshold}。空列表 = 通过。"""
    baseline = baseline or DEFAULT_BASELINE
    failures: list[dict] = []
    for layer, metrics in (("retrieval", retrieval_metrics), ("generation", generation_metrics)):
        for metric, threshold in baseline.get(layer, {}).items():
            actual = (metrics or {}).get(metric)
            if actual is None or actual < threshold:
                failures.append({
                    "layer": layer,
                    "metric": metric,
                    "actual": actual,
                    "threshold": threshold,
                })
    return failures


def main(
    retrieval_path: Path | None = None,
    generation_path: Path | None = None,
    baseline_path: Path | None = None,
) -> int:
    retrieval_path = retrieval_path or RETRIEVAL_PATH
    generation_path = generation_path or GENERATION_PATH
    baseline_path = baseline_path or BASELINE_PATH

    baseline = DEFAULT_BASELINE
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    retrieval = read_latest(retrieval_path)
    generation = read_latest(generation_path)
    retrieval_metrics = retrieval.get("metrics") or {}
    generation_metrics = generation.get("metrics") or {}

    failures = check(retrieval_metrics, generation_metrics, baseline)
    print(f"retrieval note={retrieval.get('note')!r} total={retrieval_metrics.get('total')}")
    print(f"generation note={generation.get('note')!r} total={generation_metrics.get('total')}")
    for layer, metrics in (("retrieval", retrieval_metrics), ("generation", generation_metrics)):
        for metric, threshold in baseline.get(layer, {}).items():
            actual = metrics.get(metric)
            ok = actual is not None and actual >= threshold
            print(f"[{'PASS' if ok else 'FAIL'}] {layer}.{metric}: {actual} (threshold {threshold})")

    if failures:
        print(f"eval gate FAILED: {len(failures)} metric(s) below threshold")
        return 1
    print("eval gate PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
