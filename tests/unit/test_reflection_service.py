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


def test_after_phase_lists_four_questions_and_rejects_before_ids(ctx):
    _, context_id, service = ctx
    session = service.get_session(context_id, PHASE_AFTER)
    assert [i.template.template_id for i in session.items] == [
        'after.claim', 'after.evidence', 'after.limitation', 'after.application']
    assert (session.total, session.written, session.status) == (4, 0, SESSION_NOT_STARTED)
    with pytest.raises(ReflectionValidationError):
        service.save_answer(context_id, PHASE_AFTER, 'before.background', '답', 'draft')


def test_after_answers_save_edit_and_resume_like_before_answers(ctx):
    db, context_id, service = ctx
    first = repo.save_reflection_answer(db, context_id, PHASE_AFTER, 'after.claim', '처음 이해', 'draft')
    second = repo.save_reflection_answer(db, context_id, PHASE_AFTER, 'after.claim', '고친 이해', 'completed')
    assert second.created_at == first.created_at and second.text == '고친 이해'
    reopened = ReflectionService(db)
    session = reopened.get_session(context_id, PHASE_AFTER)
    assert session.items[0].answer.text == '고친 이해' and (session.written, session.completed) == (1, 1)
    assert session.status == SESSION_IN_PROGRESS
    for template_id in ('after.evidence', 'after.limitation', 'after.application'):
        session = service.save_answer(context_id, PHASE_AFTER, template_id, '답', 'completed')
    assert session.status == SESSION_COMPLETED


def test_before_and_after_answers_do_not_mix(ctx):
    db, context_id, service = ctx
    service.save_answer(context_id, PHASE_BEFORE, 'before.expectation', '읽기 전 기대', 'completed')
    service.save_answer(context_id, PHASE_AFTER, 'after.claim', '읽은 뒤 주장', 'completed')
    assert service.get_session(context_id, PHASE_BEFORE).written == 1
    assert service.get_session(context_id, PHASE_AFTER).written == 1
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM reflection_answers').fetchone()[0] == 2


def _group(comparison, group_id):
    return [g for g in comparison.groups if g.group_id == group_id][0]


def _texts(group):
    return (group.before.answer and group.before.answer.text,
            [(i.template.template_id, i.answer and i.answer.status) for i in group.after])


def test_comparison_starts_empty_with_three_groups(ctx):
    _, context_id, service = ctx
    comparison = service.get_comparison(context_id)
    assert [(g.group_id, g.before.template.template_id, [i.template.template_id for i in g.after])
            for g in comparison.groups] == [
        ('hypothesis_vs_paper', 'before.hypothesis', ['after.claim', 'after.evidence']),
        ('background_vs_limitation', 'before.background', ['after.limitation']),
        ('expectation_vs_application', 'before.expectation', ['after.application'])]
    assert all(g.before.answer is None and all(i.answer is None for i in g.after) for g in comparison.groups)
    assert (comparison.before.total, comparison.before.written, comparison.before.status) == (3, 0, SESSION_NOT_STARTED)
    assert (comparison.after.total, comparison.after.written, comparison.after.status) == (4, 0, SESSION_NOT_STARTED)


def test_comparison_matches_the_agreed_example_with_counts(ctx):
    _, context_id, service = ctx
    save = service.save_answer
    save(context_id, PHASE_BEFORE, 'before.hypothesis', '새 방법은 항상 더 빠를 것이다.', 'completed')
    save(context_id, PHASE_BEFORE, 'before.background', '입력 길이가 실행 시간에 영향을 준다.', 'completed')
    save(context_id, PHASE_AFTER, 'after.claim', '가설과 달리 짧은 입력에서만 빨랐다.', 'completed')
    save(context_id, PHASE_AFTER, 'after.evidence', '표 2의 짧은 입력 실험을 다시 보겠다.', 'draft')
    save(context_id, PHASE_AFTER, 'after.limitation', '긴 입력에서도 같은 효과가 있는지는 확인하고 싶다.', 'completed')
    save(context_id, PHASE_AFTER, 'after.application', '우리 데이터에서 입력 길이별로 시간을 측정해 보겠다.', 'completed')
    comparison = service.get_comparison(context_id)
    assert _texts(_group(comparison, 'hypothesis_vs_paper')) == (
        '새 방법은 항상 더 빠를 것이다.', [('after.claim', 'completed'), ('after.evidence', 'draft')])
    assert _texts(_group(comparison, 'background_vs_limitation')) == (
        '입력 길이가 실행 시간에 영향을 준다.', [('after.limitation', 'completed')])
    # 읽기 전이 미작성이어도 읽기 후 답변은 그대로 보인다.
    expectation = _group(comparison, 'expectation_vs_application')
    assert expectation.before.answer is None
    assert expectation.after[0].answer.text == '우리 데이터에서 입력 길이별로 시간을 측정해 보겠다.'
    assert (comparison.before.written, comparison.before.completed) == (2, 2) and comparison.before.total == 3
    assert (comparison.after.written, comparison.after.completed) == (4, 3) and comparison.after.total == 4
    # 한계 답변이 완료여도 코드는 해결 여부를 판정하지 않는다: 문구와 상태만 있다.
    assert not hasattr(_group(comparison, 'background_vs_limitation'), 'resolved')


