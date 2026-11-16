from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.remote_processing_intake import (
    ProcessingAdmission,
    RemoteProcessingConflict,
)
from solo_leveling.interfaces.http.remote_upload_consents import (
    NOTICE_VERSION,
    RemoteUploadConsent,
    RemoteUploadConsentConflict,
)


INTENT = "00000000-0000-0000-0000-000000000393"
STORAGE = "00000000-0000-0000-0000-000000000394"
AI = "00000000-0000-0000-0000-000000000395"
JOB = "00000000-0000-0000-0000-000000000396"
PAPER = "00000000-0000-0000-0000-000000000397"
VERSION = "00000000-0000-0000-0000-000000000398"
NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


def consent(purpose="storage", *, revoked=False):
    return RemoteUploadConsent(
        STORAGE if purpose == "storage" else AI, purpose, 1, NOTICE_VERSION, NOW,
        NOW if revoked else None,
    )


class Consents:
    def __init__(self):
        self.calls = []
        self.error = None
        self.missing = False

    def list(self, *args):
        self.calls.append(("list", *args))
        if self.error:
            raise self.error
        return None if self.missing else (consent(), consent("ai_provider"))

    def grant(self, *args):
        self.calls.append(("grant", *args))
        if self.error:
            raise self.error
        return None if self.missing else consent(args[2])

    def revoke(self, *args):
        self.calls.append(("revoke", *args))
        if self.error:
            raise self.error
        return None if self.missing else consent(args[2], revoked=True)


class Processing:
    def __init__(self):
        self.calls = []
        self.error = None
        self.missing = False

    def admit(self, *args):
        self.calls.append(("admit", *args))
        if self.error:
            raise self.error
        return None if self.missing else ProcessingAdmission(JOB, PAPER, VERSION, "queued")

    def get(self, *args):
        self.calls.append(("get", *args))
        if self.error:
            raise self.error
        return None if self.missing else ProcessingAdmission(JOB, PAPER, VERSION, "queued")


def app_for(credential="web_session", consents=None, processing=None):
    principal = AuthenticatedPrincipal(
        "owner", credential, "device" if credential == "device" else None)
    consents, processing = consents or Consents(), processing or Processing()
    app = create_app(
        token_verifier=SimpleNamespace(verify=lambda _: principal),
        device_store=SimpleNamespace(),
        device_registry=SimpleNamespace(is_active=lambda *_: True),
        remote_consent_store=consents,
        remote_processing_intake=processing,
    )
    return app, consents, processing


def test_web_can_read_grant_revoke_admit_and_recover_without_input_hash():
    app, consents, processing = app_for()
    auth = {"Authorization": "Bearer test"}
    with TestClient(app) as client:
        status = client.get(f"/v1/remote-uploads/intents/{INTENT}/consents", headers=auth)
        granted = client.post(
            f"/v1/remote-uploads/intents/{INTENT}/consents/storage",
            headers={**auth, "Idempotency-Key": "grant-key"})
        revoked = client.post(
            f"/v1/remote-uploads/intents/{INTENT}/consents/storage/revoke",
            json={"expected_revision": 1}, headers=auth)
        admitted = client.post(
            f"/v1/remote-uploads/intents/{INTENT}/processing",
            json={"storage_consent_id": STORAGE, "ai_consent_id": AI},
            headers={**auth, "Idempotency-Key": "process-key"})
        recovered = client.get(
            f"/v1/remote-uploads/intents/{INTENT}/processing", headers=auth)

    assert [value.status_code for value in (status, granted, revoked, admitted, recovered)] == [200] * 5
    assert status.json()["notice_version"] == NOTICE_VERSION
    assert "완전 삭제에는 최대 30일" in status.json()["storage_notice"]
    assert "OpenAI" in status.json()["ai_provider_notice"]
    assert "input_hash" not in status.text
    assert consents.calls == [
        ("list", "owner", INTENT),
        ("grant", "owner", INTENT, "storage", "grant-key", NOTICE_VERSION),
        ("revoke", "owner", INTENT, "storage", 1),
    ]
    assert processing.calls == [
        ("admit", "owner", INTENT, "process-key", STORAGE, AI, NOTICE_VERSION),
        ("get", "owner", INTENT),
    ]
    for value in (status, granted, revoked, admitted, recovered):
        assert value.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path,method", [
    (f"/v1/remote-uploads/intents/{INTENT}/consents", "get"),
    (f"/v1/remote-uploads/intents/{INTENT}/consents/storage", "post"),
    (f"/v1/remote-uploads/intents/{INTENT}/consents/storage/revoke", "post"),
    (f"/v1/remote-uploads/intents/{INTENT}/processing", "post"),
    (f"/v1/remote-uploads/intents/{INTENT}/processing", "get"),
])
def test_device_credential_is_rejected_before_stores(path, method):
    app, consents, processing = app_for("device")
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "key"}
    options = {"headers": headers}
    if path.endswith("revoke"):
        options["json"] = {"expected_revision": 1}
    elif path.endswith("processing") and method == "post":
        options["json"] = {"storage_consent_id": STORAGE, "ai_consent_id": AI}
    with TestClient(app) as client:
        response = getattr(client, method)(path, **options)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "web_session_required"
    assert not consents.calls and not processing.calls


