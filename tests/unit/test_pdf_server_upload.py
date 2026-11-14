from hashlib import sha256
from pathlib import Path

from solo_leveling.application.server_sync.pdf_upload import PaperPdfUploadService, PdfUploadStageError
from solo_leveling.infrastructure.database.schema import init_db, get_connection


PDF = b"%PDF-1.7\nserver upload test"
PAPER_ID = "paper-1"
SERVER_PAPER_ID = "00000000-0000-0000-0000-000000000201"
UPLOAD_ID = "00000000-0000-0000-0000-000000000601"


class Response:
    def __init__(self, status, data=None):
        self.status_code, self.data = status, data
        self.closed = False

    def json(self): return self.data
    def close(self): self.closed = True
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f"HTTP {self.status_code}")


class Client:
    def __init__(self): self.calls = []
    def connection_id(self): return "account-1"
    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/uploads"):
            return Response(201, {
                "upload_id": UPLOAD_ID, "upload_url": "https://objects.example/upload",
                "method": "PUT", "headers": {
                    "Content-Length": str(len(PDF)), "Content-Type": "application/pdf"},
                "expires_at": "2026-11-14T01:00:00Z", "max_file_size": 50 * 1024 * 1024,
            })
        return Response(200, {
            "upload_id": UPLOAD_ID, "paper_id": SERVER_PAPER_ID, "version_id": "version-1",
            "file_name": "paper.pdf", "file_size": len(PDF), "content_type": "application/pdf",
            "storage_status": "ready", "failure_reason": None,
        })


class UploadSession:
    def __init__(self): self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs["data"].read(), kwargs["headers"]))
        return Response(200)


def database(tmp_path: Path):
    db = tmp_path / "data.sqlite3"
    init_db(str(db))
    pdf_root = tmp_path / "pdfs"; pdf_root.mkdir()
    source = pdf_root / "managed.pdf"; source.write_bytes(PDF)
    conn = get_connection(str(db))
    conn.execute("INSERT INTO papers VALUES (?, ?, 'local_file', ?)", (PAPER_ID, "Paper", "2026-01-01T00:00:00Z"))
    conn.execute("INSERT INTO paper_versions VALUES (?, ?, ?, ?, ?, ?)", (
        "version-1", PAPER_ID, sha256(PDF).hexdigest(), str(source), "paper.pdf", "2026-01-01T00:00:00Z"))
    conn.execute("INSERT INTO parse_revisions VALUES ('parse-1', 'version-1', 1, '2026-01-01T00:00:00Z')")
    conn.execute("""INSERT INTO translation_revisions
        (translation_revision_id, parse_revision_id, provider, model, prompt_version, target_language, created_at)
        VALUES ('translation-1', 'parse-1', NULL, NULL, NULL, 'ko', '2026-01-01T00:00:00Z')""")
    conn.execute("INSERT INTO embedding_sets VALUES ('embed-1', 'translation-1', 'model', 1, '2026-01-01T00:00:00Z')")
    conn.execute("""INSERT INTO processing_jobs
        (job_id, version_id, status, stage, limitations, created_at, updated_at)
        VALUES ('job-1', 'version-1', 'ready', 'index', '[]', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""")
    conn.execute("""INSERT INTO search_indexes
        (embedding_set_id, version_id, parse_revision_id, translation_revision_id, job_id, chunk_count, is_default)
        VALUES ('embed-1', 'version-1', 'parse-1', 'translation-1', 'job-1', 1, 1)""")
    conn.execute("""INSERT INTO learning_contexts
        (context_id, version_id, parse_revision_id, translation_revision_id, embedding_set_id,
         goal, known_concepts, created_at)
        VALUES ('context-1', 'version-1', 'parse-1', 'translation-1', 'embed-1',
                'understand', '[]', '2026-01-01T00:00:00Z')""")
    conn.execute("INSERT INTO server_sync_identity VALUES (1, 'source-1')")
    conn.execute("""INSERT INTO server_object_mappings
        (account_id, source_instance_id, object_type, local_object_id, server_object_id, server_revision, synced_at)
        VALUES ('account-1', 'source-1', 'paper', ?, ?, 1, '2026-01-01T00:00:00Z')""",
        (PAPER_ID, SERVER_PAPER_ID))
    conn.commit(); conn.close()
    return db, pdf_root


