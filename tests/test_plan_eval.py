import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.rag import plan_eval


def test_score_success_and_failures():
    task = {
        "expect_tools": ["tool_a"], "expect_answer_contains": ["结论"], "max_steps": 2,
    }
    ok = plan_eval._score(task, [{"tool": "tool_a"}], [{"tool": "tool_a"}], "结论：合规")
    assert ok["ok"] is True and ok["reasons"] == []

    bad = plan_eval._score(
        task, [{"tool": "tool_b"}, {"tool": "tool_b"}, {"tool": "tool_b"}],
        [{"tool": "tool_b"}], "不知道",
    )
    assert bad["ok"] is False
    assert set(bad["reasons"]) == {"missing_tools", "missing_answer_phrases", "too_many_steps"}


def test_metrics_aggregates_success_and_cost():
    items = [
        {
            "plan_steps": 2, "tokens": 100, "ms": 10.0,
            "score": {"ok": True, "reasons": []},
        },
        {
            "plan_steps": 1, "tokens": 50, "ms": 20.0,
            "score": {"ok": False, "reasons": ["missing_tools"]},
        },
    ]
    metrics = plan_eval._metrics(items)

    assert metrics["success_rate"] == 0.5
    assert metrics["avg_steps"] == 1.5
    assert metrics["avg_tokens"] == 75.0
    assert metrics["failures"] == {"missing_tools": 1}
    assert "ci95" in metrics


class _FakeAgent:
    def invoke(self, payload, config):
        if isinstance(payload, dict):
            return {
                "__interrupt__": [SimpleNamespace(value={
                    "steps": [{"goal": "查", "tool": "fake_tool", "args": {}}],
                })],
                "messages": [HumanMessage(content="q")],
            }
        return {
            "messages": [HumanMessage(content="q"), AIMessage(content="冷藏库 2~8，已放行")],
            "results": [{"step": 1, "tool": "fake_tool", "result": "ok"}],
        }


def test_run_plan_eval_with_fake_agent(tmp_path, monkeypatch):
    tasks = [{
        "question": "查批号和温控",
        "expect_tools": ["fake_tool"],
        "expect_answer_contains": ["冷藏库", "2~8"],
        "max_steps": 2,
    }]
    tasks_path = tmp_path / "plan_tasks.json"
    tasks_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    reports_path = tmp_path / "plan_reports.jsonl"
    monkeypatch.setattr(plan_eval, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(plan_eval, "TASKS_PATH", tasks_path)
    monkeypatch.setattr(plan_eval, "REPORTS_PATH", reports_path)

    report = plan_eval.run_plan_eval(
        note="test", agent_factory=lambda *a, **k: _FakeAgent()
    )

    assert report["metrics"]["success_rate"] == 1.0
    assert report["metrics"]["avg_steps"] == 1.0
    assert report["dataset"]["items"] == 1
    saved = json.loads(reports_path.read_text(encoding="utf-8").splitlines()[-1])
    assert saved["metrics"]["success_rate"] == 1.0
    assert plan_eval.read_plan_reports()[-1]["note"] == "test"
