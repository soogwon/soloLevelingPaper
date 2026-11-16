from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.file_uploads import CompletedUpload, PendingUpload, UploadReservation
from solo_leveling.interfaces.http.object_storage import PresignedUpload, StoredObject
from solo_leveling.interfaces.http.remote_uploads import RemoteUploadConflict, RemoteUploadIntent


NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
INTENT_ID = "00000000-0000-0000-0000-000000000393"
PAPER_ID = "00000000-0000-0000-0000-000000000394"
VERSION_ID = "00000000-0000-0000-0000-000000000395"
UPLOAD_ID = "00000000-0000-0000-0000-000000000396"
SHA256 = "ab" * 32


class Store:
    def __init__(self):
        self.calls = []
        self.error = None

    def create(self, *args):
        self.calls.append(("create", *args))
        if self.error:
            raise self.error
        return intent()

    def get(self, *args):
        self.calls.append(("get", *args))
        if self.error:
            raise self.error
        return intent("verified")

    def reserve_upload(self, *args):
        self.calls.append(("reserve", *args))
        if self.error:
            raise self.error
        return UploadReservation(UPLOAD_ID, "private/tmp/safe.pdf", NOW + timedelta(minutes=10)), pending()

    def get_completed(self, *args):
        self.calls.append(("completed", *args))
        if self.error:
            raise self.error
        return completed()


class Storage:
    def __init__(self):
        self.calls = []

    def prepare_upload(self, reservation, **kwargs):
        self.calls.append(("prepare", reservation, kwargs))
        return PresignedUpload(
            "https://objects.example/private-upload", "PUT",
            {"Content-Type": "application/pdf", "Content-Length": "1234"},
            reservation.expires_at)

    def inspect_upload(self, upload):
        self.calls.append(("inspect", upload))
        return StoredObject(upload.object_key, upload.file_size, upload.content_type, upload.sha256_hex)

    def finalize_upload(self, upload):
        self.calls.append(("finalize", upload))
        return StoredObject("private/files/final.pdf", upload.file_size, upload.content_type, upload.sha256_hex)

    def delete_upload(self, key):
        self.calls.append(("delete", key))


def intent(status="created"):
    return RemoteUploadIntent(INTENT_ID, PAPER_ID, VERSION_ID, NOW + timedelta(minutes=10), status)


def pending():
    return PendingUpload(
        UPLOAD_ID, PAPER_ID, VERSION_ID, "paper.pdf", 1234, "application/pdf", SHA256,
        "private/tmp/safe.pdf", NOW + timedelta(minutes=10), "pending")


def completed():
    return CompletedUpload(
        UPLOAD_ID, PAPER_ID, VERSION_ID, "paper.pdf", 1234, "application/pdf", SHA256,
        "private/files/final.pdf", NOW)


def body():
    return {
        "file_name": "paper.pdf", "file_size": 1234,
        "content_type": "application/pdf", "sha256": SHA256,
    }


def app_for(credential="web_session", store=None, storage=None):
    store, storage = store or Store(), storage or Storage()
    principal = AuthenticatedPrincipal(
        "owner-1", credential, "device-1" if credential == "device" else None)
    app = create_app(
        token_verifier=SimpleNamespace(verify=lambda _: principal),
        device_store=SimpleNamespace(),
        device_registry=SimpleNamespace(is_active=lambda *_: True),
        remote_upload_store=store, object_storage=storage,
    )
    return app, store, storage


def test_web_session_can_create_prepare_complete_and_recover_status_without_object_keys():
    app, store, storage = app_for()
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "request-1"}
    with TestClient(app) as client:
        created = client.post("/v1/remote-uploads/intents", json=body(), headers=headers)
        prepared = client.post(
            f"/v1/remote-uploads/intents/{INTENT_ID}/prepare",
            headers={"Authorization": "Bearer test"})
        finished = client.post(
            f"/v1/remote-uploads/intents/{INTENT_ID}/complete",
            headers={"Authorization": "Bearer test"})
        recovered = client.get(
            f"/v1/remote-uploads/intents/{INTENT_ID}",
            headers={"Authorization": "Bearer test"})

    assert [created.status_code, prepared.status_code, finished.status_code, recovered.status_code] == [201, 200, 200, 200]
    assert store.calls[0] == (
        "create", "owner-1", "request-1", "paper.pdf", 1234, "application/pdf", SHA256)
    assert prepared.json()["headers"] == {
        "Content-Type": "application/pdf", "Content-Length": "1234"}
    assert prepared.json()["upload_url"] == "https://objects.example/private-upload"
    assert finished.json()["status"] == "verified"
    assert recovered.json()["status"] == "verified"
    for response in (created, prepared, finished, recovered):
        assert response.headers["cache-control"] == "no-store"
        assert "object_key" not in response.text
    assert not any("owner-1" in str(value) for value in prepared.json().values())


