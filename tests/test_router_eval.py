import json

from app.rag import router_eval


def test_metrics_accuracy_and_confusion():
    items = [
        {"expected": "rag", "predicted": "rag", "ok": True},
        {"expected": "rag", "predicted": "tool", "ok": False},
        {"expected": "tool", "predicted": "tool", "ok": True},
    ]
    metrics = router_eval._metrics(items)

    assert metrics["accuracy"] == 0.667
    assert metrics["confusion"]["rag"] == {"rag": 1, "tool": 1}
    assert metrics["by_route"]["tool"]["precision"] == 0.5  # 有一题 rag 被误判成 tool
    assert metrics["by_route"]["rag"]["precision"] == 1.0
    assert metrics["by_route"]["rag"]["recall"] == 0.5
    assert "ci95" in metrics


def test_run_router_eval_writes_report(tmp_path, monkeypatch):
    tasks = [
        {"question": "你好", "expect_route": "chat"},
        {"question": "冷藏库的温度要求是什么", "expect_route": "rag"},
        {"question": "查一下批号 QS-2024-9527 在哪个库区", "expect_route": "tool"},
        {"question": "查一下批号 QS-2024-9527 的库存", "expect_route": "rag", "has_external_tools": False},
    ]
    tasks_path = tmp_path / "router_tasks.json"
    tasks_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    reports_path = tmp_path / "router_reports.jsonl"
    monkeypatch.setattr(router_eval, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(router_eval, "TASKS_PATH", tasks_path)
    monkeypatch.setattr(router_eval, "REPORTS_PATH", reports_path)

    report = router_eval.run_router_eval(note="test")

    assert report["metrics"]["accuracy"] == 1.0
    assert report["dataset"]["items"] == 4
    assert report["prompt_version"]["router"] == "route.v1"
    saved = json.loads(reports_path.read_text(encoding="utf-8").splitlines()[-1])
    assert saved["metrics"]["accuracy"] == 1.0
    assert router_eval.read_router_reports()[-1]["note"] == "test"