def test_empty_draft_counts_as_written_and_missing_row_is_not_started(ctx):
    _, context_id, service = ctx
    service.save_answer(context_id, PHASE_AFTER, 'after.claim', '', 'draft')
    comparison = service.get_comparison(context_id)
    claim = _group(comparison, 'hypothesis_vs_paper').after[0]
    assert claim.answer is not None and claim.answer.text == '' and claim.answer.status == 'draft'
    assert _group(comparison, 'hypothesis_vs_paper').after[1].answer is None
    assert (comparison.after.written, comparison.after.completed) == (1, 0)


def test_edit_then_reload_keeps_the_same_ids_and_shows_current_text(ctx):
    _, context_id, service = ctx
    service.save_answer(context_id, PHASE_BEFORE, 'before.hypothesis', '처음 가설', 'completed')
    service.save_answer(context_id, PHASE_AFTER, 'after.claim', '주장', 'completed')
    service.save_answer(context_id, PHASE_BEFORE, 'before.hypothesis', '고친 가설', 'completed')
    group = _group(service.get_comparison(context_id), 'hypothesis_vs_paper')
    assert group.before.template.template_id == 'before.hypothesis' and group.before.answer.text == '고친 가설'
    assert group.after[0].answer.text == '주장'


def test_comparison_reads_both_phases_in_one_snapshot(ctx, monkeypatch):
    db, context_id, service = ctx
    calls = []
    original = repo.get_reflection_answers_all
    monkeypatch.setattr(repo, 'get_reflection_answers_all', lambda *a: calls.append(a) or original(*a))
    monkeypatch.setattr(repo, 'get_reflection_answers', lambda *a: pytest.fail('두 시점을 따로 읽으면 안 된다'))
    service.get_comparison(context_id)
    assert len(calls) == 1


def test_comparison_ignores_answers_of_retired_ids_without_deleting_them(ctx):
    db, context_id, service = ctx
    with closing(get_connection(db)) as conn, conn:
        conn.execute("INSERT INTO reflection_answers(context_id, phase, template_id, text, status, created_at, updated_at)"
                     " VALUES (?, 'after', 'after.old_id', '예전 질문 답변', 'completed', 't', 't')", (context_id,))
    comparison = service.get_comparison(context_id)
    shown = [i.answer.text for g in comparison.groups for i in g.after if i.answer]
    assert shown == [] and comparison.after.written == 0
    with closing(get_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reflection_answers WHERE template_id='after.old_id'").fetchone()[0] == 1


def test_comparison_is_per_context_and_unknown_context_is_not_found(ctx):
    db, context_id, service = ctx
    service.save_answer(context_id, PHASE_BEFORE, 'before.expectation', '기대', 'completed')
    with pytest.raises(ResourceNotFoundError):
        service.get_comparison('missing')
    from dataclasses import replace
    other = replace(repo.get_learning_context(db, context_id), context_id='ctx-other')
    repo.create_learning_context(db, other)
    isolated = service.get_comparison('ctx-other')
    assert all(g.before.answer is None and all(i.answer is None for i in g.after) for g in isolated.groups)
    assert (isolated.before.written, isolated.after.written) == (0, 0)


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
