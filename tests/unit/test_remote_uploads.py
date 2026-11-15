from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from solo_leveling.interfaces.http.remote_uploads import (
    ExpiredRemoteUpload,
    PostgresRemoteUploadIntentStore,
    RemoteUploadConflict,
    RemoteUploadVerificationService,
    _payload_hash,
)
from solo_leveling.interfaces.http.file_uploads import PendingUpload, UploadReservation, CompletedUpload
from solo_leveling.interfaces.http.object_storage import StoredObject


class Result:
    def __init__(self, row=None): self.row=row
    def fetchone(self): return self.row
class Conn:
    def __init__(self, rows): self.rows=list(rows); self.calls=[]
    def execute(self, sql, params=()):
        self.calls.append((sql,params))
        return Result(self.rows.pop(0) if ("SELECT intent_id" in sql or "RETURNING intent_id" in sql) else None)
class Pool:
    def __init__(self,*rows): self.conn=Conn(rows)
    @contextmanager
    def connection(self): yield self.conn


NOW=datetime(2026,1,1,tzinfo=timezone.utc)
SHA="a"*64


def test_creates_remote_paper_version_and_intent_without_fake_local_ids():
    returned={"intent_id":"i","paper_id":"p","server_version_id":"v","expires_at":NOW,"status":"created"}
    pool=Pool(None,returned)
    view=PostgresRemoteUploadIntentStore(pool,now=lambda:NOW).create("owner","key","paper.pdf",10,"application/pdf",SHA)
    assert (view.intent_id,view.paper_id,view.server_version_id)==("i","p","v")
    sql="\n".join(call[0] for call in pool.conn.calls)
    assert "NULL,NULL" in sql and "'remote'" in sql
    assert "server_processing_versions" in sql


def test_same_key_same_payload_replays_and_different_payload_conflicts():
    old={"intent_id":"i","paper_id":"p","server_version_id":"v","expires_at":NOW,
         "status":"created","payload_hash":_payload_hash("paper.pdf",10,"application/pdf",SHA)}
    store=PostgresRemoteUploadIntentStore(Pool(old),now=lambda:NOW)
    assert store.create("owner","key","paper.pdf",10,"application/pdf",SHA).intent_id=="i"
    with pytest.raises(RemoteUploadConflict):
        PostgresRemoteUploadIntentStore(Pool(old),now=lambda:NOW).create("owner","key","other.pdf",10,"application/pdf",SHA)


@pytest.mark.parametrize("request_key,file_name", [
    (" key", "paper.pdf"),
    ("k" * 129, "paper.pdf"),
    ("key", " paper.pdf"),
    ("key", "paper\n.pdf"),
    ("key", "p" * 256),
])
def test_rejects_ambiguous_or_unbounded_names_before_database(request_key, file_name):
    pool = Pool()
    with pytest.raises(ValueError, match="remote_upload_input_invalid"):
        PostgresRemoteUploadIntentStore(pool, now=lambda: NOW).create(
            "owner", request_key, file_name, 10, "application/pdf", SHA
        )
    assert pool.conn.calls == []


def test_rejects_naive_server_clock_before_database():
    pool = Pool()
    with pytest.raises(ValueError, match="remote_upload_clock_invalid"):
        PostgresRemoteUploadIntentStore(pool, now=lambda: NOW.replace(tzinfo=None)).create(
            "owner", "key", "paper.pdf", 10, "application/pdf", SHA
        )
    assert pool.conn.calls == []


def test_verification_service_checks_object_before_marking_verified():
    pending = PendingUpload("u", "p", "v", "paper.pdf", 10, "application/pdf", SHA,
                            "private/tmp/object.pdf", NOW, "pending")
    class Store:
        def get_completed(self, *_): return None
        def reserve_upload(self, owner, intent):
            return UploadReservation("u", pending.object_key, NOW), pending
        def mark_verified(self, owner, intent, key):
            self.marked = key
            return CompletedUpload("u", "p", "v", "paper.pdf", 10, "application/pdf", SHA, key, NOW)
    class Objects:
        def inspect_upload(self, upload):
            return StoredObject(upload.object_key, 10, "application/pdf", SHA, True)
        def finalize_upload(self, upload):
            return StoredObject("private/files/final.pdf", 10, "application/pdf", SHA, True)
        def delete_upload(self, key):
            self.deleted = key
    store, objects = Store(), Objects()
    assert RemoteUploadVerificationService(store, objects).complete("owner", "intent").object_key == "private/files/final.pdf"
    assert store.marked == "private/files/final.pdf" and objects.deleted == pending.object_key


