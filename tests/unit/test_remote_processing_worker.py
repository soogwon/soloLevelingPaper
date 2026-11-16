from contextlib import nullcontext
from hashlib import sha256
from io import BytesIO

import pytest

from solo_leveling.domain.remote_processing import ParsedDocument, ProcessingResult, ProcessingSource
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.interfaces.http.object_storage import DownloadedObject, S3ObjectStorage
from solo_leveling.interfaces.http.processing_jobs import ProcessingJob
from solo_leveling.workers.remote_processing import (
    PostgresRemoteProcessingInputStore,
    RemoteProcessingInput,
    RemoteProcessingInputRejected,
    RemoteProcessingWorker,
)


SHA = "a" * 64


def job(**changes):
    value = ProcessingJob(
        "job", "owner", "paper", "version", "full_processing", "request", SHA,
        "running", 1, 3, True, "lease",
    )
    return ProcessingJob(**{**value.__dict__, **changes})


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, rows=()):
        self.rows, self.calls = list(rows), []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.rows.pop(0) if self.rows else None)


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def connection(self):
        return nullcontext(self.conn)


def source(lease="lease"):
    return ProcessingSource(
        "owner", "job", "paper", "version", "parse", "translation", "embedding", SHA, 9, lease,
    )


def test_postgres_input_binds_owner_job_lease_upload_and_current_consents():
    owner_hash = sha256(b"owner").hexdigest()[:24]
    row = {
        "object_key": f"private/files/{owner_hash}/paper/version/file.pdf", "file_size": 9,
        "content_type": "application/pdf", "sha256_hex": SHA,
    }
    conn = Connection((row,))
    loaded = PostgresRemoteProcessingInputStore(Pool(conn)).load(job())

    assert loaded.object_key == row["object_key"]
    assert loaded.source.lease_token == "lease"
    sql, params = conn.calls[0]
    assert "j.lease_token=%s" in sql and "j.lease_expires_at>CURRENT_TIMESTAMP" in sql
    assert "sb.revoked_at IS NULL" in sql and "ab.revoked_at IS NULL" in sql
    assert "u.object_key LIKE 'private/files/%%'" in sql
    assert params == ("owner", "job", "lease")


def test_allowed_uses_original_execution_token_and_blocks_translation_when_dispatch_stopped():
    conn = Connection((None,))
    allowed = PostgresRemoteProcessingInputStore(Pool(conn)).allowed(source("old-lease"), "translation")

    assert not allowed
    sql, params = conn.calls[0]
    assert "ctl.dispatch_enabled=true" in sql
    assert params == ("owner", "job", "old-lease", "translation")


def test_postgres_input_rejects_object_key_outside_owner_paper_version_prefix():
    conn = Connection(({
        "object_key": "private/files/other-owner/paper/version/file.pdf",
        "file_size": 9, "content_type": "application/pdf", "sha256_hex": SHA,
    },))
    with pytest.raises(RemoteProcessingInputRejected, match="object_key_rejected"):
        PostgresRemoteProcessingInputStore(Pool(conn)).load(job())


@pytest.mark.parametrize("phase", ["", "publishing", "unknown"])
def test_allowed_rejects_unknown_phase_without_database_access(phase):
    conn = Connection()
    assert not PostgresRemoteProcessingInputStore(Pool(conn)).allowed(source(), phase)
    assert conn.calls == []


def test_worker_claims_reads_and_runs_without_marking_job_succeeded():
    claimed, prepared = job(), RemoteProcessingInput(source(), "private/files/paper.pdf", "application/pdf")

    class Jobs:
        def __init__(self): self.calls = []
        def claim(self, worker_id, *, lease_seconds):
            self.calls.append((worker_id, lease_seconds))
            return claimed

    class Inputs:
        def load(self, value):
            assert value == claimed
            return prepared
        def allowed(self, current, phase):
            assert current == prepared.source and phase == "parse"
            return True

    class Objects:
        def read_object(self, key, *, expected_size):
            assert (key, expected_size) == (prepared.object_key, 9)
            return DownloadedObject(key, 9, "application/pdf", SHA, True, b"%PDF-data")

    class Stages:
        def run(self, current, pdf):
            assert current == prepared.source and pdf == b"%PDF-data"
            return ProcessingResult(
                current, ParsedDocument(1, ()), TranslationSettings("provider", "model", "v1"),
                (), None, (), "prepared",
            )

    jobs = Jobs()
    result = RemoteProcessingWorker(
        jobs, Inputs(), Objects(), Stages(), worker_id="worker", lease_seconds=30,
    ).run_once()
    assert result.job == claimed and result.result.status == "prepared"
    assert jobs.calls == [("worker", 30)]
    assert not hasattr(jobs, "succeed")


