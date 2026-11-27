from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_demo_page_and_assets_exist():
    html = (STATIC / "demo.html").read_text(encoding="utf-8")
    assert (STATIC / "demo.css").exists()
    js = (STATIC / "demo.js").read_text(encoding="utf-8")
    for marker in ("知识库问答", "智能体与审批", "计划任务"):
        assert marker in html
    assert "/static/demo.js" in html and "/static/demo.css" in html
    for endpoint in ("/rag/chat", "/agent/chat", "/agent/approve", "/agent/plan", "/agent/plan/approve"):
        assert endpoint in js


def test_demo_js_renders_user_content_as_text():
    js = (STATIC / "demo.js").read_text(encoding="utf-8")
    # 模型输出/来源必须用 textContent 渲染，避免 XSS
    assert "textContent" in js
    assert "awaiting_approval" in js
    assert "awaiting_plan_approval" in js
    assert "innerHTML +=" not in js  # 不往 DOM 里拼 HTML 字符串
