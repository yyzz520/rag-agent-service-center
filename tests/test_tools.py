from app.agent.tools import calculator


def test_calculator_allows_math():
    assert calculator.invoke("3*(2+5)") == "21"


def test_calculator_blocks_code_injection():
    out = calculator.invoke("__import__('os').system('id')")
    assert "非法字符" in out
