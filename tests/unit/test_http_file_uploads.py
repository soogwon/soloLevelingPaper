from datetime import datetime, timedelta, timezone
import inspect
import io
from hashlib import sha256
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.file_uploads import (
    MAX_FILE_BYTES, CompletedUpload, FileAccessRecord, PendingUpload, PostgresFileUploadStore,
    UploadAlreadyReady, UploadExpired, UploadNotFound, UploadQuotaExceeded,
    UploadReservation, UploadReservationConflict, UploadTargetNotFound,
)
from solo_leveling.interfaces.http.object_storage import (
    PresignedDownload, PresignedUpload, S3ObjectStorage, StoredObject,
)
from solo_leveling.interfaces.http.settings import load_object_storage_settings


PAPER_ID = "00000000-0000-0000-0000-000000000201"
UPLOAD_ID = "00000000-0000-0000-0000-000000000601"
NOW = datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc)
SHA256 = "ab" * 32


@pytest.mark.parametrize("credential", ["device", "web_session", None])
def test_http_pdf_routes_enforce_credentials_before_storage(credential):
    """직접 함수 호출과 달리 실제 HTTP 인증 의존성과 응답 모델까지 확인한다."""
    store, storage = UploadStore(), Storage()
    principal = AuthenticatedPrincipal(
        "user-1", credential or "device", "device-1" if credential != "web_session" else None)
    app = create_app(
        token_verifier=SimpleNamespace(verify=lambda _: principal),
        device_store=SimpleNamespace(),
        device_registry=SimpleNamespace(is_active=lambda *_: True),
        file_upload_store=store, object_storage=storage,
    )
    headers = {"Authorization": "Bearer synthetic-test-token"} if credential else {}
    with TestClient(app) as client:
        prepared = client.post(f"/v1/papers/{PAPER_ID}/uploads", json=body(), headers=headers)
        completed = client.post(f"/v1/uploads/{UPLOAD_ID}/complete", headers=headers)
        access = client.get(
            f"/v1/papers/{PAPER_ID}/versions/version-1/file-access", headers=headers)
        status = client.get(
            f"/v1/papers/{PAPER_ID}/versions/version-1/upload-status", headers=headers)
    if credential is None:
        assert [r.status_code for r in (prepared, completed, access, status)] == [401] * 4
        assert not store.calls and not storage.calls
    elif credential == "device":
        assert [r.status_code for r in (prepared, completed, access, status)] == [201, 200, 403, 200]
        assert completed.json()["storage_status"] == "ready"
        assert status.json()["status"] == "ready"
        assert "url" not in status.json()
        assert status.headers["cache-control"] == "no-store"
        assert not any(call[0] == "download" for call in storage.calls)
    else:
        assert [r.status_code for r in (prepared, completed, access, status)] == [403, 403, 200, 403]
        assert access.json()["url"] == "https://objects.example/read"
        assert "object_key" not in access.json()
        assert access.headers["cache-control"] == "no-store"
        assert store.calls == [("access", "user-1", PAPER_ID, "version-1")]


@pytest.mark.parametrize("state", ["not_uploaded", "pending", "failed"])
def test_http_non_ready_pdf_never_issues_download_url(state):
    """미완료 파일 상태는 HTTP 직렬화 후에도 열람 주소 없이 반환한다."""
    store, storage = UploadStore(), Storage()
    record = None if state == "not_uploaded" else FileAccessRecord(
        state, "paper.pdf", 1234, "application/pdf", None,
        "upload_checksum_mismatch" if state == "failed" else None)
    store.get_file_access = lambda *_: record
    app = create_app(
        token_verifier=SimpleNamespace(
            verify=lambda _: AuthenticatedPrincipal("user-1", "web_session")),
        file_upload_store=store, object_storage=storage,
    )
    with TestClient(app) as client:
        response = client.get(
            f"/v1/papers/{PAPER_ID}/versions/version-1/file-access",
            headers={"Authorization": "Bearer synthetic-test-token"})
    assert response.status_code == 200
    assert response.json()["status"] == state
    assert response.json()["url"] is None
    assert response.json()["expires_at"] is None
    assert not storage.calls


