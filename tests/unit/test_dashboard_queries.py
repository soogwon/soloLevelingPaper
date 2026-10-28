"""대시보드의 맥락 격리·저장값 집계·읽기 전용 동작을 실제 SQLite로 확인한다."""

from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
import sqlite3

import pytest

from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, ResourceNotFoundError,
)
from solo_leveling.application.quiz.attempts import QuizAttemptService
from solo_leveling.domain.models import Evidence, ProcessingJob
from solo_leveling.domain.paper_summary import SummaryItem
from solo_leveling.domain.quiz import QuizQuestion
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.dashboard_queries import DashboardReader
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import setup_db, publish


def test_empty_dashboard_does_not_write_or_expose_paths(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    before = Path(db).read_bytes()
    result = DashboardReader(db).get(context.context_id)
    assert Path(db).read_bytes() == before
    assert result['summary'] is None
    assert result['progress'] == {'recorded': False, 'current_step': 'preview',
        'completed_steps': [], 'total_steps': 4, 'completion_ratio': 0.0, 'last_studied_at': None}
    assert all(s['attempt_count'] == 0 and s['best_score'] is None for s in result['quiz_scores'])
    assert result['processing']['job_id'] == 'job'
    assert result['file_display_name'] == 'paper.pdf'
    assert '/private' not in str(result) and 'C:\\private' not in str(result)
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_progress').fetchone()[0] == 0


def test_saved_data_and_score_order_match_existing_service(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.save_evidences(db, 'ctx', [Evidence('e1', 'c1', '첫째.', 'First.')])
    repo.save_summary(db, 'ctx', 'paper', [SummaryItem('claim', '<b>저장된 요약</b>', ('e1',))])
    repo.complete_step(db, 'ctx', 'preview')
    repo.save_quiz(db, 'ctx', 'preview', [QuizQuestion('short_answer', '질문', ('e1',), answer='정답', grading_criteria='정답 일치')])
    # 같은 제출 시각에도 기존 서비스와 같은 삽입 순서로 최근 시도를 선택한다.
    ids = iter(['z-first', 'a-last'])
    service = QuizAttemptService(db, id_factory=lambda: next(ids), clock=lambda: '2026-10-27T00:00:00Z')
    service.submit('ctx', 'preview', {0: '정답'})
    service.submit('ctx', 'preview', {0: '오답'})
    before = Path(db).read_bytes()
    data = DashboardReader(db).get('ctx')
    assert data['progress']['recorded'] is True
    assert data['progress']['completion_ratio'] == .25
    assert data['progress']['current_step'] == 'structure'
    assert data['summary']['items'][0]['evidence_ids'] == ['e1']
    assert data['quiz_scores'] == [asdict(s) for s in service.step_scores('ctx')]
    assert data['quiz_scores'][0]['latest_score'] == 0
    assert data['quiz_scores'][0]['best_score'] == 1
    assert Path(db).read_bytes() == before
    # 같은 논문이라도 다른 학습 맥락의 요약·진행·점수를 가져오지 않는다.
    repo.create_learning_context(db, replace(context, context_id='other', goal='skim'))
    other = DashboardReader(db).get('other')
    assert other['summary'] is None and not other['progress']['recorded']
    assert all(s['attempt_count'] == 0 for s in other['quiz_scores'])
    # 재처리 작업이 추가되어도 기존 맥락의 게시 작업을 표시한다.
    repo.insert_job(db, ProcessingJob('later', 'v1'))
    assert DashboardReader(db).get('ctx')['processing']['job_id'] == 'job'


def test_unknown_context_and_missing_database_do_not_create_data(setup_db, tmp_path):
    db, _ = setup_db
    with pytest.raises(ResourceNotFoundError):
        DashboardReader(db).get('unknown')
    with pytest.raises(InvalidArgumentError):
        DashboardReader(db).get('  ')
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(sqlite3.OperationalError):
        DashboardReader(str(missing)).get('ctx')
    assert not missing.exists()


@pytest.mark.parametrize('mutation', [
    "UPDATE learning_contexts SET version_id='other'",
    "INSERT INTO learning_progress VALUES ('ctx','preview','[\"preview\",\"preview\"]',NULL,NULL,'now')",
    "INSERT INTO quiz_attempts VALUES ('bad','ctx','q','preview',2,1,'now')",
])
def test_inconsistent_links_or_values_are_rejected(setup_db, mutation):
    db, chunks = setup_db
    publish(db, chunks)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(mutation)
    with pytest.raises(DataIntegrityError):
        DashboardReader(db).get('ctx')


def test_summary_cannot_reference_another_context(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.create_learning_context(db, replace(context, context_id='other', goal='skim'))
    repo.save_evidences(db, 'other', [Evidence('other-e', 'c1', '첫째.', 'First.')])
    with closing(get_connection(db)) as conn, conn:
        conn.execute("INSERT INTO summaries VALUES ('s','ctx','paper','now')")
        conn.execute('INSERT INTO summary_items VALUES (?,?,?,?,?)',
                     ('s', 0, 'claim', '잘못 연결된 요약', '["other-e"]'))
    with pytest.raises(DataIntegrityError):
        DashboardReader(db).get('ctx')