@pytest.mark.parametrize("path,method", [
    ("/v1/remote-uploads/intents", "post"),
    (f"/v1/remote-uploads/intents/{INTENT_ID}", "get"),
    (f"/v1/remote-uploads/intents/{INTENT_ID}/prepare", "post"),
    (f"/v1/remote-uploads/intents/{INTENT_ID}/complete", "post"),
])
def test_remote_upload_routes_require_web_session_before_storage(path, method):
    app, store, storage = app_for("device")
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "request-1"}
    kwargs = {"headers": headers}
    if path.endswith("intents"):
        kwargs["json"] = body()
    with TestClient(app) as client:
        response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "web_session_required"
    assert not store.calls and not storage.calls


def test_missing_authentication_and_idempotency_key_do_not_create_intent():
    app, store, _ = app_for()
    with TestClient(app) as client:
        missing_auth = client.post(
            "/v1/remote-uploads/intents", json=body(), headers={"Idempotency-Key": "request-1"})
        missing_key = client.post(
            "/v1/remote-uploads/intents", json=body(), headers={"Authorization": "Bearer test"})
    assert missing_auth.status_code == 401
    assert missing_key.status_code == 422
    assert not store.calls


def test_request_key_conflict_is_stable_and_does_not_leak_input():
    store = Store()
    store.error = RemoteUploadConflict("private details")
    app, _, _ = app_for(store=store)
    with TestClient(app) as client:
        response = client.post(
            "/v1/remote-uploads/intents", json=body(),
            headers={"Authorization": "Bearer test", "Idempotency-Key": "request-1"})
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "remote_upload_request_conflict"}}
    assert "private details" not in response.text


@pytest.mark.parametrize("status_code,code", [(404, "remote_upload_not_found"), (410, "remote_upload_expired")])
def test_prepare_maps_safe_owner_scoped_errors(status_code, code):
    store = Store()
    store.error = ValueError(code)
    app, _, _ = app_for(store=store)
    with TestClient(app) as client:
        response = client.post(
            f"/v1/remote-uploads/intents/{INTENT_ID}/prepare",
            headers={"Authorization": "Bearer test"})
    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": code}}


def test_foreign_and_missing_intents_have_the_same_external_response():
    store = Store()
    store.get = lambda *_: None
    app, _, _ = app_for(store=store)
    with TestClient(app) as client:
        first = client.get(
            f"/v1/remote-uploads/intents/{INTENT_ID}",
            headers={"Authorization": "Bearer test"})
        second = client.get(
            "/v1/remote-uploads/intents/00000000-0000-0000-0000-000000000999",
            headers={"Authorization": "Bearer test"})
    assert first.status_code == second.status_code == 404
    assert first.json() == second.json() == {"detail": {"code": "remote_upload_not_found"}}


@pytest.mark.parametrize("suffix,method", [("", "post"), (f"/{INTENT_ID}", "get"),
                                          (f"/{INTENT_ID}/prepare", "post"),
                                          (f"/{INTENT_ID}/complete", "post")])
def test_database_connection_failure_returns_safe_unavailable(suffix, method):
    from psycopg import OperationalError

    store = Store()
    store.error = OperationalError("private database connection detail")
    app, _, _ = app_for(store=store)
    options = {"headers": {"Authorization": "Bearer test", "Idempotency-Key": "request-1"}}
    if not suffix:
        options["json"] = body()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = getattr(client, method)("/v1/remote-uploads/intents" + suffix, **options)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "remote_upload_unavailable"}}
    assert "private" not in response.text


@pytest.mark.parametrize("operation", ["prepare", "complete"])
def test_object_storage_failure_returns_safe_unavailable(operation):
    class BrokenStorage(Storage):
        def prepare_upload(self, *args, **kwargs):
            raise OSError("private object storage detail")

        def inspect_upload(self, *args, **kwargs):
            raise OSError("private object storage detail")

    store = Store()
    store.get_completed = lambda *_: None
    app, _, _ = app_for(store=store, storage=BrokenStorage())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/v1/remote-uploads/intents/{INTENT_ID}/{operation}",
            headers={"Authorization": "Bearer test"})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "remote_upload_unavailable"}}
    assert "private" not in response.text
