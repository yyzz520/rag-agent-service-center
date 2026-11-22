import json

from app.rag.manifest import dataset_manifest


def test_manifest_records_hash_items_and_bytes(tmp_path):
    path = tmp_path / "golden.json"
    data = [{"question": "q1"}, {"question": "q2"}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    manifest = dataset_manifest(path)

    assert manifest["path"] == str(path)
    assert manifest["items"] == 2
    assert manifest["bytes"] == path.stat().st_size
    assert len(manifest["sha256"]) == 64


def test_manifest_changes_when_dataset_changes(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(json.dumps([{"question": "q1"}]), encoding="utf-8")
    before = dataset_manifest(path)["sha256"]
    path.write_text(json.dumps([{"question": "q2"}]), encoding="utf-8")
    assert dataset_manifest(path)["sha256"] != before


def test_manifest_reports_missing_file(tmp_path):
    manifest = dataset_manifest(tmp_path / "nope.json")
    assert manifest["missing"] is True
    assert "sha256" not in manifest
