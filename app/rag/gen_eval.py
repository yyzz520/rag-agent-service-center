"""生成侧评测：负样本拒答率 + 正样本答案正确性/引用率。

与 eval.py 的分工：
- eval.py 只重放检索，不调生成模型，测 recall@k / MRR；
- 这里真跑 RAG 生成链路，测"答得对不对、该拒答时拒没拒、该引用时引没引"。

正样本用 LLM-as-judge 对照参考答案判定；负样本用拒答话术 + 是否带引用做确定性判定。
评测会整体绕过检索缓存，保证打的是真实链路。
"""
import json
import re
import time
from pathlib import Path

from app.ai.providers import primary_llm
from app.config import settings
from app.rag import cache
from app.rag.chain import run_rag
from app.rag.trace import rotate_jsonl_if_needed

EVAL_DIR = Path(settings.persist_dir).parent / "eval"
GOLDEN_PATH = EVAL_DIR / "golden_generation.json"
REPORTS_PATH = EVAL_DIR / "gen_reports.jsonl"

REFUSAL_MARKERS = (
    "资料中没有相关内容",
    "未检索到",
    "没有找到",
    "无法回答",
    "没有相关信息",
    "无法提供",
    "资料中未提及",
)

_JUDGE_PROMPT = """你是严格的 RAG 评测裁判。只判断模型回答是否包含参考答案的关键事实，措辞不同不算错；不要依赖你自己的知识，只对照参考答案。

问题：{question}
参考答案：{expected}
模型回答：{answer}

只输出 JSON，不要 markdown：
{{"correct": true 或 false, "reason": "一句话"}}
"""


def load_golden() -> list[dict]:
    if not GOLDEN_PATH.exists():
        return []
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _text(msg) -> str:
    content = getattr(msg, "content", "")
    return content if isinstance(content, str) else str(content)


def _judge(question: str, expected: str, answer: str) -> dict:
    try:
        msg = primary_llm(0.0).invoke(
            _JUDGE_PROMPT.format(question=question, expected=expected, answer=answer)
        )
        text = _text(msg)
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {"correct": None, "reason": f"judge 未返回 JSON: {text[:120]}", "error": True}
        data = json.loads(match.group(0))
        return {
            "correct": bool(data.get("correct")),
            "reason": str(data.get("reason", ""))[:300],
            "error": False,
        }
    except Exception as exc:
        return {"correct": None, "reason": f"judge 失败: {exc}", "error": True}


def _run_item(item: dict) -> dict:
    question = item["question"]
    category = item.get("category", "positive")
    try:
        # 跳过检索缓存，确保测的是当前检索 + 生成链路
        with cache.bypass():
            out = run_rag(question)
        answer = out.get("answer", "")
        sources = out.get("sources", [])
    except Exception as exc:
        return {
            "question": question,
            "category": category,
            "answer": "",
            "sources": 0,
            "cited": False,
            "correct": False,
            "reason": f"生成失败: {exc}",
            "judge_error": False,
        }

    cited = bool(re.search(r"\[\d+\]", answer)) and bool(sources)
    if category == "negative":
        refused = any(marker in answer for marker in REFUSAL_MARKERS)
        correct = refused and not cited
        reason = "refused" if correct else ("refused_but_cited" if refused else "not_refused")
        return {
            "question": question,
            "category": category,
            "answer": answer[:2000],
            "sources": len(sources),
            "cited": cited,
            "correct": correct,
            "reason": reason,
            "judge_error": False,
        }

    judged = _judge(question, item.get("expected_answer", ""), answer)
    return {
        "question": question,
        "category": category,
        "answer": answer[:2000],
        "sources": len(sources),
        "cited": cited,
        "correct": bool(judged.get("correct")),
        "reason": judged.get("reason", ""),
        "judge_error": bool(judged.get("error")),
    }


def _metrics(results: list[dict]) -> dict:
    positive = [r for r in results if r.get("category") == "positive"]
    negative = [r for r in results if r.get("category") == "negative"]
    clean_refused = sum(1 for r in negative if r.get("reason") == "refused")
    any_refused = sum(1 for r in negative if r.get("reason") in ("refused", "refused_but_cited"))
    return {
        "total": len(results),
        "positive_total": len(positive),
        "positive_correct": sum(1 for r in positive if r["correct"]),
        "correct_rate": round(sum(1 for r in positive if r["correct"]) / len(positive), 3) if positive else 0.0,
        "citation_rate": round(sum(1 for r in positive if r["cited"]) / len(positive), 3) if positive else 0.0,
        "negative_total": len(negative),
        "negative_refused": clean_refused,
        "refusal_rate": round(clean_refused / len(negative), 3) if negative else 0.0,
        "negative_refused_any": any_refused,
        "refusal_any_rate": round(any_refused / len(negative), 3) if negative else 0.0,
        "negative_cited_rate": round(sum(1 for r in negative if r["cited"]) / len(negative), 3) if negative else 0.0,
        "judge_errors": sum(1 for r in positive if r.get("judge_error")),
    }


def run_gen_eval(note: str | None = None) -> dict:
    golden = load_golden()
    if not golden:
        raise ValueError(f"没有生成评测集：请先建 {GOLDEN_PATH}")
    results = [_run_item(item) for item in golden]
    report = {
        "ts": time.time(),
        "note": note,
        "metrics": _metrics(results),
        "items": results,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    rotate_jsonl_if_needed(REPORTS_PATH)
    return report


def read_gen_reports() -> list[dict]:
    if not REPORTS_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in REPORTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
