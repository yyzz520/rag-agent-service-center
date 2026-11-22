from app.rag import cache


def test_cache_bypass_context_restores_outer_state():
    assert cache._bypass.get() is False
    with cache.bypass():
        assert cache._bypass.get() is True
    assert cache._bypass.get() is False
