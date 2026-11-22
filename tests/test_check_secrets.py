from scripts.check_secrets import scan_text


def test_scan_text_detects_common_keys():
    assert scan_text("key=sk-" + "a" * 24)
    assert scan_text("token=ghp_" + "b" * 32)
    assert scan_text("-----BEGIN PRIVATE KEY-----")


def test_scan_text_ignores_placeholder():
    assert not scan_text("SILICONFLOW_API_KEY=your-key-here")