def test_worker_rejects_r2_metadata_mismatch_before_processing():
    class Jobs:
        def claim(self, *_args, **_kwargs): return job()
    class Inputs:
        def load(self, _job): return RemoteProcessingInput(source(), "private/files/paper.pdf", "application/pdf")
        def allowed(self, _source, _phase): return True
    class Objects:
        def read_object(self, key, *, expected_size):
            return DownloadedObject(key, expected_size, "text/plain", SHA, True, b"%PDF-data")
    class Stages:
        def run(self, *_args): raise AssertionError("processing must not start")

    with pytest.raises(RemoteProcessingInputRejected, match="object_mismatch"):
        RemoteProcessingWorker(Jobs(), Inputs(), Objects(), Stages(), worker_id="worker").run_once()


class Body(BytesIO):
    closed_by_storage = False

    def close(self):
        self.closed_by_storage = True
        super().close()


class S3:
    def __init__(self, data=b"%PDF-data", content_type="application/pdf"):
        self.body, self.data, self.content_type = Body(data), data, content_type

    def get_object(self, **kwargs):
        assert kwargs == {"Bucket": "bucket", "Key": "private/files/paper.pdf"}
        return {"Body": self.body, "ContentLength": len(self.data), "ContentType": self.content_type}


def test_s3_reader_returns_verified_bytes_and_closes_stream():
    raw = S3()
    result = S3ObjectStorage(raw, "bucket").read_object(
        "private/files/paper.pdf", expected_size=len(raw.data),
    )
    assert result.body == raw.data and result.pdf_header_valid
    assert raw.body.closed_by_storage


def test_s3_reader_stops_when_object_exceeds_expected_size():
    raw = S3()
    with pytest.raises(RuntimeError, match="object_size_mismatch"):
        S3ObjectStorage(raw, "bucket").read_object("private/files/paper.pdf", expected_size=1)
    assert raw.body.closed_by_storage


@pytest.mark.parametrize("failure", ["read", "close", "both"])
def test_s3_reader_sanitizes_read_and_close_errors(failure):
    raw = S3()

    class FailingBody(Body):
        def read(self, size=-1):
            if failure in ("read", "both"):
                raise RuntimeError("private-signed-url-token")
            return super().read(size)

        def close(self):
            super().close()
            if failure in ("close", "both"):
                raise RuntimeError("private-close-token")

    raw.body = FailingBody(raw.data)
    with pytest.raises(RuntimeError, match="^object_read_failed$"):
        S3ObjectStorage(raw, "bucket").read_object("private/files/paper.pdf", expected_size=len(raw.data))
    assert raw.body.closed_by_storage


def test_s3_reader_rejects_actual_growth_even_when_declared_length_matches():
    raw = S3()
    raw.body = Body(raw.data + b"excess")
    with pytest.raises(RuntimeError, match="^object_size_mismatch$"):
        S3ObjectStorage(raw, "bucket").read_object("private/files/paper.pdf", expected_size=len(raw.data))
    assert raw.body.closed_by_storage


def test_s3_reader_accepts_short_reads_without_truncating_pdf_header():
    raw = S3()

    class ShortBody(Body):
        def read(self, size=-1):
            return super().read(min(size, 2))

    raw.body = ShortBody(raw.data)
    result = S3ObjectStorage(raw, "bucket").read_object("private/files/paper.pdf", expected_size=len(raw.data))
    assert result.body == raw.data and result.pdf_header_valid
    assert raw.body.closed_by_storage
