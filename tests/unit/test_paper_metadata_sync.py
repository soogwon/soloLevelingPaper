"""로컬 논문 메타데이터 outbox 동기화 계약."""

from datetime import datetime, timezone

import pytest

from solo_leveling.application.server_sync import PaperMetadataSyncService, SyncResult
from solo_leveling.domain.models import Paper
from solo_leveling.infrastructure.database.repository import insert_paper
from solo_leveling.infrastructure.database.schema import get_connection, init_db


NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
SERVER_PAPER_ID = "10000000-0000-0000-0000-000000000001"
SERVER_RECORD_ID = "10000000-0000-0000-0000-000000000002"
SERVER_SUMMARY_ID = "10000000-0000-0000-0000-000000000003"


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


def add_learning_record(database):
    with get_connection(database) as conn:
        conn.execute("INSERT INTO paper_versions VALUES (?,?,?,?,?,?)", (
            "version-1", "paper-local-1", "hash", r"C:\private\paper.pdf", "paper.pdf", NOW.isoformat()))
        conn.execute("INSERT INTO parse_revisions VALUES (?,?,?,?)", ("parse-1", "version-1", 10, NOW.isoformat()))
        conn.execute("INSERT INTO translation_revisions VALUES (?,?,?,?,?,?,?)", (
            "translation-1", "parse-1", None, None, None, None, NOW.isoformat()))
        conn.execute("INSERT INTO embedding_sets VALUES (?,?,?,?,?)", (
            "embedding-1", "translation-1", "model", 3, NOW.isoformat()))
        conn.execute("INSERT INTO processing_jobs VALUES (?,?,?,?,?,?,?)", (
            "job-1", "version-1", "succeeded", None, "[]", NOW.isoformat(), NOW.isoformat()))
        conn.execute("INSERT INTO search_indexes VALUES (?,?,?,?,?,?,?)", (
            "embedding-1", "version-1", "parse-1", "translation-1", "job-1", 1, 1))
        conn.execute("INSERT INTO learning_contexts VALUES (?,?,?,?,?,?,?,?)", (
            "context-1", "version-1", "parse-1", "translation-1", "embedding-1",
            "understand", "[]", NOW.isoformat()))
        conn.execute("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?)", (
            "parse-1", "method", "Method", None, 1, 1, 3, 5, 0, 0))
        conn.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)", (
            "chunk-1", "parse-1", 0, "text", None, None, "3", 3, "method"))
        conn.execute("INSERT INTO learning_progress VALUES (?,?,?,?,?,?)", (
            "context-1", "deep_read", '["preview","structure"]',
            '{"chunk_id":"chunk-1","pdf_page":3}', NOW.isoformat(), NOW.isoformat()))


def add_summary(database):
    add_learning_record(database)
    with get_connection(database) as conn:
        conn.execute("INSERT INTO summaries VALUES (?,?,?,?)", (
            "summary-1", "context-1", "paper", NOW.isoformat()))
        conn.execute("INSERT INTO summary_items VALUES (?,?,?,?,?)", (
            "summary-1", 0, "claim", "핵심 주장", '["evidence-1"]'))


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


def test_preview_does_not_create_server_identity(database):
    service = PaperMetadataSyncService(database, Client(), now=lambda: NOW)

    service.preview("paper-local-1")

    with get_connection(database) as conn:
        assert conn.execute("SELECT count(*) FROM server_sync_identity").fetchone()[0] == 0


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


def test_approved_paper_queues_and_sends_learning_record_without_local_path(database):
    add_learning_record(database)
    learning_response = Response(200, {
        "learning_record_id": SERVER_RECORD_ID, "revision": 1, "sync_status": "stored",
        "server_updated_at": "2026-11-12T12:00:00Z",
    })
    client = Client([Response(), learning_response])
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    preview = service.preview("paper-local-1")
    assert (preview.learning_record_count, preview.last_studied_at) == (1, NOW.isoformat())
    operation = service.grant_and_queue("paper-local-1", "paper-learning-sync-v2", preview=preview).operation_id

    result = service.sync(operation)

    assert result == SyncResult(operation, "synced", SERVER_PAPER_ID)
    assert [(call[0], call[1]) for call in client.calls] == [
        ("POST", "/v1/mcp/papers"),
        ("PUT", f"/v1/mcp/papers/{SERVER_PAPER_ID}/learning-records/context-1"),
    ]
    body = client.calls[1][2]["json"]
    assert body["file_name"] == "paper.pdf" and "local_learning_record_id" not in body
    assert body["last_position"] == {
        "chunk_id": "chunk-1", "pdf_page": 3, "printed_page_label": "3",
        "section_id": "method", "section_title": "Method",
    }
    assert body["completed_steps"] == ["preview", "structure"]
    assert r"C:\private" not in str(body)
    with get_connection(database) as conn:
        statuses = conn.execute(
            "SELECT object_type, status FROM sync_outbox ORDER BY object_type").fetchall()
        mapping = conn.execute("SELECT * FROM server_subobject_mappings").fetchone()
    assert [(row["object_type"], row["status"]) for row in statuses] == [
        ("learning_record", "synced"), ("paper", "synced")]
    assert (mapping["local_subobject_id"], mapping["server_object_id"]) == ("context-1", SERVER_RECORD_ID)