def test_verification_service_rejects_checksum_without_state_change():
    pending = PendingUpload("u", "p", "v", "paper.pdf", 10, "application/pdf", SHA,
                            "private/tmp/object.pdf", NOW, "pending")
    class Store:
        def get_completed(self, *_): return None
        def reserve_upload(self, *_): return UploadReservation("u", pending.object_key, NOW), pending
        def mark_verified(self, *_): pytest.fail("검증 실패 객체를 게시하면 안 됨")
    class Objects:
        def inspect_upload(self, upload): return StoredObject(upload.object_key, 10, "application/pdf", "b"*64, True)
    with pytest.raises(ValueError, match="remote_upload_object_invalid"):
        RemoteUploadVerificationService(Store(), Objects()).complete("owner", "intent")


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_completed_retry_reuses_result_without_copy_or_reservation(cleanup_fails):
    pending = PendingUpload('u', 'p', 'v', 'paper.pdf', 10, 'application/pdf', SHA,
                            'private/tmp/object.pdf', NOW, 'pending')
    completed = CompletedUpload('u', 'p', 'v', 'paper.pdf', 10, 'application/pdf', SHA,
                                'private/files/final.pdf', NOW)
    class Store:
        saved = None
        reservations = 0
        def get_completed(self, *_): return self.saved
        def reserve_upload(self, *_):
            self.reservations += 1
            return UploadReservation('u', pending.object_key, NOW), pending
        def mark_verified(self, *_):
            self.saved = completed
            return completed
    class Objects:
        copies = 0
        def inspect_upload(self, upload): return StoredObject(upload.object_key, 10, 'application/pdf', SHA, True)
        def finalize_upload(self, upload):
            self.copies += 1
            return StoredObject(completed.object_key, 10, 'application/pdf', SHA, True)
        def delete_upload(self, _):
            if cleanup_fails: raise RuntimeError('합성 정리 실패')
    store, objects = Store(), Objects()
    service = RemoteUploadVerificationService(store, objects)
    assert service.complete('owner', 'intent') == completed
    assert service.complete('owner', 'intent') == completed
    assert store.reservations == objects.copies == 1


def test_expired_cleanup_retries_failures_without_exposing_object_keys():
    class Store:
        marked = []

        def claim_expired_objects(self, *, limit):
            assert limit == 2
            return (
                ExpiredRemoteUpload("intent-1", "private/tmp/owner/one.pdf"),
                ExpiredRemoteUpload("intent-2", "private/tmp/owner/two.pdf"),
            )

        def mark_expired_object_deleted(self, intent_id, object_key):
            self.marked.append((intent_id, object_key))

    class Objects:
        def __init__(self):
            self.calls = []

        def delete_upload(self, key):
            self.calls.append(key)
            if key.endswith("two.pdf"):
                raise RuntimeError("synthetic cleanup failure")

    objects = Objects()
    result = RemoteUploadVerificationService(Store(), objects).cleanup_expired(limit=2)
    assert result == {"claimed": 2, "deleted": 1, "failed": 1}
    assert objects.calls == ["private/tmp/owner/one.pdf", "private/tmp/owner/two.pdf"]
    assert Store.marked == [("intent-1", "private/tmp/owner/one.pdf")]


def test_expired_cleanup_query_excludes_ready_and_non_temporary_objects():
    class RowsResult(Result):
        def fetchall(self):
            return [{"intent_id": "intent-1", "object_key": "private/tmp/owner/one.pdf"}]

    class CleanupConn(Conn):
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return RowsResult()

    pool = Pool()
    pool.conn = CleanupConn([])
    rows = PostgresRemoteUploadIntentStore(pool, now=lambda: NOW).claim_expired_objects(limit=7)
    assert rows == (ExpiredRemoteUpload("intent-1", "private/tmp/owner/one.pdf"),)
    sql, params = pool.conn.calls[0]
    assert "u.status='pending'" in sql
    assert "u.source_kind='remote'" in sql
    assert "private/tmp/%" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert params == (7,)


@pytest.mark.parametrize("limit", [0, 1001, True])
def test_expired_cleanup_rejects_unbounded_limit(limit):
    pool = Pool()
    with pytest.raises(ValueError, match="remote_upload_cleanup_limit_invalid"):
        PostgresRemoteUploadIntentStore(pool, now=lambda: NOW).claim_expired_objects(limit=limit)
    assert pool.conn.calls == []


def test_deleted_expired_object_is_acknowledged_in_exact_intent_scope():
    class MarkConn(Conn):
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return Result({"upload_id": "upload"})

    pool = Pool()
    pool.conn = MarkConn([])
    PostgresRemoteUploadIntentStore(pool, now=lambda: NOW).mark_expired_object_deleted(
        "intent", "private/tmp/owner/object.pdf")
    sql, params = pool.conn.calls[0]
    assert "i.status='expired'" in sql
    assert "u.status='pending'" in sql
    assert "failure_reason='upload_expired'" in sql
    assert params == ("intent", "private/tmp/owner/object.pdf")
