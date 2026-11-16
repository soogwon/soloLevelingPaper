"""삭제 의도의 선저장·응답 유실·재시작·계정 전환을 합성 전송으로 확인한다."""

import sqlite3
from types import SimpleNamespace
from uuid import uuid4
import pytest
from solo_leveling.application.server_sync.deletion import ServerPaperDeletionService, DeletionUnavailable
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired


class Transport:
    def __init__(self):
        self.connection = "account"
        self.calls = []
        self.rows = {}
        self.lose = False
        self.switch = False

    def connection_id(self):
        return self.connection

    def request(self, method, path, **options):
        assert options["expected_connection_id"] == self.connection
        self.calls.append((method, path))
        if method == "PUT":
            self.rows.setdefault(
                path,
                {
                    "paper_id": "server",
                    "request_id": str(uuid4()),
                    "status": "blocking",
                    "backup_erasure_confirmed": False,
                },
            )
        if self.lose:
            raise TimeoutError("private detail")
        if self.switch:
            self.connection = "other"
        return SimpleNamespace(
            status_code=200 if path in self.rows else 404, json=lambda: self.rows[path], close=lambda: None
        )


def setup(tmp_path):
    path = tmp_path / "local.sqlite"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE papers(paper_id TEXT,title TEXT)")
        c.execute("INSERT INTO papers VALUES ('local','합성 논문')")
        c.execute(
            "CREATE TABLE server_object_mappings(account_id TEXT,object_type TEXT,local_object_id TEXT,server_object_id TEXT)"
        )
        c.execute("INSERT INTO server_object_mappings VALUES ('account','paper','local','server')")
    transport = Transport()
    return path, transport, ServerPaperDeletionService(path, transport)


def test_lost_ack_restart_same_key_and_local_rows_preserved(tmp_path):
    path, t, s = setup(tmp_path)
    preview = s.preview("local")
    assert t.calls == []
    t.lose = True
    with pytest.raises(DeletionUnavailable):
        s.confirm(preview)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT state FROM client_deletion_intents").fetchone() == ("pending",)
        assert c.execute("SELECT count(*) FROM papers").fetchone()[0] == 1
    t.lose = False
    s = ServerPaperDeletionService(path, t)
    assert s.retry("local")["status"] == "blocking"
    assert t.calls[0] == t.calls[1] and len(t.rows) == 1
    assert s.status("local")["status"] == "blocking"
    assert t.calls[-1][0] == "GET"
    assert s.retry("local")["status"] == "blocking"
    assert t.calls[-1][0] == "GET"


def test_read_never_creates_and_other_account_cannot_retry(tmp_path):
    path, t, s = setup(tmp_path)
    with pytest.raises(KeyError):
        s.status("local")
    assert t.calls == []
    preview = s.preview("local")
    t.connection = "other"
    with pytest.raises(KeyError):
        s.confirm(preview)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM client_deletion_intents").fetchone()[0] == 0
    t.connection = "account"
    s.confirm(preview)
    t.connection = "other"
    before = len(t.calls)
    with pytest.raises(KeyError):
        s.retry("local")
    assert len(t.calls) == before


def test_account_change_during_response_drops_result(tmp_path):
    path, t, s = setup(tmp_path)
    t.switch = True
    with pytest.raises(ServerConnectionRequired):
        s.confirm(s.preview("local"))
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT state FROM client_deletion_intents").fetchone()[0] == "pending"


def test_invalid_response_keeps_request(tmp_path):
    path, t, s = setup(tmp_path)
    s.confirm(s.preview("local"))
    next(iter(t.rows.values()))["paper_id"] = "wrong"
    with pytest.raises(DeletionUnavailable):
        s.status("local")
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT state FROM client_deletion_intents").fetchone()[0] == "blocking"


def test_completed_state_is_not_downgraded_by_late_response(tmp_path):
    path, t, s = setup(tmp_path)
    s.confirm(s.preview("local"))
    result = next(iter(t.rows.values()))
    result["status"] = "complete"
    assert s.status("local")["status"] == "complete"
    result["status"] = "cleaning"
    assert s.status("local")["status"] == "complete"


def test_changed_mapping_cannot_retarget_existing_intent(tmp_path):
    path, t, s = setup(tmp_path)
    s.confirm(s.preview("local"))
    with sqlite3.connect(path) as c:
        c.execute("UPDATE server_object_mappings SET server_object_id='other-server'")
    before = len(t.calls)
    with pytest.raises(DeletionUnavailable):
        s.confirm(s.preview("local"))
    assert len(t.calls) == before


def test_intent_is_committed_before_network_and_survives_local_row_removal(tmp_path):
    path, transport, service = setup(tmp_path)
    original = transport.request

    def send(method, target, **options):
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT request_key FROM client_deletion_intents").fetchone()
            assert row is not None and target.endswith(row[0])
        return original(method, target, **options)

    transport.request = send
    service.confirm(service.preview("local"))
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM papers")
        conn.execute("DELETE FROM server_object_mappings")
    restarted = ServerPaperDeletionService(path, transport)
    assert restarted.status("local")["status"] == "blocking"
    assert len(transport.rows) == 1
