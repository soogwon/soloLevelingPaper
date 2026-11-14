from datetime import datetime, timedelta, timezone
import inspect
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from solo_leveling.interfaces.http.app import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.file_uploads import (
    MAX_FILE_BYTES, PostgresFileUploadStore, UploadAlreadyReady, UploadQuotaExceeded,
    UploadReservation, UploadReservationConflict, UploadTargetNotFound,
)
from solo_leveling.interfaces.http.object_storage import PresignedUpload, S3ObjectStorage
from solo_leveling.interfaces.http.settings import load_object_storage_settings


PAPER_ID = "00000000-0000-0000-0000-000000000201"
UPLOAD_ID = "00000000-0000-0000-0000-000000000601"
NOW = datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc)
SHA256 = "ab" * 32


class UploadStore:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def reserve(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return UploadReservation(UPLOAD_ID, "private/key.pdf", NOW)


class Storage:
    def __init__(self):
        self.calls = []

    def prepare_upload(self, reservation, **kwargs):
        self.calls.append((reservation, kwargs))
        return PresignedUpload("https://objects.example/upload", "PUT", {
            "Content-Type": "application/pdf", "x-amz-checksum-sha256": "checksum",
        }, NOW)


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
            "Content-Type": "application/pdf", "x-amz-checksum-sha256": "checksum",
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
    assert upload.headers["x-amz-checksum-sha256"] == sent["Params"]["ChecksumSHA256"]


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
