from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from solo_leveling.interfaces.http.remote_uploads import (
    PostgresRemoteUploadIntentStore,
    RemoteUploadConflict,
    RemoteUploadVerificationService,
    _payload_hash,
)
from solo_leveling.interfaces.http.file_uploads import PendingUpload, UploadReservation
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
        def reserve_upload(self, owner, intent):
            return UploadReservation("u", pending.object_key, NOW), pending
        def mark_verified(self, owner, intent, key):
            self.marked = key
            return key
    class Objects:
        def inspect_upload(self, upload):
            return StoredObject(upload.object_key, 10, "application/pdf", SHA, True)
        def finalize_upload(self, upload):
            return StoredObject("private/files/final.pdf", 10, "application/pdf", SHA, True)
        def delete_upload(self, key):
            self.deleted = key
    store, objects = Store(), Objects()
    assert RemoteUploadVerificationService(store, objects).complete("owner", "intent") == "private/files/final.pdf"
    assert store.marked == "private/files/final.pdf" and objects.deleted == pending.object_key


def test_verification_service_rejects_checksum_without_state_change():
    pending = PendingUpload("u", "p", "v", "paper.pdf", 10, "application/pdf", SHA,
                            "private/tmp/object.pdf", NOW, "pending")
    class Store:
        def reserve_upload(self, *_): return UploadReservation("u", pending.object_key, NOW), pending
        def mark_verified(self, *_): pytest.fail("검증 실패 객체를 게시하면 안 됨")
    class Objects:
        def inspect_upload(self, upload): return StoredObject(upload.object_key, 10, "application/pdf", "b"*64, True)
    with pytest.raises(ValueError, match="remote_upload_object_invalid"):
        RemoteUploadVerificationService(Store(), Objects()).complete("owner", "intent")
