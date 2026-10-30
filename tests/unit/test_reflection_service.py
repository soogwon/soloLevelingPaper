"""읽기 전 질문 답변의 저장·조회·수정과 세션 재개를 실제 SQLite로 검증한다(M1-07, #52)."""
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.reflection.service import (
    SESSION_COMPLETED, SESSION_IN_PROGRESS, SESSION_NOT_STARTED, ReflectionService,
)
from solo_leveling.domain.reflection import PHASE_AFTER, PHASE_BEFORE, ReflectionValidationError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from tests.unit.test_learning_persistence import publish, setup_db  # noqa: F401


@pytest.fixture
def ctx(setup_db):  # noqa: F811
    db, chunks = setup_db
    context = publish(db, chunks)
    return db, context.context_id, ReflectionService(db)


def test_new_session_lists_all_questions_unanswered(ctx):
    _, context_id, service = ctx
    session = service.get_session(context_id, PHASE_BEFORE)
    assert [i.template.template_id for i in session.items] == [
        'before.background', 'before.hypothesis', 'before.expectation']
    assert all(i.answer is None for i in session.items)
    assert (session.total, session.written, session.completed, session.status) == (3, 0, 0, SESSION_NOT_STARTED)


def test_questions_are_filtered_by_step(ctx):
    _, context_id, service = ctx
    assert service.get_session(context_id, PHASE_BEFORE, 'preview').total == 3
    assert service.get_session(context_id, PHASE_BEFORE, 'review').total == 0


def test_save_then_retrieve_after_service_restart(ctx):
    db, context_id, service = ctx
    service.save_answer(context_id, PHASE_BEFORE, 'before.hypothesis', '어텐션이 병렬화를 돕는다고 생각해요.', 'draft')
    reopened = ReflectionService(db)  # 새 서비스·새 연결로 세션 재개
    session = reopened.get_session(context_id, PHASE_BEFORE)
    answer = session.items[1].answer
    assert answer.text == '어텐션이 병렬화를 돕는다고 생각해요.' and answer.status == 'draft'
    assert session.items[0].answer is None and session.status == SESSION_IN_PROGRESS


def test_edit_keeps_session_link_and_created_at(ctx):
    db, context_id, service = ctx
    first = repo.save_reflection_answer(db, context_id, PHASE_BEFORE, 'before.background', '처음', 'draft')
    second = repo.save_reflection_answer(db, context_id, PHASE_BEFORE, 'before.background', '고친 답', 'completed')
    assert second.context_id == context_id == first.context_id
    assert second.created_at == first.created_at
    assert second.text == '고친 답' and second.status == 'completed'
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM reflection_answers').fetchone()[0] == 1
    assert service.get_session(context_id, PHASE_BEFORE).items[0].answer.text == '고친 답'


def test_status_moves_from_in_progress_to_completed(ctx):
    _, context_id, service = ctx
    for template_id in ('before.background', 'before.hypothesis'):
        session = service.save_answer(context_id, PHASE_BEFORE, template_id, '답', 'completed')
    assert (session.written, session.completed, session.status) == (2, 2, SESSION_IN_PROGRESS)
    session = service.save_answer(context_id, PHASE_BEFORE, 'before.expectation', '답', 'draft')
    assert (session.written, session.completed, session.status) == (3, 2, SESSION_IN_PROGRESS)
    session = service.save_answer(context_id, PHASE_BEFORE, 'before.expectation', '답', 'completed')
    assert session.status == SESSION_COMPLETED


def test_reverting_completed_to_draft_reopens_the_session(ctx):
    _, context_id, service = ctx
    for template_id in ('before.background', 'before.hypothesis', 'before.expectation'):
        service.save_answer(context_id, PHASE_BEFORE, template_id, '답', 'completed')
    session = service.save_answer(context_id, PHASE_BEFORE, 'before.hypothesis', '다시 쓰는 중', 'draft')
    assert session.status == SESSION_IN_PROGRESS and session.completed == 2


def test_invalid_answers_save_nothing(ctx):
    db, context_id, service = ctx
    for kwargs in (dict(template_id='nope', text='답', status='draft'),
                   dict(template_id='before.background', text='  ', status='completed'),
                   dict(template_id='before.background', text='답', status='submitted')):
        with pytest.raises(ReflectionValidationError):
            service.save_answer(context_id, PHASE_BEFORE, **kwargs)
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM reflection_answers').fetchone()[0] == 0


def test_after_phase_has_no_questions_yet_so_answers_are_rejected(ctx):
    _, context_id, service = ctx
    with pytest.raises(ReflectionValidationError):
        service.save_answer(context_id, PHASE_AFTER, 'before.background', '답', 'draft')
    assert service.get_session(context_id, PHASE_AFTER).total == 0


def test_unknown_context_is_not_found_and_nothing_is_saved(ctx):
    db, _, service = ctx
    with pytest.raises(ResourceNotFoundError):
        service.get_session('missing', PHASE_BEFORE)
    with pytest.raises(ResourceNotFoundError):
        service.save_answer('missing', PHASE_BEFORE, 'before.background', '답', 'draft')
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM reflection_answers').fetchone()[0] == 0


def test_answers_are_separated_per_context(ctx):
    db, context_id, service = ctx
    other = repo.get_learning_context(db, context_id)
    from dataclasses import replace
    repo.create_learning_context(db, replace(other, context_id='ctx2'))
    service.save_answer(context_id, PHASE_BEFORE, 'before.background', '첫 세션', 'draft')
    service.save_answer('ctx2', PHASE_BEFORE, 'before.background', '둘째 세션', 'draft')
    assert service.get_session(context_id, PHASE_BEFORE).items[0].answer.text == '첫 세션'
    assert service.get_session('ctx2', PHASE_BEFORE).items[0].answer.text == '둘째 세션'


def test_schema_init_is_repeatable_and_keeps_answers(ctx):
    db, context_id, service = ctx
    service.save_answer(context_id, PHASE_BEFORE, 'before.background', '유지', 'draft')
    init_db(db)
    init_db(db)
    assert service.get_session(context_id, PHASE_BEFORE).items[0].answer.text == '유지'


def test_foreign_keys_block_orphan_rows(ctx):
    db, _, _ = ctx
    with closing(get_connection(db)) as conn:
        with pytest.raises(Exception):
            with conn:
                conn.execute("INSERT INTO reflection_answers VALUES ('ghost','before','before.background','x','draft','t','t')")
