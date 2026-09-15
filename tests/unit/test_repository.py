import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from solo_leveling.domain.models import (
    Chunk,
    JobStage,
    JobStatus,
    Paper,
    ParseRevision,
    PaperVersion,
    ProcessingJob,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db


def _db(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


def test_insert_and_find_version_by_hash(tmp_path):
    db_path = _db(tmp_path)
    repo.insert_paper(db_path, Paper(paper_id="p1", title="Test Paper"))
    version = PaperVersion(
        version_id="v1", paper_id="p1", file_hash="abc123", stored_path="/x.pdf", original_filename="x.pdf"
    )
    repo.insert_version(db_path, version)

    found = repo.find_version_by_hash(db_path, "p1", "abc123")
    assert found is not None
    assert found.version_id == "v1"

    not_found = repo.find_version_by_hash(db_path, "p1", "different-hash")
    assert not_found is None


def test_foreign_key_enforced(tmp_path):
    """SQLite FK는 연결마다 활성화하고 테스트한다(21번 문서)"""
    db_path = _db(tmp_path)
    import sqlite3

    conn = repo.get_connection(db_path)
    with __import__("pytest").raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO paper_versions (version_id, paper_id, file_hash, stored_path, original_filename, created_at) "
            "VALUES ('v1', 'nonexistent-paper', 'h', 'p', 'f', 'now')"
        )
        conn.commit()
    conn.close()


def test_insert_and_query_chunks(tmp_path):
    db_path = _db(tmp_path)
    repo.insert_paper(db_path, Paper(paper_id="p1"))
    repo.insert_version(db_path, PaperVersion(version_id="v1", paper_id="p1", file_hash="h", stored_path="s", original_filename="f"))
    repo.insert_parse_revision(db_path, ParseRevision(parse_revision_id="pr1", version_id="v1", page_count=2))

    chunks = [
        Chunk(chunk_id="c1", parse_revision_id="pr1", chunk_index=0, original_text="첫 청크", pdf_page=1),
        Chunk(chunk_id="c2", parse_revision_id="pr1", chunk_index=1, original_text="둘째 청크", pdf_page=2),
    ]
    repo.insert_chunks(db_path, chunks)

    result = repo.get_chunks_by_parse_revision(db_path, "pr1")
    assert len(result) == 2
    assert result[0].chunk_index == 0
    assert result[1].pdf_page == 2

    single = repo.get_chunk(db_path, "c1")
    assert single.original_text == "첫 청크"


def test_job_status_transitions(tmp_path):
    db_path = _db(tmp_path)
    repo.insert_paper(db_path, Paper(paper_id="p1"))
    repo.insert_version(db_path, PaperVersion(version_id="v1", paper_id="p1", file_hash="h", stored_path="s", original_filename="f"))

    job = ProcessingJob(job_id="j1", version_id="v1", status=JobStatus.QUEUED)
    repo.insert_job(db_path, job)

    repo.update_job_status(db_path, "j1", JobStatus.PROCESSING, stage=JobStage.PARSE)
    fetched = repo.get_job(db_path, "j1")
    assert fetched.status == JobStatus.PROCESSING
    assert fetched.stage == JobStage.PARSE

    repo.update_job_status(db_path, "j1", JobStatus.READY, limitations=["extraction_empty"])
    fetched = repo.get_job(db_path, "j1")
    assert fetched.status == JobStatus.READY
    assert fetched.limitations == ["extraction_empty"]


def test_mark_interrupted_jobs_on_startup(tmp_path):
    """22번 문서: 재시작 시 미완료 processing 작업은 interrupted로 표시"""
    db_path = _db(tmp_path)
    repo.insert_paper(db_path, Paper(paper_id="p1"))
    repo.insert_version(db_path, PaperVersion(version_id="v1", paper_id="p1", file_hash="h", stored_path="s", original_filename="f"))
    repo.insert_job(db_path, ProcessingJob(job_id="j1", version_id="v1", status=JobStatus.PROCESSING))
    repo.insert_job(db_path, ProcessingJob(job_id="j2", version_id="v1", status=JobStatus.READY))

    count = repo.mark_interrupted_jobs_on_startup(db_path)

    assert count == 1
    assert repo.get_job(db_path, "j1").status == JobStatus.INTERRUPTED
    assert repo.get_job(db_path, "j2").status == JobStatus.READY  # 이미 끝난 건 안 건드림
