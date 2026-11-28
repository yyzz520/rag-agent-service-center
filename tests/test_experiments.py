from app.rag import experiments


def _retrieval_report(metrics, items):
    return {
        "metrics": metrics,
        "items": items,
        "dataset": {"sha256": "same"},
        "prompt_version": {"rewrite": "rewrite.v1"},
    }


def test_compare_reports_paired_delta_and_ci():
    base = _retrieval_report(
        {"recall@1": 0.5, "recall@5": 0.5, "recall@16": 0.5, "mrr": 0.25},
        [
            {"question": "q1", "hit": False, "rank": None},
            {"question": "q2", "hit": True, "rank": 2},
        ],
    )
    var = _retrieval_report(
        {"recall@1": 0.5, "recall@5": 1.0, "recall@16": 1.0, "mrr": 0.75},
        [
            {"question": "q1", "hit": True, "rank": 1},
            {"question": "q2", "hit": True, "rank": 1},
        ],
    )
    out = experiments.compare_reports("rerank", base, var)
    assert out["comparable"] is True
    assert out["comparison"]["recall@5"]["delta"] == 0.5
    assert out["comparison"]["mrr"]["delta"] == 0.5
    assert out["comparison"]["recall@5"]["delta_ci95"]["pairs"] == 2


def test_run_experiment_writes_report(tmp_path, monkeypatch):
    base = _retrieval_report(
        {"recall@1": 0.0, "recall@5": 0.0, "recall@16": 0.0, "mrr": 0.0},
        [{"question": "q1", "hit": False, "rank": None}],
    )
    var = _retrieval_report(
        {"recall@1": 1.0, "recall@5": 1.0, "recall@16": 1.0, "mrr": 1.0},
        [{"question": "q1", "hit": True, "rank": 1}],
    )

    def fake_run(task, note, config):
        return base if "baseline" in note else var

    monkeypatch.setattr(experiments, "_run", fake_run)
    monkeypatch.setattr(experiments, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(experiments, "REPORTS_PATH", tmp_path / "experiments.jsonl")

    report = experiments.run_experiment("rerank", note="test")

    assert report["comparison"]["recall@1"]["delta"] == 1.0
    saved = experiments.read_experiments()[-1]
    assert saved["name"] == "rerank" and saved["note"] == "test"


def test_registry_has_core_experiments():
    assert {"rerank", "refusal_finalize", "plan_schema"} <= set(experiments.EXPERIMENTS)