def test_approved_summary_syncs_after_learning_record_with_explicit_content(database):
    add_summary(database)
    learning_response = Response(200, {
        "learning_record_id": SERVER_RECORD_ID, "revision": 1, "sync_status": "stored",
        "server_updated_at": NOW.isoformat(),
    })
    summary_response = Response(200, {
        "summary_id": SERVER_SUMMARY_ID, "revision": 1, "sync_status": "stored",
        "server_updated_at": NOW.isoformat(),
    })
    client = Client([Response(), learning_response, summary_response])
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    preview = service.preview("paper-local-1")

    assert preview.summary_count == 1 and preview.summary_hash
    operation = service.grant_and_queue(
        "paper-local-1", "paper-learning-summary-sync-v3", preview=preview).operation_id
    result = service.sync(operation)

    assert result == SyncResult(operation, "synced", SERVER_PAPER_ID)
    assert [call[0] for call in client.calls] == ["POST", "PUT", "PUT"]
    assert client.calls[2][1].endswith(
        f"/learning-records/context-1/summaries/summary-1")
    body = client.calls[2][2]["json"]
    assert body == {
        "source_instance_id": body["source_instance_id"],
        "selected_version_id": "version-1", "scope": "paper", "author_type": "mcp",
        "items": [{"kind": "claim", "text": "핵심 주장", "evidence_ids": ["evidence-1"]}],
        "original_created_at": NOW.isoformat(), "source_updated_at": NOW.isoformat(),
    }
    assert "근거 본문" not in str(body)
    with get_connection(database) as conn:
        rows = conn.execute(
            "SELECT object_type, status FROM sync_outbox ORDER BY object_type").fetchall()
        mapping = conn.execute(
            "SELECT * FROM server_subobject_mappings WHERE object_type='summary'").fetchone()
    assert [(row["object_type"], row["status"]) for row in rows] == [
        ("learning_record", "synced"), ("paper", "synced"), ("summary", "synced")]
    assert mapping["server_object_id"] == SERVER_SUMMARY_ID


def test_summary_change_after_approval_conflicts_without_upload(database):
    add_summary(database)
    client = Client([Response(), Response(200, {
        "learning_record_id": SERVER_RECORD_ID, "revision": 1, "sync_status": "stored",
        "server_updated_at": NOW.isoformat(),
    })])
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    operation = service.grant_and_queue(
        "paper-local-1", "paper-learning-summary-sync-v3").operation_id
    with get_connection(database) as conn:
        conn.execute("UPDATE summary_items SET text='승인 뒤 변경된 주장'")

    result = service.sync(operation)

    assert result.status == "conflict" and result.error_code == "payload_changed"
    assert len(client.calls) == 2
    with get_connection(database) as conn:
        summary = conn.execute(
            "SELECT status, last_error_code FROM sync_outbox WHERE object_type='summary'").fetchone()
    assert dict(summary) == {"status": "conflict", "last_error_code": "payload_changed"}


def test_learning_change_after_approval_conflicts_without_sending_changed_payload(database):
    add_learning_record(database)
    client = Client([Response()])
    service = PaperMetadataSyncService(database, client, now=lambda: NOW)
    operation = service.grant_and_queue("paper-local-1", "paper-learning-sync-v2").operation_id
    with get_connection(database) as conn:
        conn.execute("UPDATE learning_progress SET completed_steps='[\"preview\",\"structure\",\"deep_read\"]'")

    assert service.sync(operation).status == "conflict"
    assert service.status(operation).error_code == "payload_changed"

    assert len(client.calls) == 1
    with get_connection(database) as conn:
        learning = conn.execute(
            "SELECT status, last_error_code FROM sync_outbox WHERE object_type='learning_record'").fetchone()
    assert dict(learning) == {"status": "conflict", "last_error_code": "payload_changed"}


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


def test_learning_retry_resumes_only_current_consent_after_restart(database):
    add_learning_record(database)
    response = Response(200, {"learning_record_id": SERVER_RECORD_ID, "revision": 1,
                              "server_updated_at": NOW.isoformat(), "sync_status": "stored"})
    client = Client([Response(), Response(503), response])
    service = PaperMetadataSyncService(database, client)
    other = service.grant_and_queue("paper-local-1", "v2").operation_id
    op = service.grant_and_queue("paper-local-1", "v2").operation_id
    assert service.sync(op).status == "retryable_failure"
    restarted = PaperMetadataSyncService(database, client)
    assert restarted.status(op).error_code == "server_unavailable"
    assert restarted.sync(op).status == "synced"
    assert restarted.status(other).status == "pending"
    assert [method for method, _, _ in client.calls] == ["POST", "PUT", "PUT"]
    assert client.calls[1] == client.calls[2]


def test_same_count_and_study_time_do_not_hide_changed_preview(database):
    from solo_leveling.application.server_sync.paper_metadata import SyncPreviewChanged

    add_learning_record(database)
    service = PaperMetadataSyncService(database, Client())
    preview = service.preview("paper-local-1")
    with get_connection(database) as conn:
        conn.execute("UPDATE learning_progress SET completed_steps='[]'")
    with pytest.raises(SyncPreviewChanged):
        service.grant_and_queue("paper-local-1", "v2", preview=preview)
    with get_connection(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


def test_same_summary_count_does_not_hide_changed_summary_preview(database):
    from solo_leveling.application.server_sync.paper_metadata import SyncPreviewChanged

    add_summary(database)
    service = PaperMetadataSyncService(database, Client())
    preview = service.preview("paper-local-1")
    with get_connection(database) as conn:
        conn.execute("UPDATE summary_items SET text='카드가 열린 뒤 바뀐 요약'")

    with pytest.raises(SyncPreviewChanged):
        service.grant_and_queue("paper-local-1", "paper-learning-summary-sync-v3", preview=preview)

    with get_connection(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM server_sync_consents").fetchone()[0] == 0