def test_upload_uses_owned_mapping_streams_pdf_and_completes(tmp_path):
    db, root = database(tmp_path)
    client, session = Client(), UploadSession()
    service = PaperPdfUploadService(str(db), root, client, session)
    preview = service.preview(PAPER_ID)
    result = service.upload(preview)

    assert preview.file_size == len(PDF) and preview.sha256_hex == sha256(PDF).hexdigest()
    assert client.calls[0][2]["json"]["consent"] is True
    assert session.calls[0][0:3] == ("PUT", "https://objects.example/upload", PDF)
    assert client.calls[1][1] == f"/v1/uploads/{UPLOAD_ID}/complete"
    assert result.status == "ready"


def test_status_checks_server_without_uploading_pdf(tmp_path):
    db, root = database(tmp_path)

    class StatusClient(Client):
        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return Response(200, {
                "status": "ready", "file_name": "paper.pdf", "file_size": len(PDF),
                "content_type": "application/pdf", "reason": None,
            })

    client, session = StatusClient(), UploadSession()
    service = PaperPdfUploadService(str(db), root, client, session)
    result = service.status(service.preview(PAPER_ID))

    assert result["status"] == "ready"
    assert client.calls[0][0] == "GET"
    assert client.calls[0][1].endswith("/versions/version-1/upload-status")
    assert not session.calls


def test_changed_bytes_after_prepare_are_not_sent(tmp_path):
    import pytest

    db, root = database(tmp_path)

    class ChangedFileClient(Client):
        def request(self, method, path, **kwargs):
            response = super().request(method, path, **kwargs)
            (root / "managed.pdf").write_bytes(PDF[:-1] + b"!")
            return response

    client, session = ChangedFileClient(), UploadSession()
    service = PaperPdfUploadService(str(db), root, client, session)
    with pytest.raises(ValueError, match="PDF_UPLOAD_PREVIEW_CHANGED"):
        service.upload(service.preview(PAPER_ID))
    assert not session.calls
    assert len(client.calls) == 1


def test_storage_redirect_does_not_complete(tmp_path):
    import pytest
    from solo_leveling.infrastructure.storage.server_client import ServerResponseInvalid

    db, root = database(tmp_path)

    class RedirectSession:
        def request(self, method, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            return Response(307)

    client = Client()
    service = PaperPdfUploadService(str(db), root, client, RedirectSession())
    with pytest.raises(ServerResponseInvalid):
        service.upload(service.preview(PAPER_ID))
    assert len(client.calls) == 1


def test_prepare_failure_reports_stage_status_and_safe_code(tmp_path):
    import pytest

    db, root = database(tmp_path)

    class FailedClient(Client):
        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return Response(503, {"detail": {"code": "storage_unavailable"}})

    service = PaperPdfUploadService(str(db), root, FailedClient(), UploadSession())
    with pytest.raises(PdfUploadStageError) as caught:
        service.upload(service.preview(PAPER_ID))
    assert (caught.value.stage, caught.value.status_code, caught.value.detail_code) == (
        "prepare", 503, "storage_unavailable")


def test_prepare_failure_does_not_expose_untrusted_detail(tmp_path):
    import pytest

    db, root = database(tmp_path)

    class FailedClient(Client):
        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return Response(503, {"detail": {"code": "secret=do-not-display"}})

    service = PaperPdfUploadService(str(db), root, FailedClient(), UploadSession())
    with pytest.raises(PdfUploadStageError) as caught:
        service.upload(service.preview(PAPER_ID))
    assert caught.value.detail_code is None


def test_transfer_failure_reports_stage_and_does_not_complete(tmp_path):
    import pytest

    db, root = database(tmp_path)

    class ForbiddenSession(UploadSession):
        def request(self, method, url, **kwargs):
            return Response(403)

    client = Client()
    service = PaperPdfUploadService(str(db), root, client, ForbiddenSession())
    with pytest.raises(PdfUploadStageError) as caught:
        service.upload(service.preview(PAPER_ID))
    assert (caught.value.stage, caught.value.status_code) == ("transfer", 403)
    assert len(client.calls) == 1


def test_complete_failure_reports_stage_status_and_safe_code(tmp_path):
    import pytest

    db, root = database(tmp_path)

    class FailedCompleteClient(Client):
        def request(self, method, path, **kwargs):
            if path.endswith("/complete"):
                self.calls.append((method, path, kwargs))
                return Response(422, {"detail": {"code": "object_checksum_mismatch"}})
            return super().request(method, path, **kwargs)

    service = PaperPdfUploadService(str(db), root, FailedCompleteClient(), UploadSession())
    with pytest.raises(PdfUploadStageError) as caught:
        service.upload(service.preview(PAPER_ID))
    assert (caught.value.stage, caught.value.status_code, caught.value.detail_code) == (
        "complete", 422, "object_checksum_mismatch")
