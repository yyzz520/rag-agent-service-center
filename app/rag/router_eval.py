"""Router 路由评测：固定题集 → 规则分类 → 准确率/混淆矩阵。

只调规则分类，不调模型，零 LLM 花费；改关键词/优先级后重跑即可回归。
题集里可带 `has_external_tools=false`，验证"没有外部工具时 tool 自动退回 rag"。
"""
import json
import time
from pathlib import Path

from app.agent.router import ROUTE_VERSION, classify
from app.config import settings
from app.rag import manifest
from app.rag.stats import bootstrap_ci
from app.rag.trace import rotate_jsonl_if_needed

EVAL_DIR = Path(settings.persist_dir).parent / "eval"
TASKS_PATH = EVAL_DIR / "router_tasks.json"
REPORTS_PATH = EVAL_DIR / "router_reports.jsonl"


def load_tasks() -> list[dict]:
    if not TASKS_PATH.exists():
        return []
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def _metrics(items: list[dict]) -> dict:
    total = len(items)
    correct = sum(1 for i in items if i["predicted"] == i["expected"])
    routes = sorted({i["expected"] for i in items} | {i["predicted"] for i in items})
    by_route = {}
    confusion: dict[str, dict[str, int]] = {}
    for item in items:
        row = confusion.setdefault(item["expected"], {})
        row[item["predicted"]] = row.get(item["predicted"], 0) + 1
    for route in routes:
        tp = sum(1 for i in items if i["expected"] == route and i["predicted"] == route)
        predicted = sum(1 for i in items if i["predicted"] == route)
        support = sum(1 for i in items if i["expected"] == route)
        precision = round(tp / predicted, 3) if predicted else 0.0
        recall = round(tp / support, 3) if support else 0.0
        f1 = round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0
        by_route[route] = {
            "support": support, "precision": precision, "recall": recall, "f1": f1,
        }
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else 0.0,
        "by_route": by_route,
        "confusion": confusion,
        "ci95": {
            "accuracy": bootstrap_ci([
                1.0 if i["predicted"] == i["expected"] else 0.0 for i in items
            ]),
        },
    }


def run_router_eval(note: str | None = None) -> dict:
    tasks = load_tasks()
    if not tasks:
        raise ValueError(f"没有路由评测集：请先建 {TASKS_PATH}")
    items = []
    for task in tasks:
        decision = classify(
            task["question"],
            has_external_tools=task.get("has_external_tools", True),
        )
        items.append({
            "question": task["question"],
            "expected": task["expect_route"],
            "predicted": decision.route,
            "reason": decision.reason,
            "ok": decision.route == task["expect_route"],
        })
    report = {
        "ts": time.time(),
        "note": note,
        "prompt_version": {"router": ROUTE_VERSION},
        "dataset": manifest.dataset_manifest(TASKS_PATH),
        "metrics": _metrics(items),
        "items": items,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    rotate_jsonl_if_needed(REPORTS_PATH)
    return report


def read_router_reports() -> list[dict]:
    if not REPORTS_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in REPORTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
