from app.agent import router, toolkit


def test_tool_route_by_keyword():
    d = router.classify("查一下批号 QS-2024-9527 现在在哪个库区", has_external_tools=True)
    assert d.route == "tool" and d.reason == "keyword_tool"


def test_tool_route_falls_back_when_no_external_tools():
    d = router.classify("查一下批号 QS-2024-9527 在哪个库区", has_external_tools=False)
    assert d.route == "rag" and d.reason == "no_external_tools"


def test_chat_route_greeting():
    assert router.classify("你好", has_external_tools=True).route == "chat"


def test_rag_route_default_and_keyword():
    assert router.classify("冷藏库的温度要求是什么", has_external_tools=True).route == "rag"
    assert router.classify("帮我看看这个问题", has_external_tools=True).route == "rag"


def test_route_suffix_only_for_known_routes():
    assert router.route_suffix("tool")
    assert router.route_suffix(None) == ""


class _FakeTool:
    def __init__(self, name):
        self.name = name


def test_tools_for_route_selects_tool_sets(monkeypatch):
    monkeypatch.setattr(toolkit, "external_tools", lambda: [_FakeTool("mcp_batch")])

    assert [t.name for t in toolkit.tools_for_route(None, "rag")] == [
        "search_knowledge_base", "calculator",
    ]
    assert "mcp_batch" in [t.name for t in toolkit.tools_for_route(None, "tool")]
    assert [t.name for t in toolkit.tools_for_route(None, "chat")] == ["calculator"]
    assert "mcp_batch" in [t.name for t in toolkit.tools_for_route(None, None)]
