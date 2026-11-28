"""单变量 A/B 实验：同一题集跑 baseline / variant，输出差值与配对 bootstrap 区间。

原则：只改一个变量（rerank、拒答收口、计划 schema），题集/模型/prompt 版本保持一致；
否则分数变化无法归因。实验报告与普通评测报告分开落盘（experiments.jsonl）。
"""
import json
import math
import time
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.rag.eval import run_eval
from app.rag.gen_eval import run_gen_eval
from app.rag.plan_eval import run_plan_eval
from app.rag.stats import bootstrap_ci
from app.rag.trace import rotate_jsonl_if_needed

EVAL_DIR = Path(settings.persist_dir).parent / "eval"
REPORTS_PATH = EVAL_DIR / "experiments.jsonl"

EXPERIMENTS = {
    "rerank": {
        "title": "Rerank 关 vs 开",
        "task": "retrieval",
        "baseline": {"use_rerank": False},
        "variant": {"use_rerank": True},
        "setting": None,
        "cost": "检索重放（不生成答案）",
    },
    "refusal_finalize": {
        "title": "拒答收口 关 vs 开",
        "task": "generation",
        "baseline": {"refusal_finalize_enabled": False},
        "variant": {"refusal_finalize_enabled": True},
        "setting": "refusal_finalize_enabled",
        "cost": "真跑生成（有 token 花费）",
    },
    "plan_schema": {
        "title": "计划提示词 schema 关 vs 开",
        "task": "plan",
        "baseline": {"agent_plan_include_schema": False},
        "variant": {"agent_plan_include_schema": True},
        "setting": "agent_plan_include_schema",
        "cost": "真跑规划+执行（有 token 花费）",
    },
}


@contextmanager
def _override(name: str | None, value):
    if not name or value is None:
        yield
        return
    old = getattr(settings, name)
    setattr(settings, name, value)
    try:
        yield
    finally:
        setattr(settings, name, old)


def _run(task: str, note: str, config: dict) -> dict:
    if task == "retrieval":
        return run_eval(note=note, overrides=config)
    if task == "generation":
        with _override("refusal_finalize_enabled", config.get("refusal_finalize_enabled")):
            return run_gen_eval(note=note)
    if task == "plan":
        with _override("agent_plan_include_schema", config.get("agent_plan_include_schema")):
            return run_plan_eval(note=note)
    raise ValueError(f"未知实验任务：{task}")


def _item_key(task: str, item: dict):
    if task == "generation":
        return (item.get("category"), item.get("question"))
    return item.get("question")


def _value_fns(task: str) -> dict:
    if task == "retrieval":
        return {
            "recall@1": (lambda r: 1.0 if r.get("hit") and (r.get("rank") or 99) <= 1 else 0.0, None),
            "recall@5": (lambda r: 1.0 if r.get("hit") and (r.get("rank") or 99) <= 5 else 0.0, None),
            "recall@16": (lambda r: 1.0 if r.get("hit") and (r.get("rank") or 99) <= 16 else 0.0, None),
            "mrr": (lambda r: 1.0 / r["rank"] if r.get("hit") and r.get("rank") else 0.0, None),
            "ndcg@16": (lambda r: 1.0 / math.log2(r["rank"] + 1) if r.get("hit") and (r.get("rank") or 99) <= 16 else 0.0, None),
        }
    if task == "generation":
        pos = lambda r: r.get("category") == "positive"
        neg = lambda r: r.get("category") == "negative"
        return {
            "correct_rate": (lambda r: 1.0 if r.get("correct") else 0.0, pos),
            "citation_rate": (lambda r: 1.0 if r.get("cited") else 0.0, pos),
            "citation_correct_rate": (lambda r: 1.0 if r.get("citation_correct") else 0.0, pos),
            "refusal_rate": (lambda r: 1.0 if r.get("reason") == "refused" else 0.0, neg),
            "negative_cited_rate": (lambda r: 1.0 if r.get("cited") else 0.0, neg),
        }
    if task == "plan":
        return {
            "success_rate": (lambda r: 1.0 if (r.get("score") or {}).get("ok") else 0.0, None),
            "avg_steps": (lambda r: float(r.get("plan_steps") or 0), None),
            "avg_tokens": (lambda r: float(r.get("tokens") or 0), None),
            "avg_ms": (lambda r: float(r.get("ms") or 0), None),
        }
    return {}


def _paired_ci(task: str, key: str, base_items: list, var_items: list) -> dict | None:
    fn, select = _value_fns(task).get(key, (None, None))
    if fn is None:
        return None
    bmap = {_item_key(task, r): r for r in base_items if not select or select(r)}
    vmap = {_item_key(task, r): r for r in var_items if not select or select(r)}
    keys = sorted(set(bmap) & set(vmap))
    if not keys:
        return None
    diffs = [fn(vmap[k]) - fn(bmap[k]) for k in keys]
    return bootstrap_ci(diffs) | {"pairs": len(diffs)}


def compare_reports(name: str, base: dict, var: dict) -> dict:
    task = EXPERIMENTS[name]["task"]
    comp = {}
    for key in _value_fns(task):
        b = (base.get("metrics") or {}).get(key)
        v = (var.get("metrics") or {}).get(key)
        if b is None and v is None:
            continue
        row = {"baseline": b, "variant": v}
        if isinstance(b, (int, float)) and isinstance(v, (int, float)):
            row["delta"] = round(v - b, 3)
        ci = _paired_ci(task, key, base.get("items") or [], var.get("items") or [])
        if ci:
            row["delta_ci95"] = ci
        comp[key] = row
    base_hash = (base.get("dataset") or {}).get("sha256")
    var_hash = (var.get("dataset") or {}).get("sha256")
    return {
        "comparison": comp,
        "comparable": base_hash == var_hash and base_hash is not None,
        "dataset": base.get("dataset"),
        "prompt_version": base.get("prompt_version"),
    }


def run_experiment(name: str, note: str | None = None) -> dict:
    spec = EXPERIMENTS.get(name)
    if spec is None:
        raise ValueError(f"未知实验：{name}（可选：{', '.join(EXPERIMENTS)}）")
    base_cfg = spec["baseline"]
    var_cfg = spec["variant"]
    base = _run(spec["task"], f"exp:{name}:baseline", base_cfg)
    var = _run(spec["task"], f"exp:{name}:variant", var_cfg)
    compared = compare_reports(name, base, var)
    report = {
        "ts": time.time(),
        "note": note,
        "name": name,
        "title": spec["title"],
        "task": spec["task"],
        "cost": spec["cost"],
        "baseline": {"config": base_cfg, "metrics": base.get("metrics")},
        "variant": {"config": var_cfg, "metrics": var.get("metrics")},
        **compared,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    rotate_jsonl_if_needed(REPORTS_PATH)
    return report


def read_experiments() -> list[dict]:
    if not REPORTS_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in REPORTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
