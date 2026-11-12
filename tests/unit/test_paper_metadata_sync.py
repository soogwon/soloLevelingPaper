"""로컬 논문 메타데이터 outbox 동기화 계약."""

from datetime import datetime, timezone

import pytest

from solo_leveling.application.server_sync import PaperMetadataSyncService, SyncResult
from solo_leveling.domain.models import Paper
from solo_leveling.infrastructure.database.repository import insert_paper
from solo_leveling.infrastructure.database.schema import get_connection, init_db


NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
SERVER_PAPER_ID = "10000000-0000-0000-0000-000000000001"


class Response:
    def __init__(self, status_code=201, data=None):
        self.status_code = status_code
        self.data = data or {"paper_id": SERVER_PAPER_ID, "revision": 1, "sync_status": "stored",
                             "server_updated_at": "2026-11-12T12:00:00Z"}
        self.closed = False

    def json(self):
        return self.data

    def close(self):
        self.closed = True


class Client:
    def __init__(self, responses=(), connection_id="device-account-a"):
        self.responses, self.calls, self._connection_id = list(responses), [], connection_id

    def connection_id(self):
        return self._connection_id

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        value = self.responses.pop(0) if self.responses else Response()
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def database(tmp_path):
    path = str(tmp_path / "local.sqlite3")
    init_db(path)
    insert_paper(path, Paper("paper-local-1", "A paper", created_at="2026-11-01T10:00:00+09:00"))
    return path


def test_explicit_consent_queues_without_sending_or_storing_payload(database):
    client = Client()
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    queued = service.grant_and_queue("paper-local-1", "paper-sync-v1")
    assert queued.status == "pending"
    assert client.calls == []
    conn = get_connection(database)
    row = conn.execute("SELECT * FROM sync_outbox").fetchone()
    consent = conn.execute("SELECT * FROM server_sync_consents").fetchone()
    conn.close()
    assert row["payload_hash"] and "payload" not in row.keys()
    assert consent["notice_version"] == "paper-sync-v1"


def test_preview_is_read_only_and_status_recovers_after_restart(database):
    client = Client()
    first = PaperMetadataSyncService(database, client, now=lambda: NOW)
    assert first.preview("paper-local-1").title == "A paper"
    operation = first.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id

    restarted = PaperMetadataSyncService(database, client, now=lambda: NOW)

    assert restarted.status(operation) == SyncResult(operation, "pending")
    assert client.calls == []


def test_caller_supplied_operation_id_is_persisted_for_response_recovery(database):
    operation_id = "20000000-0000-0000-0000-000000000002"
    service = PaperMetadataSyncService(database, Client(), now=lambda: NOW)

    queued = service.grant_and_queue("paper-local-1", "paper-sync-v1", operation_id)

    assert queued.operation_id == operation_id
    assert service.status(operation_id) == SyncResult(operation_id, "pending")


def test_success_uses_operation_as_idempotency_key_and_atomically_records_mapping(database):
    response, client = Response(), Client([Response()])
    client.responses[0] = response
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    operation = service.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id
    result = service.sync(operation)
    assert (result.status, result.server_object_id) == ("synced", SERVER_PAPER_ID)
    method, path, request = client.calls[0]
    assert (method, path) == ("POST", "/v1/mcp/papers")
    assert request["headers"] == {"Idempotency-Key": operation}
    assert request["json"]["local_paper_id"] == "paper-local-1"
    assert request["json"]["authors"] is None
    assert response.closed
    conn = get_connection(database)
    outbox = conn.execute("SELECT status, attempt_count FROM sync_outbox").fetchone()
    mapping = conn.execute("SELECT * FROM server_object_mappings").fetchone()
    conn.close()
    assert dict(outbox) == {"status": "synced", "attempt_count": 1}
    assert (mapping["server_object_id"], mapping["server_revision"]) == (SERVER_PAPER_ID, 1)


def test_transport_failure_keeps_stable_key_for_retry(database):
    client = Client([ConnectionError("offline"), Response()])
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    operation = service.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id
    failed, retried = service.sync(operation), service.sync(operation)
    assert (failed.status, failed.error_code) == ("retryable_failure", "server_unavailable")
    assert retried.status == "synced"
    assert [call[2]["headers"]["Idempotency-Key"] for call in client.calls] == [operation, operation]


def test_payload_change_after_queue_becomes_conflict_without_sending(database):
    client = Client()
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    operation = service.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id
    conn = get_connection(database)
    conn.execute("UPDATE papers SET title='changed' WHERE paper_id='paper-local-1'")
    conn.commit()
    conn.close()
    result = service.sync(operation)
    assert (result.status, result.error_code) == ("conflict", "payload_changed")
    assert client.calls == []


def test_another_connection_cannot_send_existing_accounts_outbox(database):
    first = PaperMetadataSyncService(database, Client(connection_id="device-account-a"), now=lambda: NOW)
    operation = first.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id
    switched = PaperMetadataSyncService(database, Client(connection_id="device-account-b"), now=lambda: NOW)
    with pytest.raises(KeyError):
        switched.sync(operation)


@pytest.mark.parametrize(("response", "status", "code"), [
    (Response(409), "conflict", "server_conflict"),
    (Response(422), "action_required", "invalid_paper_request"),
    (Response(503), "retryable_failure", "server_unavailable"),
])
def test_server_failures_are_safe_local_states(database, response, status, code):
    client = Client([response])
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    operation = service.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id
    result = service.sync(operation)
    assert (result.status, result.error_code) == (status, code)
    assert response.closed


def test_late_failure_cannot_replace_completed_retry(database):
    client = Client()
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    operation = service.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id
    normal_request = client.request

    def delayed_failure(*args, **kwargs):
        client.request = normal_request
        assert service.sync(operation).status == "synced"
        return Response(503)

    client.request = delayed_failure
    result = service.sync(operation)
    assert (result.status, result.server_object_id) == ("synced", SERVER_PAPER_ID)
    assert service.sync(operation) == result


@pytest.mark.parametrize("change", [{"server_updated_at": None}, {"paper_id": 123}, {"revision": 2**63}])
def test_invalid_success_does_not_leave_outbox_syncing(database, change):
    response = Response()
    response.data.update(change)
    service = PaperMetadataSyncService(database, Client([response]), now=lambda: NOW)
    operation = service.grant_and_queue("paper-local-1", "paper-sync-v1").operation_id
    result = service.sync(operation)
    assert (result.status, result.error_code) == ("action_required", "invalid_server_response")

@pytest.mark.parametrize("change", ["title", "connection", "created_at"])
def test_approval_rejects_changed_preview_before_writing_consent(database, change):
    from solo_leveling.application.server_sync.paper_metadata import SyncPreviewChanged

    client = Client()
    service = PaperMetadataSyncService(database, client)
    preview = service.preview("paper-local-1")
    with get_connection(database) as conn:
        if change == "connection":
            client._connection_id = "other"
        elif change == "title":
            conn.execute("UPDATE papers SET title='changed'")
        else:
            conn.execute("UPDATE papers SET created_at='2026-11-01T00:00:00Z'")
    with pytest.raises(SyncPreviewChanged):
        service.grant_and_queue("paper-local-1", "v1", preview=preview)
    with get_connection(database) as conn:
        for table in ("sync_outbox", "server_sync_consents"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert not client.calls
