from app.config import settings
from app.core import quota


def setup_function():
    quota.reset_quota()


def test_quota_per_tenant_and_limit(monkeypatch):
    monkeypatch.setattr(settings, "daily_request_quota", 2)
    monkeypatch.setattr(settings, "redis_url", "")
    assert quota.check_and_increment("tenant-a") == (True, 1)
    assert quota.check_and_increment("tenant-a") == (True, 2)
    assert quota.check_and_increment("tenant-a") == (False, 3)
    # 另一个租户独立计数
    assert quota.check_and_increment("tenant-b") == (True, 1)


def test_quota_disabled(monkeypatch):
    monkeypatch.setattr(settings, "daily_request_quota", 0)
    assert quota.check_and_increment("tenant-a") == (True, 0)
