import json

from app.rag import gen_eval, prompts


def test_gen_eval_report_records_prompt_version_and_dataset(monkeypatch, tmp_path):
    golden = tmp_path / "golden_generation.json"
    golden.write_text(
        json.dumps([{"question": "q", "category": "negative"}], ensure_ascii=False),
        encoding="utf-8",
    )
    reports = tmp_path / "gen_reports.jsonl"
    monkeypatch.setattr(gen_eval, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(gen_eval, "GOLDEN_PATH", golden)
    monkeypatch.setattr(gen_eval, "REPORTS_PATH", reports)
    monkeypatch.setattr(gen_eval, "rotate_jsonl_if_needed", lambda path: None)
    monkeypatch.setattr(gen_eval, "_run_item", lambda item: {
        "question": item["question"],
        "category": "negative",
        "answer": "资料中没有相关内容。",
        "sources": 0,
        "cited": False,
        "cited_ids": [],
        "citation_correct": False,
        "correct": True,
        "reason": "refused",
        "judge_error": False,
    })

    report = gen_eval.run_gen_eval(note="meta")

    assert report["prompt_version"] == prompts.versions(("citation", "judge"))
    assert report["dataset"]["items"] == 1
    assert len(report["dataset"]["sha256"]) == 64
    saved = json.loads(reports.read_text(encoding="utf-8").splitlines()[-1])
    assert saved["prompt_version"] == report["prompt_version"]
    assert saved["dataset"]["sha256"] == report["dataset"]["sha256"]
