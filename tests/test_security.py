from app.core.security import authenticate, create_access_token, decode_token


def test_default_users_authenticate():
    assert authenticate("alice", "alice123")["role"] == "user"
    assert authenticate("admin", "admin123")["role"] == "admin"
    assert authenticate("alice", "wrong") is None


def test_token_roundtrip():
    user = authenticate("admin", "admin123")
    token = create_access_token(user)
    payload = decode_token(token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"
    assert payload["tenant_id"] == "tenant-admin"
