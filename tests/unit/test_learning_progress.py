"""학습 진행 상태(M1-04, #49)의 저장·재개·재시작 후 복원을 실제 SQLite로 검증한다."""
import sqlite3
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from tests.unit.test_learning_persistence import setup_db, publish


def test_new_context_starts_at_first_step_without_saving(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    progress = repo.get_learning_progress(db, context.context_id)
    assert progress.current_step == LEARNING_STEPS[0]
    assert progress.completed_steps == []
    assert progress.progress == 0.0
    assert progress.last_studied_at is None
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_progress').fetchone()[0] == 0


def test_set_current_step_saves_position_and_time(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    saved = repo.set_current_step(db, context.context_id, 'deep_read',
                                  {'chunk_id': 'c2', 'pdf_page': 2})
    assert saved.current_step == 'deep_read'
    assert saved.resume_position == {'chunk_id': 'c2', 'pdf_page': 2}
    assert saved.last_studied_at is not None
    # 위치를 주지 않으면 이전 재개 위치를 유지한다.
    moved = repo.set_current_step(db, context.context_id, 'review')
    assert moved.resume_position == {'chunk_id': 'c2', 'pdf_page': 2}


def test_complete_step_is_idempotent_and_advances(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    first = repo.complete_step(db, context.context_id, 'preview')
    again = repo.complete_step(db, context.context_id, 'preview')
    assert first.completed_steps == again.completed_steps == ['preview']
    assert again.current_step == 'structure'
    assert again.progress == pytest.approx(1 / len(LEARNING_STEPS))


def test_completing_all_steps_stays_on_last_step(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    for step in reversed(LEARNING_STEPS):
        done = repo.complete_step(db, context.context_id, step)
    assert done.completed_steps == list(LEARNING_STEPS)
    assert done.current_step == LEARNING_STEPS[-1]
    assert done.progress == 1.0


def test_progress_survives_restart_and_is_found_by_same_context_id(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.complete_step(db, context.context_id, 'preview')
    repo.set_current_step(db, context.context_id, 'deep_read', {'section_id': 's3'})
    init_db(db)  # 서버를 다시 시작한 것처럼 초기화를 다시 실행한다.
    again = repo.get_learning_progress(db, context.context_id)
    assert again.current_step == 'deep_read'
    assert again.completed_steps == ['preview']
    assert again.resume_position == {'section_id': 's3'}


def test_existing_database_without_progress_table_is_upgraded(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('DROP TABLE learning_progress')
    init_db(db)
    assert repo.get_learning_progress(db, context.context_id).current_step == LEARNING_STEPS[0]


def test_unknown_context_step_and_position_are_rejected(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    with pytest.raises(ResourceNotFoundError):
        repo.get_learning_progress(db, 'missing')
    with pytest.raises(ResourceNotFoundError):
        repo.complete_step(db, 'missing', 'preview')
    with pytest.raises(ValueError):
        repo.set_current_step(db, context.context_id, 'unknown-step')
    with pytest.raises(ValueError):
        repo.set_current_step(db, context.context_id, 'preview', {'page_text': 'x'})
    with pytest.raises(ValueError):
        repo.set_current_step(db, context.context_id, 'preview', {'pdf_page': True})
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_progress').fetchone()[0] == 0