def test_foreign_and_missing_scope_share_safe_not_found():
    consents = Consents()
    consents.missing = True
    app, _, _ = app_for(consents=consents)
    auth = {"Authorization": "Bearer test"}
    with TestClient(app) as client:
        first = client.get(f"/v1/remote-uploads/intents/{INTENT}/consents", headers=auth)
        second = client.get(
            "/v1/remote-uploads/intents/00000000-0000-0000-0000-000000000999/consents",
            headers=auth)
    assert first.status_code == second.status_code == 404
    assert first.json() == second.json() == {"detail": {"code": "remote_upload_not_found"}}


@pytest.mark.parametrize("error,code", [
    (RemoteUploadConsentConflict("remote_upload_consent_request_conflict"),
     "remote_upload_consent_request_conflict"),
    (RemoteUploadConsentConflict("remote_upload_consent_notice_changed"),
     "remote_upload_consent_notice_changed"),
])
def test_consent_conflicts_are_stable_and_hide_private_details(error, code):
    consents = Consents()
    consents.error = error
    app, _, _ = app_for(consents=consents)
    with TestClient(app) as client:
        response = client.post(
            f"/v1/remote-uploads/intents/{INTENT}/consents/storage",
            headers={"Authorization": "Bearer test", "Idempotency-Key": "key"})
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": code}}


def test_processing_conflict_not_ready_and_recovery_not_found_are_distinct():
    processing = Processing()
    app, _, _ = app_for(processing=processing)
    auth = {"Authorization": "Bearer test", "Idempotency-Key": "key"}
    body = {"storage_consent_id": STORAGE, "ai_consent_id": AI}
    with TestClient(app) as client:
        processing.error = RemoteProcessingConflict("private")
        conflict = client.post(
            f"/v1/remote-uploads/intents/{INTENT}/processing", json=body, headers=auth)
        processing.error = None
        processing.missing = True
        not_ready = client.post(
            f"/v1/remote-uploads/intents/{INTENT}/processing", json=body, headers=auth)
        missing = client.get(
            f"/v1/remote-uploads/intents/{INTENT}/processing", headers=auth)
    assert conflict.json() == {"detail": {"code": "remote_processing_request_conflict"}}
    assert not_ready.json() == {"detail": {"code": "remote_processing_not_ready"}}
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"code": "remote_processing_not_found"}}


def test_old_notice_is_reported_stale_without_mutation():
    consents = Consents()
    old = RemoteUploadConsent(STORAGE, "storage", 1, "old-notice", NOW, None)
    consents.list = lambda *_: (old,)
    app, _, _ = app_for(consents=consents)
    with TestClient(app) as client:
        response = client.get(
            f"/v1/remote-uploads/intents/{INTENT}/consents",
            headers={"Authorization": "Bearer test"})
    assert response.status_code == 200
    assert response.json()["storage"]["state"] == "stale"
