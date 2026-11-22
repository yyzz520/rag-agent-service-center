import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.core.security import UserContext
from app.db import Base
from app.models import Doc, KB
from app.services.access import (
    owned_doc,
    visible_doc,
    visible_doc_ids,
    visible_owner_ids,
)

USERS = [
    {"username": "alice", "role": "user", "tenant_id": "t", "org_id": "hq/tech/rag", "level": 2},
    {"username": "bob", "role": "user", "tenant_id": "t", "org_id": "hq/tech/rag", "level": 1},
    {"username": "carol", "role": "user", "tenant_id": "t", "org_id": "hq/tech", "level": 3},
    {"username": "dave", "role": "user", "tenant_id": "t", "org_id": "hq/sales", "level": 1},
    {"username": "admin", "role": "admin", "tenant_id": "t", "level": 99},
]


def _ctx(username: str) -> UserContext:
    u = next(x for x in USERS if x["username"] == username)
    return UserContext(
        user_id=username, username=username, role=u["role"],
        tenant_id="t", org_id=u.get("org_id", ""), level=u.get("level", 1),
    )


@pytest.fixture(autouse=True)
def _users(monkeypatch):
    monkeypatch.setattr(settings, "demo_users_json", json.dumps(USERS))


def test_unconfigured_org_sees_only_self(monkeypatch):
    monkeypatch.setattr(settings, "demo_users_json", "")
    ctx = UserContext(user_id="alice", username="alice", role="user", tenant_id="default")
    assert visible_owner_ids(ctx) == ["alice"]


def test_same_org_rank_gate():
    assert visible_owner_ids(_ctx("carol")) == ["alice", "bob", "carol"]
    assert visible_owner_ids(_ctx("alice")) == ["alice", "bob"]
    assert visible_owner_ids(_ctx("bob")) == ["bob"]  # 看不到职级更高的 alice
    assert visible_owner_ids(_ctx("dave")) == ["dave"]  # 同租户但不同机构


def test_parent_org_sees_subtree_but_not_siblings():
    owners = set(visible_owner_ids(_ctx("carol")))
    assert {"alice", "bob", "carol"} <= owners
    assert "dave" not in owners


def test_admin_is_unrestricted():
    assert visible_owner_ids(_ctx("admin")) is None


def test_tenant_boundary_beats_org_path(monkeypatch):
    users = USERS + [
        {"username": "erin", "role": "user", "tenant_id": "other",
         "org_id": "hq/tech/rag", "level": 1},
    ]
    monkeypatch.setattr(settings, "demo_users_json", json.dumps(users))
    assert "erin" not in visible_owner_ids(_ctx("carol"))


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(KB(id="kb1", owner_id="bob", name="默认知识库"))
    for i, owner in enumerate(["alice", "bob", "carol", "dave"]):
        db.add(Doc(
            id=f"d_{owner}", kb_id="kb1", owner_id=owner,
            file_name=f"{owner}.txt", storage_path=f"/tmp/{owner}.txt", status="ready",
        ))
    db.commit()
    return db


def test_visible_doc_ids_applies_org_and_level():
    db = _db()
    assert set(visible_doc_ids(db, _ctx("alice"))) == {"d_alice", "d_bob"}
    assert set(visible_doc_ids(db, _ctx("carol"))) == {"d_alice", "d_bob", "d_carol"}
    assert set(visible_doc_ids(db, _ctx("bob"))) == {"d_bob"}
    assert set(visible_doc_ids(db, _ctx("dave"))) == {"d_dave"}
    assert len(visible_doc_ids(db, _ctx("admin"))) == 4


def test_view_permission_does_not_grant_edit_permission():
    db = _db()
    alice = _ctx("alice")
    assert visible_doc(db, alice, "d_bob") is not None  # 上级可查看
    assert owned_doc(db, alice.user_id, "d_bob") is None  # 但编辑/删除仍只限本人
