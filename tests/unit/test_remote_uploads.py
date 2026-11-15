from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from solo_leveling.interfaces.http.remote_uploads import (
    PostgresRemoteUploadIntentStore,
    RemoteUploadConflict,
    _payload_hash,
)


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