class UploadStore:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def reserve(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return UploadReservation(UPLOAD_ID, "private/key.pdf", NOW)

    def get_for_completion(self, *args):
        self.calls.append(("get", *args))
        if self.error:
            raise self.error
        return pending_upload()

    def mark_failed(self, *args):
        self.calls.append(("failed", *args))

    def mark_ready(self, *args):
        self.calls.append(("ready", *args))
        return completed_upload(object_key=args[-1])

    def get_file_access(self, *args):
        self.calls.append(("access", *args))
        if self.error:
            raise self.error
        return FileAccessRecord(
            "ready", "paper.pdf", 1234, "application/pdf", "private/final.pdf")


class Storage:
    def __init__(self):
        self.calls = []

    def prepare_upload(self, reservation, **kwargs):
        self.calls.append((reservation, kwargs))
        return PresignedUpload("https://objects.example/upload", "PUT", {
            "Content-Type": "application/pdf",
        }, NOW)

    def inspect_upload(self, upload):
        self.calls.append(("inspect", upload))
        return stored_object(upload.object_key)

    def finalize_upload(self, upload):
        self.calls.append(("finalize", upload))
        return stored_object("private/final.pdf")

    def delete_upload(self, object_key):
        self.calls.append(("delete", object_key))

    def prepare_download(self, file):
        self.calls.append(("download", file))
        return PresignedDownload("https://objects.example/read", NOW)


def route(store=None, storage=None):
    store, storage = store or UploadStore(), storage or Storage()
    app = create_app(
        token_verifier=SimpleNamespace(verify=lambda _: AuthenticatedPrincipal("user-1", "device", "device-1")),
        device_store=SimpleNamespace(),
        device_registry=SimpleNamespace(is_active=lambda *_: True),
        file_upload_store=store, object_storage=storage,
    )
    endpoint = next(item.endpoint for item in app.routes
                    if item.path == "/v1/papers/{paper_id}/uploads")
    payload_type = inspect.signature(endpoint).parameters["payload"].annotation
    return endpoint, payload_type


def complete_route(store=None, storage=None):
    store, storage = store or UploadStore(), storage or Storage()
    app = create_app(
        token_verifier=SimpleNamespace(verify=lambda _: AuthenticatedPrincipal("user-1", "device", "device-1")),
        device_store=SimpleNamespace(), device_registry=SimpleNamespace(is_active=lambda *_: True),
        file_upload_store=store, object_storage=storage,
    )
    endpoint = next(item.endpoint for item in app.routes
                    if item.path == "/v1/uploads/{upload_id}/complete")
    return endpoint


def file_access_route(store=None, storage=None):
    store, storage = store or UploadStore(), storage or Storage()
    app = create_app(file_upload_store=store, object_storage=storage)
    endpoint = next(item.endpoint for item in app.routes
                    if item.path == "/v1/papers/{paper_id}/versions/{version_id}/file-access")
    return endpoint


def upload_status_route(store=None):
    store = store or UploadStore()
    app = create_app(
        token_verifier=SimpleNamespace(verify=lambda _: AuthenticatedPrincipal("user-1", "device", "device-1")),
        device_store=SimpleNamespace(), device_registry=SimpleNamespace(is_active=lambda *_: True),
        file_upload_store=store, object_storage=Storage(),
    )
    return next(item.endpoint for item in app.routes
                if item.path == "/v1/papers/{paper_id}/versions/{version_id}/upload-status")


def pending_upload(**changes):
    values = dict(
        upload_id=UPLOAD_ID, paper_id=PAPER_ID, server_version_id="version-1",
        file_name="paper.pdf", file_size=1234, content_type="application/pdf",
        sha256_hex=SHA256, object_key="private/key.pdf",
        expires_at=NOW + timedelta(minutes=10), status="pending",
    )
    return PendingUpload(**(values | changes))


def completed_upload(**changes):
    values = dict(
        upload_id=UPLOAD_ID, paper_id=PAPER_ID, server_version_id="version-1",
        file_name="paper.pdf", file_size=1234, content_type="application/pdf",
        sha256_hex=SHA256, object_key="private/final.pdf", completed_at=NOW,
    )
    return CompletedUpload(**(values | changes))


def stored_object(key="private/key.pdf", **changes):
    values = dict(
        object_key=key, file_size=1234, content_type="application/pdf",
        sha256_hex=SHA256,
    )
    return StoredObject(**(values | changes))


def body(**changes):
    return {
        "server_version_id": "version-1", "local_version_id": "version-1",
        "file_name": "paper.pdf", "file_size": 1234,
        "content_type": "application/pdf", "sha256": SHA256, "consent": True,
    } | changes


def test_device_prepares_private_pdf_upload_with_explicit_integrity_headers():
    store, storage = UploadStore(), Storage()
    endpoint, payload_type = route(store, storage)
    response = endpoint(UUID(PAPER_ID), payload_type(**body()),
                        AuthenticatedPrincipal("user-1", "device", "device-1"))

    assert response.model_dump(mode="json") == {
        "upload_id": UPLOAD_ID, "upload_url": "https://objects.example/upload", "method": "PUT",
        "headers": {
            "Content-Type": "application/pdf",
        }, "expires_at": NOW.isoformat(), "max_file_size": MAX_FILE_BYTES,
    }
    assert store.calls == [("user-1", PAPER_ID, "version-1", "version-1", "paper.pdf",
                            1234, "application/pdf", SHA256)]
    assert storage.calls[0][1] == {"file_size": 1234, "content_type": "application/pdf", "sha256_hex": SHA256}


def test_upload_requires_separate_consent_before_reserving_storage():
    store = UploadStore()
    endpoint, payload_type = route(store)
    with pytest.raises(HTTPException) as failure:
        endpoint(UUID(PAPER_ID), payload_type(**body(consent=False)),
                 AuthenticatedPrincipal("user-1", "device", "device-1"))
    assert failure.value.status_code == 422
    assert failure.value.detail["code"] == "pdf_upload_consent_required"
    assert not store.calls


@pytest.mark.parametrize("change", [
    {"consent": 1}, {"consent": "true"}, {"file_size": MAX_FILE_BYTES + 1}, {"content_type": "text/plain"},
    {"file_name": "../paper.pdf"}, {"sha256": "nope"},
])
def test_invalid_pdf_metadata_never_reserves_storage(change):
    store = UploadStore()
    _, payload_type = route(store)
    with pytest.raises(ValidationError):
        payload_type(**body(**change))
    assert not store.calls


@pytest.mark.parametrize(("error", "status", "code"), [
    (UploadTargetNotFound(), 404, "paper_version_not_found"),
    (UploadQuotaExceeded(), 413, "pdf_storage_quota_exceeded"),
    (UploadAlreadyReady(), 409, "pdf_already_uploaded"),
])
def test_prepare_reports_target_and_quota_failures(error, status, code):
    endpoint, payload_type = route(UploadStore(error))
    with pytest.raises(HTTPException) as failure:
        endpoint(UUID(PAPER_ID), payload_type(**body()),
                 AuthenticatedPrincipal("user-1", "device", "device-1"))
    assert failure.value.status_code == status
    assert failure.value.detail["code"] == code


def test_web_session_cannot_prepare_device_pdf_upload():
    store = UploadStore()
    endpoint, payload_type = route(store)
    with pytest.raises(HTTPException) as failure:
        endpoint(UUID(PAPER_ID), payload_type(**body()),
                 AuthenticatedPrincipal("user-1", "web_session"))
    assert failure.value.status_code == 403
    assert not store.calls


def test_openapi_exposes_protected_upload_preparation_contract():
    operation = create_app(file_upload_store=UploadStore(), object_storage=Storage()).openapi()[
        "paths"]["/v1/papers/{paper_id}/uploads"]["post"]
    assert operation["security"] == [{"ServerAccessToken": []}]
    assert operation["responses"]["201"]["content"]["application/json"]["schema"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("/UploadPrepare")


def test_complete_verifies_and_promotes_object_before_marking_ready():
    store, storage = UploadStore(), Storage()
    response = complete_route(store, storage)(
        UUID(UPLOAD_ID), AuthenticatedPrincipal("user-1", "device", "device-1"))

    assert response.model_dump(mode="json") == {
        "upload_id": UPLOAD_ID, "paper_id": PAPER_ID, "version_id": "version-1",
        "file_name": "paper.pdf", "file_size": 1234, "content_type": "application/pdf",
        "storage_status": "ready", "failure_reason": None,
    }
    assert [call[0] for call in storage.calls] == ["inspect", "finalize", "delete"]
    assert store.calls[-1] == ("ready", "user-1", UPLOAD_ID, "private/final.pdf")


@pytest.mark.parametrize(("actual", "code"), [
    (stored_object(file_size=1235), "upload_size_mismatch"),
    (stored_object(pdf_header_valid=False), "upload_content_type_mismatch"),
    (stored_object(content_type="text/plain"), "upload_content_type_mismatch"),
    (stored_object(sha256_hex="wrong"), "upload_checksum_mismatch"),
])
def test_complete_rejects_mismatched_object_and_records_failure(actual, code):
    class MismatchStorage(Storage):
        def inspect_upload(self, upload):
            self.calls.append(("inspect", upload))
            return actual

    store, storage = UploadStore(), MismatchStorage()
    with pytest.raises(HTTPException) as failure:
        complete_route(store, storage)(
            UUID(UPLOAD_ID), AuthenticatedPrincipal("user-1", "device", "device-1"))
    assert failure.value.status_code == 422
    assert failure.value.detail["code"] == code
    assert store.calls[-1] == ("failed", "user-1", UPLOAD_ID, code)
    assert not any(call[0] == "finalize" for call in storage.calls)


def test_complete_missing_object_is_retryable_until_reservation_expires():
    class MissingStorage(Storage):
        def inspect_upload(self, upload):
            return None

    store = UploadStore()
    with pytest.raises(HTTPException) as failure:
        complete_route(store, MissingStorage())(
            UUID(UPLOAD_ID), AuthenticatedPrincipal("user-1", "device", "device-1"))
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == "upload_object_missing"
    assert not any(call[0] == "failed" for call in store.calls)


def test_complete_is_idempotent_after_ready_without_touching_storage():
    store, storage = UploadStore(), Storage()
    store.get_for_completion = lambda *_: completed_upload()
    response = complete_route(store, storage)(
        UUID(UPLOAD_ID), AuthenticatedPrincipal("user-1", "device", "device-1"))
    assert response.storage_status == "ready"
    assert not storage.calls


@pytest.mark.parametrize(("error", "status", "code"), [
    (UploadNotFound(), 404, "upload_not_found"),
    (UploadExpired(), 410, "upload_expired"),
])
def test_complete_hides_foreign_upload_and_reports_expiration(error, status, code):
    store = UploadStore(error)
    with pytest.raises(HTTPException) as failure:
        complete_route(store)(
            UUID(UPLOAD_ID), AuthenticatedPrincipal("user-1", "device", "device-1"))
    assert failure.value.status_code == status
    assert failure.value.detail["code"] == code


def test_web_session_cannot_complete_device_upload():
    store = UploadStore()
    with pytest.raises(HTTPException) as failure:
        complete_route(store)(UUID(UPLOAD_ID), AuthenticatedPrincipal("user-1", "web_session"))
    assert failure.value.status_code == 403
    assert not store.calls


def test_web_session_gets_short_lived_pdf_url_without_object_key():
    store, storage = UploadStore(), Storage()
    headers = Response()
    response = file_access_route(store, storage)(
        UUID(PAPER_ID), "version-1", AuthenticatedPrincipal("user-1", "web_session"), headers)

    assert headers.headers["Cache-Control"] == "no-store"
    assert response.model_dump(mode="json") == {
        "status": "ready", "url": "https://objects.example/read",
        "expires_at": NOW.isoformat(), "file_name": "paper.pdf", "file_size": 1234,
        "content_type": "application/pdf", "reason": None,
    }
    assert "private/final.pdf" not in response.model_dump_json()
    assert store.calls == [("access", "user-1", PAPER_ID, "version-1")]


@pytest.mark.parametrize(("record", "status", "reason"), [
    (None, "not_uploaded", "not_uploaded"),
    (FileAccessRecord("pending", "paper.pdf", 1234, "application/pdf", None),
     "pending", "upload_pending"),
    (FileAccessRecord("failed", "paper.pdf", 1234, "application/pdf", None,
                      "upload_checksum_mismatch"),
     "failed", "upload_checksum_mismatch"),
])
def test_file_access_distinguishes_non_ready_states_without_url(record, status, reason):
    store = UploadStore()
    store.get_file_access = lambda *_: record
    response = file_access_route(store)(
        UUID(PAPER_ID), "version-1", AuthenticatedPrincipal("user-1", "web_session"), Response())
    assert response.status == status
    assert response.reason == reason
    assert response.url is None and response.expires_at is None


def test_file_access_hides_foreign_or_unknown_paper_version():
    with pytest.raises(HTTPException) as failure:
        file_access_route(UploadStore(UploadTargetNotFound()))(
            UUID(PAPER_ID), "version-1", AuthenticatedPrincipal("user-1", "web_session"), Response())
    assert failure.value.status_code == 404
    assert failure.value.detail["code"] == "paper_version_not_found"


def test_device_token_cannot_use_external_site_file_access():
    store = UploadStore()
    with pytest.raises(HTTPException) as failure:
        file_access_route(store)(
            UUID(PAPER_ID), "version-1", AuthenticatedPrincipal("user-1", "device", "device-1"), Response())
    assert failure.value.status_code == 403
    assert not store.calls


def test_device_can_check_ready_upload_without_download_url():
    store = UploadStore()
    headers = Response()
    response = upload_status_route(store)(
        UUID(PAPER_ID), "version-1",
        AuthenticatedPrincipal("user-1", "device", "device-1"), headers)

    assert headers.headers["Cache-Control"] == "no-store"
    assert response.model_dump(mode="json") == {
        "status": "ready", "file_name": "paper.pdf", "file_size": 1234,
        "content_type": "application/pdf", "reason": None,
    }
    assert store.calls == [("access", "user-1", PAPER_ID, "version-1")]


def test_web_session_cannot_use_device_upload_status():
    store = UploadStore()
    with pytest.raises(HTTPException) as failure:
        upload_status_route(store)(
            UUID(PAPER_ID), "version-1", AuthenticatedPrincipal("user-1", "web_session"),
            Response())
    assert failure.value.status_code == 403
    assert not store.calls


def test_object_storage_settings_are_all_or_nothing_and_https_only():
    assert load_object_storage_settings({}) is None
    with pytest.raises(ValueError):
        load_object_storage_settings({"OBJECT_STORAGE_BUCKET": "bucket"})
    with pytest.raises(ValueError):
        load_object_storage_settings({
            "OBJECT_STORAGE_ENDPOINT": "http://objects.example",
            "OBJECT_STORAGE_BUCKET": "bucket", "OBJECT_STORAGE_ACCESS_KEY_ID": "key",
            "OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
        })


def test_s3_presign_binds_content_type_and_checksum():
    class Client:
        def generate_presigned_url(self, operation, **kwargs):
            self.call = (operation, kwargs)
            return "https://objects.example/upload"

    raw = Client()
    upload = S3ObjectStorage(raw, "private-bucket").prepare_upload(
        UploadReservation(UPLOAD_ID, "private/key.pdf", datetime.now(timezone.utc) + timedelta(minutes=10)),
        file_size=1234, content_type="application/pdf", sha256_hex=SHA256,
    )
    with pytest.raises(RuntimeError, match="expired"):
        S3ObjectStorage(raw, "private-bucket").prepare_upload(
            UploadReservation(UPLOAD_ID, "private/key.pdf", datetime.now(timezone.utc)),
            file_size=1234, content_type="application/pdf", sha256_hex=SHA256)
    operation, sent = raw.call
    assert int(upload.headers["Content-Length"]) == sent["Params"]["ContentLength"] == 1234
    assert operation == "put_object" and sent["HttpMethod"] == "PUT"
    assert sent["Params"]["Bucket"] == "private-bucket"
    assert sent["Params"]["Key"] == "private/key.pdf"
    assert "ChecksumSHA256" not in sent["Params"]


def test_s3_completion_reads_checksum_and_copies_to_immutable_key():
    payload = b"%PDF-1.7 verified bytes"
    checksum = sha256(payload).hexdigest()

    class Client:
        def __init__(self):
            self.calls = []

        def get_object(self, **kwargs):
            self.calls.append(("get", kwargs))
            return {"ContentLength": len(payload), "ContentType": "application/pdf",
                    "Body": io.BytesIO(payload)}

        def copy_object(self, **kwargs):
            self.calls.append(("copy", kwargs))

        def delete_object(self, **kwargs):
            self.calls.append(("delete", kwargs))

    raw = Client()
    storage = S3ObjectStorage(raw, "private-bucket")
    inspected = storage.inspect_upload(pending_upload())
    finalized = storage.finalize_upload(pending_upload())
    storage.delete_upload("private/key.pdf")

    assert inspected.sha256_hex == checksum
    assert finalized.object_key.startswith("private/files/")
    assert storage.finalize_upload(pending_upload()).object_key != finalized.object_key
    assert raw.calls[1][1]["CopySource"] == {
        "Bucket": "private-bucket", "Key": "private/key.pdf"}
    assert ("delete", {"Bucket": "private-bucket", "Key": "private/key.pdf"}) in raw.calls

    def failed_read(**kwargs):
        raise RuntimeError("unavailable")
    raw.get_object = failed_read
    with pytest.raises(RuntimeError, match="final_object_read_failed"):
        storage.finalize_upload(pending_upload())
    assert raw.calls[-1][0] == "delete"
    assert raw.calls[-1][1]["Key"] == raw.calls[-2][1]["Key"]
    assert raw.calls[-1][1]["Key"] != finalized.object_key


@pytest.mark.parametrize('source_key, final_prefix', [
    ('private/tmp/owner/paper-1/version-1/upload.pdf',
     'private/files/owner/paper-1/version-1/'),
    ('private/owner/paper-1/version-1/upload.pdf',
     'private/owner/paper-1/version-1/files/'),
])
def test_new_and_legacy_uploads_survive_temporary_object_lifecycle(source_key, final_prefix):
    payload = b'%PDF-1.7 lifecycle fixture'

    class Client:
        def __init__(self):
            self.objects = {source_key: payload}

        def copy_object(self, **kwargs):
            self.objects[kwargs['Key']] = self.objects[kwargs['CopySource']['Key']]

        def get_object(self, **kwargs):
            data = self.objects[kwargs['Key']]
            return {'ContentLength': len(data), 'ContentType': 'application/pdf',
                    'Body': io.BytesIO(data)}

    raw = Client()
    storage = S3ObjectStorage(raw, 'private-bucket')
    finalized = storage.finalize_upload(pending_upload(
        object_key=source_key, file_size=len(payload)))
    assert finalized.object_key.startswith(final_prefix)
    # 외부 저장소 규칙의 prefix 선택만 모사하며 실제 R2 삭제를 실행하지 않는다.
    for key in list(raw.objects):
        if key.startswith('private/tmp/'):
            del raw.objects[key]
    assert raw.objects[finalized.object_key] == payload
    assert finalized.sha256_hex == sha256(payload).hexdigest()
    assert finalized.pdf_header_valid


def test_s3_download_presign_is_get_only_and_uses_safe_inline_filename():
    class Client:
        def generate_presigned_url(self, operation, **kwargs):
            self.call = (operation, kwargs)
            return "https://objects.example/read"

    raw = Client()
    result = S3ObjectStorage(raw, "private-bucket").prepare_download(
        FileAccessRecord("ready", "논문.pdf", 1234, "application/pdf", "private/final.pdf"))
    operation, sent = raw.call
    assert operation == "get_object" and sent["HttpMethod"] == "GET"
    assert sent["ExpiresIn"] == 300
    assert sent["Params"]["Key"] == "private/final.pdf"
    assert sent["Params"]["ResponseContentType"] == "application/pdf"
    assert "filename*=UTF-8''%EB%85%BC%EB%AC%B8.pdf" in sent["Params"]["ResponseContentDisposition"]
    assert result.url == "https://objects.example/read"


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0)


class Pool:
    def __init__(self, results):
        self.conn = Connection(results)

    def connection(self):
        return nullcontext(self.conn)


def test_postgres_reservation_locks_quota_and_requires_owned_synced_version():
    pool = Pool([
        Result(), Result({"exists": 1}), Result(), Result({"used": 2048}),
        Result({"upload_id": UPLOAD_ID, "object_key": "private/key.pdf", "expires_at": NOW}),
    ])
    store = PostgresFileUploadStore(pool, now=lambda: NOW)

    result = store.reserve("user-1", PAPER_ID, "version-1", "local-v1", "paper.pdf",
                           1234, "application/pdf", SHA256)

    assert result == UploadReservation(UPLOAD_ID, "private/key.pdf", NOW)
    assert "pg_advisory_xact_lock" in pool.conn.calls[0][0]
    assert "r.selected_version_id=%s" in pool.conn.calls[1][0]
    assert pool.conn.calls[1][1] == (PAPER_ID, "user-1", "version-1")
    assert "FOR UPDATE" in pool.conn.calls[2][0]
    assert "status='ready'" in pool.conn.calls[3][0]
    assert pool.conn.calls[4][1][9].startswith("private/tmp/")


def test_postgres_reservation_rejects_quota_before_insert():
    pool = Pool([Result(), Result({"exists": 1}), Result(),
                 Result({"used": 1024 * 1024 * 1024})])
    store = PostgresFileUploadStore(pool, now=lambda: NOW)
    with pytest.raises(UploadQuotaExceeded):
        store.reserve("user-1", PAPER_ID, "version-1", "local-v1", "paper.pdf",
                      1, "application/pdf", SHA256)
    assert len(pool.conn.calls) == 4


def test_postgres_active_retry_reuses_reservation_without_double_counting_quota():
    existing = {"upload_id": UPLOAD_ID, "object_key": "private/key.pdf",
                "file_size": 1234, "local_version_id": "local-v1", "file_name": "paper.pdf",
                "sha256_hex": SHA256,
                "expires_at": NOW.replace(minute=10), "status": "pending"}
    pool = Pool([Result(), Result({"exists": 1}), Result(existing)])
    result = PostgresFileUploadStore(pool, now=lambda: NOW).reserve(
        "user-1", PAPER_ID, "version-1", "local-v1", "paper.pdf",
        1234, "application/pdf", SHA256)
    assert result == UploadReservation(UPLOAD_ID, "private/key.pdf", existing["expires_at"])
    assert len(pool.conn.calls) == 3

    pool = Pool([Result(), Result({"exists": 1}), Result(existing)])
    with pytest.raises(UploadReservationConflict):
        PostgresFileUploadStore(pool, now=lambda: NOW).reserve(
            "user-1", PAPER_ID, "version-1", "local-v1", "paper.pdf",
            1235, "application/pdf", SHA256)


def test_postgres_completion_reads_owned_pending_upload_and_marks_ready():
    pending_row = {
        "upload_id": UPLOAD_ID, "paper_id": PAPER_ID, "server_version_id": "version-1",
        "file_name": "paper.pdf", "file_size": 1234, "content_type": "application/pdf",
        "sha256_hex": SHA256, "object_key": "private/key.pdf",
        "expires_at": NOW + timedelta(minutes=10), "status": "pending", "completed_at": None,
    }
    ready_row = pending_row | {
        "object_key": "private/final.pdf", "status": "ready", "completed_at": NOW,
    }
    pool = Pool([Result(pending_row), Result(ready_row)])
    store = PostgresFileUploadStore(pool, now=lambda: NOW)

    pending = store.get_for_completion("user-1", UPLOAD_ID)
    completed = store.mark_ready("user-1", UPLOAD_ID, "private/final.pdf")

    assert pending == pending_upload()
    assert completed == completed_upload()
    assert pool.conn.calls[0][1] == ("user-1", UPLOAD_ID)
    assert "status='ready'" in pool.conn.calls[1][0]


def test_postgres_completion_expires_pending_reservation_atomically():
    expired = {
        "upload_id": UPLOAD_ID, "paper_id": PAPER_ID, "server_version_id": "version-1",
        "file_name": "paper.pdf", "file_size": 1234, "content_type": "application/pdf",
        "sha256_hex": SHA256, "object_key": "private/key.pdf",
        "expires_at": NOW - timedelta(seconds=1), "status": "pending", "completed_at": None,
    }
    pool = Pool([Result(expired), Result()])
    with pytest.raises(UploadExpired):
        PostgresFileUploadStore(pool, now=lambda: NOW).get_for_completion("user-1", UPLOAD_ID)
    assert "failure_reason='upload_expired'" in pool.conn.calls[1][0]


def test_postgres_file_access_requires_owned_version_and_returns_ready_object():
    pool = Pool([
        Result({"exists": 1}),
        Result({
            "file_name": "paper.pdf", "file_size": 1234,
            "content_type": "application/pdf", "object_key": "private/final.pdf",
            "status": "ready", "failure_reason": None,
            "expires_at": NOW + timedelta(minutes=10),
        }),
    ])
    result = PostgresFileUploadStore(pool, now=lambda: NOW).get_file_access(
        "user-1", PAPER_ID, "version-1")
    assert result == FileAccessRecord(
        "ready", "paper.pdf", 1234, "application/pdf", "private/final.pdf")
    assert pool.conn.calls[0][1] == (PAPER_ID, "user-1", "version-1")
    assert pool.conn.calls[1][1] == ("user-1", PAPER_ID, "version-1")


def test_postgres_file_access_expires_pending_without_exposing_object_key():
    pool = Pool([
        Result({"exists": 1}),
        Result({
            "file_name": "paper.pdf", "file_size": 1234,
            "content_type": "application/pdf", "object_key": "private/staging.pdf",
            "status": "pending", "failure_reason": None,
            "expires_at": NOW - timedelta(seconds=1),
        }),
        Result(),
    ])
    result = PostgresFileUploadStore(pool, now=lambda: NOW).get_file_access(
        "user-1", PAPER_ID, "version-1")
    assert result.status == "failed" and result.failure_reason == "upload_expired"
    assert result.object_key is None
    assert "status='failed'" in pool.conn.calls[2][0]
