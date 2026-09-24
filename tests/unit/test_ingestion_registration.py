"""동시 등록의 원자성과 기존 작업 재사용을 실제 SQLite로 검증한다."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from solo_leveling.domain.models import (
    IngestionDisposition, JobStatus, Paper, PaperVersion,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db, get_connection


def test_concurrent_registration_shares_version_and_job(tmp_path):
    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    barrier = Barrier(4)

    def register(number):
        barrier.wait(timeout=10)
        return repo.prepare_ingestion(db, Paper('p'),
            PaperVersion(f'v{number}', 'p', 'hash', 'paper.pdf', 'paper.pdf'), f'j{number}')

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(register, range(4)))
    assert sum(r.disposition == IngestionDisposition.STARTED for r in results) == 1
    assert sum(r.disposition == IngestionDisposition.IN_PROGRESS for r in results) == 3
    assert len({r.version_id for r in results}) == 1
    assert len({r.job_id for r in results}) == 1
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM paper_versions').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM processing_jobs').fetchone()[0] == 1


@pytest.mark.parametrize('status', [JobStatus.FAILED, JobStatus.INTERRUPTED])
def test_retry_preserves_old_job_and_version(tmp_path, status):
    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    first = repo.prepare_ingestion(db, Paper('p'), PaperVersion('v1', 'p', 'hash', 'a', 'a'), 'j1')
    repo.update_job_status(db, first.job_id, status)
    retry = repo.prepare_ingestion(db, Paper('p'), PaperVersion('v2', 'p', 'hash', 'a', 'a'), 'j2')
    assert retry.disposition == IngestionDisposition.STARTED
    assert retry.version_id == first.version_id
    assert retry.job_id != first.job_id
    assert retry.reused_existing
    assert repo.get_job(db, first.job_id).status == status


def test_unique_index_does_not_delete_existing_duplicates(tmp_path):
    import sqlite3

    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    repo.insert_paper(db, Paper('p'))
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DROP INDEX uq_versions_paper_hash')
    for number in (1, 2):
        repo.insert_version(db, PaperVersion(f'v{number}', 'p', 'hash', 'a', 'a'))
    with pytest.raises(sqlite3.IntegrityError):
        init_db(db)
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM paper_versions').fetchone()[0] == 2


def test_queued_job_is_reused(tmp_path):
    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    first = repo.prepare_ingestion(db, Paper('p'), PaperVersion('v1', 'p', 'hash', 'a', 'a'), 'j1')
    repo.update_job_status(db, first.job_id, JobStatus.QUEUED)
    second = repo.prepare_ingestion(db, Paper('p'), PaperVersion('v2', 'p', 'hash', 'a', 'a'), 'j2')
    assert second.disposition == IngestionDisposition.IN_PROGRESS
    assert second.job_id == first.job_id


def test_registration_scope_is_paper_and_file_hash(tmp_path):
    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    results = [repo.prepare_ingestion(db, Paper(paper_id),
        PaperVersion(f'v{i}', paper_id, file_hash, 'a', 'a'), f'j{i}')
        for i, (paper_id, file_hash) in enumerate((('p1', 'hash1'), ('p2', 'hash1'), ('p1', 'hash2')))]
    assert all(r.disposition == IngestionDisposition.STARTED for r in results)
    assert len({r.version_id for r in results}) == 3


def test_failed_job_insertion_rolls_back_new_paper_and_version(tmp_path):
    import sqlite3

    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    repo.prepare_ingestion(db, Paper('p1'), PaperVersion('v1', 'p1', 'hash', 'a', 'a'), 'j1')
    with pytest.raises(sqlite3.IntegrityError):
        repo.prepare_ingestion(db, Paper('p2'), PaperVersion('v2', 'p2', 'hash', 'a', 'a'), 'j1')
    with closing(get_connection(db)) as conn:
        assert conn.execute("SELECT 1 FROM papers WHERE paper_id='p2'").fetchone() is None
        assert conn.execute("SELECT 1 FROM paper_versions WHERE version_id='v2'").fetchone() is None
