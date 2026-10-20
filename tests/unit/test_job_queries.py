"""목록 검색·페이지 경계·작업 이력·읽기 전용 연결을 검증한다."""

from contextlib import closing
import sqlite3

import pytest

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from solo_leveling.infrastructure.database.schema import init_db, get_connection
from solo_leveling.infrastructure.database.job_queries import list_paper_jobs


@pytest.fixture
def database(tmp_path):
    path = str(tmp_path / 'jobs.sqlite3')
    init_db(path)
    with closing(get_connection(path)) as conn, conn:
        conn.execute("INSERT INTO papers VALUES ('p1', 'Attention 논문', 'local_file', '2026-01-01')")
        conn.execute("INSERT INTO papers VALUES ('p2', NULL, 'local_file', '2026-01-01')")
        conn.execute("INSERT INTO paper_versions VALUES ('v1','p1','hash1','/private/file','C:\\private\\paper.pdf','2026-01-01')")
        conn.execute("INSERT INTO paper_versions VALUES ('v2','p2','hash2','/private/other','/private/100%_결과.pdf','2026-01-01')")
        for job, version, status, stage in [
            ('j1', 'v1', 'failed', 'translate'),
            ('j2', 'v1', 'ready', 'index'),
            ('j3', 'v2', 'interrupted', None),
        ]:
            conn.execute('INSERT INTO processing_jobs VALUES (?,?,?,?,?,?,?)',
                         (job, version, status, stage, '[]', '2026-01-02', '2026-01-03'))
    return path


def test_history_and_safe_fields(database):
    result = list_paper_jobs(database)
    assert [item['job_id'] for item in result['jobs']] == ['j3', 'j2', 'j1']
    assert [item['status'] for item in result['jobs']] == ['interrupted', 'ready', 'failed']
    assert result['next_cursor'] is None
    assert result['jobs'][0]['title'] is None
    assert result['jobs'][0]['stage'] is None
    assert result['jobs'][0]['file_display_name'] == '100%_결과.pdf'
    assert result['jobs'][1]['file_display_name'] == 'paper.pdf'
    assert 'private' not in str(result)
    assert set(result['jobs'][0]) == {
        'paper_id', 'version_id', 'job_id', 'title', 'file_display_name',
        'status', 'stage', 'created_at', 'updated_at'}


@pytest.mark.parametrize(('query', 'ids'), [
    ('attention', ['j2', 'j1']), ('논문', ['j2', 'j1']),
    ('paper.pdf', ['j2', 'j1']), ('%', ['j3']), ('_', ['j3']),
    ("' OR 1=1 --", []), ('없는 논문', []),
])
def test_literal_search(database, query, ids):
    assert [item['job_id'] for item in list_paper_jobs(database, query=query)['jobs']] == ids


def test_keyset_pages_survive_new_job_and_status_change(database):
    first = list_paper_jobs(database, limit=1)
    with closing(get_connection(database)) as conn, conn:
        conn.execute("INSERT INTO processing_jobs VALUES ('j4','v1','queued',NULL,'[]','2026-01-04','2026-01-04')")
        conn.execute("UPDATE processing_jobs SET status='processing', updated_at='2026-01-05' WHERE job_id='j1'")
    second = list_paper_jobs(database, limit=1, cursor=first['next_cursor'])
    third = list_paper_jobs(database, limit=1, cursor=second['next_cursor'])
    assert [first['jobs'][0]['job_id'], second['jobs'][0]['job_id'],
            third['jobs'][0]['job_id']] == ['j3', 'j2', 'j1']
    assert third['next_cursor'] is None
    assert third['jobs'][0]['status'] == 'processing'


@pytest.mark.parametrize('kwargs', [
    {'limit': True}, {'limit': 0}, {'limit': 101}, {'limit': '2'},
    {'query': ''}, {'query': '  '}, {'query': 'x' * 201}, {'query': 12},
    {'cursor': ''}, {'cursor': '%%%x'}, {'cursor': 'e30='},
    {'cursor': 'x' * 2049}, {'cursor': 5},
])
def test_invalid_inputs(database, kwargs):
    with pytest.raises(InvalidArgumentError):
        list_paper_jobs(database, **kwargs)


def test_cursor_cannot_reuse_other_query(database):
    cursor = list_paper_jobs(database, query='paper', limit=1)['next_cursor']
    with pytest.raises(InvalidArgumentError):
        list_paper_jobs(database, query='Attention', cursor=cursor)


def test_empty_database_and_no_write(tmp_path):
    path = tmp_path / 'empty.sqlite3'
    init_db(str(path))
    before = path.read_bytes()
    assert list_paper_jobs(str(path)) == {'jobs': [], 'next_cursor': None}
    assert path.read_bytes() == before
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        list_paper_jobs(str(missing))
    assert not missing.exists()
