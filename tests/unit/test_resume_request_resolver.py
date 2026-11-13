import pytest

from solo_leveling.application.server_sync.resume import (
    ResumeRequestExpired, ResumeRequestResolver, ResumeRequestUnavailable,
)
from solo_leveling.infrastructure.storage.server_client import ServerResponseInvalid


class Response:
    def __init__(self, status, data=None):
        self.status_code = status
        self.data = data
        self.closed = False

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def close(self):
        self.closed = True

    def raise_for_status(self):
        raise RuntimeError("server failure")


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response

    def connection_id(self):
        return "device-account"


def payload(**change):
    return {
        "resume_request_id": "request-1", "paper_id": "paper-1", "title": "Paper",
        "version_id": "version-1", "learning_record_id": "record-1",
        "last_position": {"pdf_page": 3}, "annotation_id": None, "annotation": None,
        "file_name": "paper.pdf", "file_access_status": "local_check_required",
        "status": "prepared", "reason": "local_file_required",
    } | change


def test_resolver_posts_code_and_returns_validated_selection():
    response = Response(200, payload())
    client = Client(response)
    result = ResumeRequestResolver(client).resolve("resume_code")
    assert result["last_position"] == {"pdf_page": 3}
    assert client.calls == [(('POST', '/v1/mcp/resume-requests/resolve'), {
        'json': {'code': 'resume_code'}, 'timeout': 15, 'expected_connection_id': None,
    })]
    assert response.closed


@pytest.mark.parametrize("status,error", [
    (404, ResumeRequestUnavailable), (410, ResumeRequestExpired),
])
def test_resolver_distinguishes_missing_and_expired(status, error):
    response = Response(status)
    with pytest.raises(error):
        ResumeRequestResolver(Client(response)).resolve("resume_code")
    assert response.closed


@pytest.mark.parametrize("data", [None, {}, payload(status=None), payload(last_position="page 3")])
def test_resolver_rejects_untrusted_server_shapes(data):
    response = Response(200, data)
    with pytest.raises(ServerResponseInvalid):
        ResumeRequestResolver(Client(response)).resolve("resume_code")
    assert response.closed


def local_database(tmp_path, *, version_id="version-1", stored_path=None):
    import sqlite3

    db = tmp_path / "local.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE server_subobject_mappings (
            account_id TEXT, object_type TEXT, local_object_id TEXT,
            local_subobject_id TEXT, server_object_id TEXT
        );
        CREATE TABLE learning_contexts (context_id TEXT PRIMARY KEY, version_id TEXT);
        CREATE TABLE paper_versions (
            version_id TEXT PRIMARY KEY, paper_id TEXT, stored_path TEXT
        );
    """)
    conn.execute("INSERT INTO server_subobject_mappings VALUES (?,?,?,?,?)",
                 ("device-account", "learning_record", "local-paper", "local-context",
                  "record-1"))
    conn.execute("INSERT INTO learning_contexts VALUES (?,?)", ("local-context", version_id))
    conn.execute("INSERT INTO paper_versions VALUES (?,?,?)",
                 (version_id, "local-paper", str(stored_path) if stored_path else None))
    conn.commit()
    conn.close()
    return db


def test_resolver_reports_same_pc_file_without_claiming_search_ready(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fixture")
    db = local_database(tmp_path, stored_path=pdf)

    result = ResumeRequestResolver(Client(Response(200, payload())), str(db), tmp_path).resolve("resume_code")

    assert result["file_access_status"] == "local_file_ready"
    assert result["status"] == "prepared"
    assert result["reason"] == "local_search_data_check_required"


def test_resolver_reports_missing_mapping_on_a_different_pc(tmp_path):
    db = local_database(tmp_path, stored_path=tmp_path / "paper.pdf")
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM server_subobject_mappings")
    conn.commit()
    conn.close()

    result = ResumeRequestResolver(Client(Response(200, payload())), str(db), tmp_path).resolve("resume_code")

    assert result["file_access_status"] == "local_mapping_missing"
    assert result["status"] == "action_required"
    assert result["reason"] == "local_mapping_missing"


@pytest.mark.parametrize("version_id,has_file,expected", [
    ("version-2", True, "local_version_mismatch"),
    ("version-1", False, "local_file_missing"),
])
def test_resolver_reports_local_file_blockers(tmp_path, version_id, has_file, expected):
    pdf = tmp_path / "paper.pdf"
    if has_file:
        pdf.write_bytes(b"%PDF fixture")
    db = local_database(tmp_path, version_id=version_id, stored_path=pdf)

    result = ResumeRequestResolver(Client(Response(200, payload())), str(db), tmp_path).resolve("resume_code")

    assert result["file_access_status"] == expected
    assert result["status"] == "action_required"
    assert result["reason"] == expected


def test_relative_pdf_uses_configured_root_and_request_connection(tmp_path, monkeypatch):
    class SwitchingClient(Client):
        active = "device-account"

        def connection_id(self):
            return self.active

        def request(self, *args, expected_connection_id, **kwargs):
            assert expected_connection_id == self.active
            self.active = "other-device"
            return self.response

    root = tmp_path / "pdfs"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF-1.7")
    monkeypatch.chdir(tmp_path)
    db = local_database(tmp_path, stored_path="paper.pdf")
    result = ResumeRequestResolver(SwitchingClient(Response(200, payload())), str(db), root)
    assert result.resolve("resume_code")["file_access_status"] == "local_file_ready"


def test_file_outside_managed_root_is_unavailable(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    other = tmp_path / "paper.pdf"
    other.write_bytes(b"%PDF-1.7")
    db = local_database(tmp_path, stored_path=other)
    resolver = ResumeRequestResolver(Client(Response(200, payload())), str(db), root)
    assert resolver.resolve("resume_code")["file_access_status"] == "local_file_missing"
