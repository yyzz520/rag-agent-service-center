from app.config import settings
from app.core import token_budget


def test_budget_disabled(monkeypatch):
    monkeypatch.setattr(settings, "tenant_daily_token_budget", 0)
    token_budget.reset()
    assert token_budget.check("t1") == (True, 0)
    assert token_budget.add("t1", 999999) == 0


def test_budget_in_process_fallback(monkeypatch):
    monkeypatch.setattr(settings, "tenant_daily_token_budget", 100)
    monkeypatch.setattr(settings, "redis_url", "")
    token_budget.reset()

    assert token_budget.check("t1") == (True, 0)
    assert token_budget.add("t1", 60) == 60
    assert token_budget.check("t1") == (True, 60)
    assert token_budget.add("t1", 50) == 110
    allowed, used = token_budget.check("t1")
    assert allowed is False and used == 110
    assert token_budget.check("t2") == (True, 0)  # 按租户隔离
    token_budget.reset()
