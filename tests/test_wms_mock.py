import json

from mcp_servers import wms_server


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("WMS_MOCK_PATH", str(tmp_path / "wms_mock.json"))


def test_get_batch_storage_has_gmp_fields(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    text = wms_server.get_batch_storage("qs-2024-9527")
    for part in ("冷藏库 B-03", "2~8℃", "已放行", "批记录完整", "无未关闭偏差", "1200"):
        assert part in text


def test_release_batch_persists_and_is_idempotent(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    first = wms_server.release_batch("QS-2024-6001")
    assert "已放行" in first
    assert json.loads((tmp_path / "wms_mock.json").read_text(encoding="utf-8"))[
        "QS-2024-6001"
    ]["qa_status"] == "已放行"
    assert "已放行" in wms_server.get_batch_storage("QS-2024-6001")
    second = wms_server.release_batch("QS-2024-6001")
    assert "未重复执行" in second


def test_release_batch_rejects_open_deviation(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    text = wms_server.release_batch("QS-2024-6100")
    assert "未关闭偏差" in text
    assert "待放行" in wms_server.get_batch_storage("QS-2024-6100")


def test_unknown_batch(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert "未查到" in wms_server.get_batch_storage("NOT-EXIST")
    assert "未查到" in wms_server.release_batch("NOT-EXIST")
