import pytest

import solo_leveling.application.server_sync.resume as resume_module
from solo_leveling.application.server_sync.resume import (
    ResumeRequestExpired, ResumeRequestResolver, ResumeRequestUnavailable,
    _available_search_data, _resume_position_status,
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


def test_resolver_preserves_selected_annotation_body_and_quote_snapshots():
    annotation = {
        "annotation_id": "annotation-1",
        "kind": "highlight",
        "body": "저장 당시 메모",
        "segments": [
            {"quote_snapshot": "Attention is all you need.", "chunk_id": "old-chunk"},
        ],
    }
    result = ResumeRequestResolver(Client(Response(200, payload(
        annotation_id="annotation-1", annotation=annotation,
    )))).resolve("resume_code")

    assert result["annotation"] == annotation


@pytest.mark.parametrize("annotation_id,annotation", [
    ("annotation-1", None),
    (None, {"annotation_id": "annotation-1", "kind": "note", "body": "memo",
            "segments": []}),
    ("annotation-1", {"annotation_id": "other", "kind": "note", "body": "memo",
                      "segments": []}),
    ("annotation-1", {"annotation_id": "annotation-1", "kind": "unknown",
                      "body": "memo", "segments": []}),
    ("annotation-1", {"annotation_id": "annotation-1", "kind": "note",
                      "body": "memo", "segments": [{}]}),
])
def test_resolver_rejects_malformed_selected_annotation(annotation_id, annotation):
    with pytest.raises(ServerResponseInvalid):
        ResumeRequestResolver(Client(Response(200, payload(
            annotation_id=annotation_id, annotation=annotation,
        )))).resolve("resume_code")


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


@pytest.mark.parametrize("ready,reason", [
    (True, "local_search_data_ready"),
    (False, "local_search_data_check_required"),
])
def test_resolver_reports_search_readiness_when_chroma_is_configured(
        tmp_path, monkeypatch, ready, reason):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fixture")
    db = local_database(tmp_path, stored_path=pdf)
    checked = []
    chroma = object()

    def search_status(*args):
        checked.append(args)
        return ready

    monkeypatch.setattr(resume_module, "_available_search_data", search_status)
    monkeypatch.setattr(resume_module, "_resume_position_status",
                        lambda *args: "local_position_ready")
    result = ResumeRequestResolver(
        Client(Response(200, payload())), str(db), tmp_path, chroma,
    ).resolve("resume_code")

    assert result["file_access_status"] == "local_file_ready"
    assert result["status"] == "prepared"
    assert result["reason"] == reason
    assert result["resume_position_status"] == (
        "local_position_ready" if ready else "not_checked")
    assert checked == [(str(db), chroma, "local-paper", "local-context",
                        "version-1")]


def test_resolver_does_not_claim_resume_ready_when_saved_position_is_unavailable(
        tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fixture")
    db = local_database(tmp_path, stored_path=pdf)
    monkeypatch.setattr(resume_module, "_available_search_data", lambda *args: True)
    monkeypatch.setattr(resume_module, "_resume_position_status",
                        lambda *args: "local_position_unavailable")

    result = ResumeRequestResolver(
        Client(Response(200, payload())), str(db), tmp_path, object(),
    ).resolve("resume_code")

    assert result["file_access_status"] == "local_file_ready"
    assert result["resume_position_status"] == "local_position_unavailable"
    assert result["status"] == "action_required"
    assert result["reason"] == "local_resume_position_unavailable"


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


def test_search_readiness_requires_matching_db_members_and_vector_metadata(tmp_path):
    import sqlite3

    db = tmp_path / "search.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE learning_contexts (
            context_id TEXT PRIMARY KEY, version_id TEXT, embedding_set_id TEXT
        );
        CREATE TABLE search_indexes (
            embedding_set_id TEXT PRIMARY KEY, version_id TEXT, chunk_count INTEGER
        );
        CREATE TABLE search_index_chunks (embedding_set_id TEXT, chunk_id TEXT);
        INSERT INTO learning_contexts VALUES ('context', 'version', 'embedding');
        INSERT INTO search_indexes VALUES ('embedding', 'version', 2);
        INSERT INTO search_index_chunks VALUES ('embedding', 'chunk-1');
        INSERT INTO search_index_chunks VALUES ('embedding', 'chunk-2');
    """)
    conn.commit()
    conn.close()

    class Collection:
        size = 2
        metadata_count = 2

        def count(self):
            return self.size

        def get(self, *, ids, include):
            assert set(ids) == {"chunk-1", "chunk-2"}
            assert include == ["metadatas"]
            metadata = {"embedding_set_id": "embedding", "paper_id": "paper",
                        "version_id": "version"}
            return {"ids": ids, "metadatas": [metadata.copy() for _ in range(self.metadata_count)]}

    class ChromaClient:
        def get_collection(self, name):
            assert name == "chunks-embedding"
            return Collection()

    client = ChromaClient()
    def ready():
        return _available_search_data(str(db), client, "paper", "context", "version")

    assert ready()
    Collection.metadata_count = 1
    assert not ready()
    Collection.metadata_count = 2
    Collection.size = 3
    assert not ready()
    Collection.size = 2
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM search_index_chunks WHERE chunk_id='chunk-2'")
    conn.commit()
    conn.close()
    assert not ready()

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE search_indexes")
    conn.commit()
    conn.close()
    assert not ready()


@pytest.mark.parametrize("position,expected", [
    (None, "no_saved_position"),
    ({}, "local_position_unavailable"),
    ({"chunk_id": "missing", "pdf_page": 2}, "local_position_unavailable"),
    ({"pdf_page": 2}, "local_position_ready"),
    ({"pdf_page": 9}, "local_position_unavailable"),
    ({"section_id": "section"}, "local_position_ready"),
    ({"section_id": "section", "pdf_page": 3}, "local_position_ready"),
    ({"section_id": "section", "pdf_page": 9}, "local_position_unavailable"),
    ({"section_id": "other-revision"}, "local_position_unavailable"),
    ({"chunk_id": "missing"}, "local_position_unavailable"),
    ({"chunk_id": "chunk", "pdf_page": 3}, "local_position_unavailable"),
    ({"chunk_id": "chunk", "section_id": "other"}, "local_position_unavailable"),
    ({"chunk_id": "chunk", "pdf_page": 2, "section_id": "section"},
     "local_position_ready"),
])
def test_resume_position_must_match_published_context_chunk(tmp_path, position, expected):
    import sqlite3

    db = tmp_path / "position.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE learning_contexts (
            context_id TEXT PRIMARY KEY, parse_revision_id TEXT, embedding_set_id TEXT
        );
        CREATE TABLE search_index_chunks (embedding_set_id TEXT, chunk_id TEXT);
        CREATE TABLE chunks (
            chunk_id TEXT, parse_revision_id TEXT, pdf_page INTEGER, section_id TEXT
        );
        CREATE TABLE sections (
            parse_revision_id TEXT, section_id TEXT, start_pdf_page INTEGER, end_pdf_page INTEGER
        );
        INSERT INTO sections VALUES ('parse', 'section', 2, 4);
        INSERT INTO sections VALUES ('older', 'other-revision', 1, 9);
        INSERT INTO learning_contexts VALUES ('context', 'parse', 'embedding');
        INSERT INTO search_index_chunks VALUES ('embedding', 'chunk');
        INSERT INTO chunks VALUES ('chunk', 'parse', 2, 'section');
    """)
    conn.commit()
    conn.close()

    assert _resume_position_status(str(db), "context", position) == expected
