"""삭제 API 인증·확인·조회 무변경 계약."""

from types import SimpleNamespace
from uuid import uuid4
from fastapi.testclient import TestClient
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal, AccessTokenRejected
from solo_leveling.interfaces.http.remote_deletions import DeletionRequest

PAPER = str(uuid4())
KEY = "test-request-key-1"


class Store:
    def __init__(self):
        self.rows = {}
        self.calls = 0

    def get_existing(self, owner, paper, key):
        return self.rows.get((owner, paper, key))

    def request(self, owner, paper, key):
        self.calls += 1
        if owner != "owner" or paper != PAPER:
            return None
        for (saved_owner, saved_paper, saved_key), row in self.rows.items():
            if saved_owner == owner and (saved_paper == paper or saved_key == key):
                return row
        row = DeletionRequest(str(uuid4()), paper, "blocking")
        self.rows[owner, paper, key] = row
        return row


class Verifier:
    def verify(self, token):
        if token == "invalid":
            raise AccessTokenRejected()
        return AuthenticatedPrincipal(token, "device", "device")


def test_authenticated_confirmation_replay_and_read_only_lookup():
    store = Store()
    app = create_app(
        token_verifier=Verifier(), device_registry=SimpleNamespace(is_active=lambda *_: True), deletion_store=store
    )
    path = f"/v1/papers/{PAPER}/deletion-requests/{KEY}"
    headers = {"Authorization": "Bearer owner"}
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).status_code == 404
        assert store.calls == 0
        for body in (
            {},
            {"confirmed": False, "notice_version": "server-paper-deletion-v1"},
            {"confirmed": 1, "notice_version": "server-paper-deletion-v1"},
            {"confirmed": "true", "notice_version": "server-paper-deletion-v1"},
            {"confirmed": True, "notice_version": "old"},
            {"confirmed": True, "notice_version": "server-paper-deletion-v1", "owner": "other"},
        ):
            assert client.put(path, headers=headers, json=body).status_code == 422
        assert store.calls == 0
        body = {"confirmed": True, "notice_version": "server-paper-deletion-v1"}
        first = client.put(path, headers=headers, json=body)
        assert first.status_code == 200
        assert first.json()["status"] == "blocking"
        assert first.json()["backup_erasure_confirmed"] is False
        assert client.put(path, headers=headers, json=body).json() == first.json()
        calls = store.calls
        assert client.get(path, headers=headers).json() == first.json()
        assert store.calls == calls
        assert client.get(path, headers={"Authorization": "Bearer other"}).status_code == 404
        assert client.put(path, headers={"Authorization": "Bearer other"}, json=body).status_code == 404
        assert client.put(path + "-new", headers=headers, json=body).status_code == 409
        assert client.get(path + "-new", headers=headers).status_code == 404
        assert len(store.rows) == 1


def test_storage_errors_are_not_exposed():
    class Broken(Store):
        def get_existing(self, *_):
            raise RuntimeError("secret database detail")

    app = create_app(
        token_verifier=Verifier(), device_registry=SimpleNamespace(is_active=lambda *_: True), deletion_store=Broken()
    )
    with TestClient(app) as client:
        result = client.get(f"/v1/papers/{PAPER}/deletion-requests/{KEY}", headers={"Authorization": "Bearer owner"})
        assert result.status_code == 503
        assert "secret" not in result.text


def test_revoked_device_and_missing_web_csrf_cannot_submit():
    store = Store()
    app = create_app(
        token_verifier=Verifier(),
        device_registry=SimpleNamespace(is_active=lambda *_: False),
        web_session_store=SimpleNamespace(authenticate=lambda *_: SimpleNamespace(user_id="owner")),
        deletion_store=store,
    )
    path = f"/v1/papers/{PAPER}/deletion-requests/{KEY}"
    body = {"confirmed": True, "notice_version": "server-paper-deletion-v1"}
    with TestClient(app) as client:
        assert client.put(path, headers={"Authorization": "Bearer owner"}, json=body).status_code == 401
        client.cookies.set("slp_session", "synthetic-session")
        client.cookies.set("csrf_token", "synthetic-csrf")
        assert client.put(path, json=body).status_code == 403
        assert store.calls == 0
        assert client.put(path, headers={"X-CSRF-Token": "synthetic-csrf"}, json=body).status_code == 200
        assert store.calls == 1
