from app.agent.execution_ledger import mark_executed, prune, was_executed


def test_mark_is_idempotent(tmp_path):
    path = str(tmp_path / "ledger.db")
    assert mark_executed("s", "c1", "write", '{"x":1}', path=path) is True
    assert was_executed("s", "c1", path=path)
    assert mark_executed("s", "c1", "write", '{"x":1}', path=path) is False


def test_scope_isolates_calls(tmp_path):
    path = str(tmp_path / "ledger.db")
    mark_executed("conv-a", "c1", path=path)
    assert not was_executed("conv-b", "c1", path=path)


def test_prune_removes_old_rows(tmp_path):
    path = str(tmp_path / "ledger.db")
    mark_executed("s", "c1", path=path)
    prune(days=0, path=path)
    assert not was_executed("s", "c1", path=path)
