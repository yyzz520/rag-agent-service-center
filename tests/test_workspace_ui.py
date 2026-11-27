from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_workspace_page_and_assets_exist():
    html = (STATIC / "workspace.html").read_text(encoding="utf-8")
    assert (STATIC / "workspace.css").exists()
    js = (STATIC / "workspace.js").read_text(encoding="utf-8")
    for marker in ("知识库问答", "智能体与审批", "计划任务", "评测数据", "runRoute", "evalCards"):
        assert marker in html
    assert "/static/workspace.js" in html and "/static/workspace.css" in html
    for endpoint in ("/rag/chat", "/agent/chat", "/agent/approve", "/agent/plan", "/agent/plan/approve", "/eval/retrieval", "/eval/router", "/eval/plan"):
        assert endpoint in js


def test_workspace_js_renders_user_content_as_text():
    js = (STATIC / "workspace.js").read_text(encoding="utf-8")
    # 模型输出/来源必须用 textContent 渲染，避免 XSS
    assert "textContent" in js
    assert "awaiting_approval" in js
    assert "awaiting_plan_approval" in js
    assert "innerHTML +=" not in js  # 不往 DOM 里拼 HTML 字符串
