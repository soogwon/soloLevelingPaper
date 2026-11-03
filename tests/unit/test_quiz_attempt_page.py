"""시도 목록 페이지 조회(#145): 정렬·동률·커서·격리·요약만 읽기를 실제 SQLite로 검증한다."""
from contextlib import closing

import pytest

from solo_leveling.application.quiz.attempts import QuizAttemptService
from solo_leveling.domain.models import Evidence, LearningContext
from solo_leveling.domain.quiz_attempt import AnswerResult, AttemptValidationError, QuizAttempt
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import publish, setup_db  # noqa: F401  (fixture)

T0 = '2026-11-01T00:00:00+00:00'


def attempt(attempt_id, step='preview', submitted_at=T0, context_id='ctx', correct=1):
    evidence = ('e1',) if context_id == 'ctx' else ('e2',)
    results = tuple(AnswerResult(i, 'multiple_choice', f'문항 {i}', ('가', '나'), 0, None, '가', '', i < correct, evidence)
                    for i in range(2))
    return QuizAttempt(attempt_id, context_id, 'quiz1', step, results, submitted_at)


def save(db, *attempts):
    for a in attempts:
        repo.save_quiz_attempt(db, a)


def ids(page):
    return [a.attempt_id for a in page.items]


def walk(db, context_id='ctx', step=None, limit=2):
    seen, cursor = [], None
    while True:
        page = repo.list_quiz_attempt_page(db, context_id, step, limit, cursor)
        seen += ids(page)
        if page.next_cursor is None:
            return seen
        cursor = page.next_cursor


@pytest.fixture
def env(setup_db):  # noqa: F811
    db, chunks = setup_db
    publish(db, chunks)
    repo.create_learning_context(db, LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=[],
                                                     embedding_set_id='idx'))
    repo.save_evidences(db, 'ctx', [Evidence('e1', 'c1', '첫째.', 'First.')])
    repo.save_evidences(db, 'ctx2', [Evidence('e2', 'c1', '첫째.', 'First.')])
    return db


def test_empty_history_is_an_empty_last_page(env):
    page = repo.list_quiz_attempt_page(env, 'ctx')
    assert page.items == () and page.next_cursor is None


def test_newest_first_and_same_time_prefers_the_later_saved_not_attempt_id_order(env):
    save(env, attempt('m', submitted_at='2026-11-01T00:00:01+00:00'),
         attempt('z'), attempt('a'), attempt('k'))          # z, a, k는 같은 시각. 저장 순서는 z → a → k
    assert ids(repo.list_quiz_attempt_page(env, 'ctx')) == ['m', 'k', 'a', 'z']


def test_ties_across_the_page_boundary_are_neither_dropped_nor_repeated(env):
    save(env, *[attempt(f'a{i}') for i in range(7)])        # 모두 같은 제출 시각
    assert walk(env, limit=2) == [f'a{i}' for i in reversed(range(7))]
    assert walk(env, limit=3) == [f'a{i}' for i in reversed(range(7))]


def test_next_cursor_is_null_exactly_on_the_last_page(env):
    save(env, *[attempt(f'a{i}', submitted_at=f'2026-11-01T00:00:0{i}+00:00') for i in range(4)])
    assert repo.list_quiz_attempt_page(env, 'ctx', None, 4).next_cursor is None      # 딱 limit개
    page = repo.list_quiz_attempt_page(env, 'ctx', None, 3)
    assert page.next_cursor == page.items[-1].attempt_id == 'a1'
    assert repo.list_quiz_attempt_page(env, 'ctx', None, 3, page.next_cursor).next_cursor is None


def test_new_submissions_between_pages_do_not_disturb_the_rest(env):
    save(env, *[attempt(f'a{i}') for i in range(5)])
    first = repo.list_quiz_attempt_page(env, 'ctx', None, 2)
    assert ids(first) == ['a4', 'a3']
    save(env, attempt('same-time-new'), attempt('newer', submitted_at='2026-11-02T00:00:00+00:00'))
    rest = repo.list_quiz_attempt_page(env, 'ctx', None, 10, first.next_cursor)
    assert ids(rest) == ['a2', 'a1', 'a0']                  # 새 제출은 커서 앞쪽에 놓이므로 뒤쪽은 그대로다


def test_context_and_step_are_isolated(env):
    save(env, attempt('p1'), attempt('r1', step='review'), attempt('x1', context_id='ctx2'))
    assert walk(env, 'ctx') == ['r1', 'p1']                  # 같은 시각이면 나중에 저장한 시도부터
    assert ids(repo.list_quiz_attempt_page(env, 'ctx', 'review')) == ['r1']
    assert ids(repo.list_quiz_attempt_page(env, 'ctx2')) == ['x1']
    assert repo.list_quiz_attempt_page(env, 'ctx', 'preview').items[0].step == 'preview'


@pytest.mark.parametrize('context,step,cursor', [
    ('ctx', None, 'nope'),          # 없는 커서
    ('ctx', 'review', 'p1'),        # 다른 단계의 커서
    ('ctx2', None, 'p1'),           # 다른 맥락의 커서
    ('ctx', None, ''),
])
def test_unknown_or_out_of_scope_cursor_is_rejected(env, context, step, cursor):
    save(env, attempt('p1'), attempt('r1', step='review'))
    with pytest.raises(AttemptValidationError):
        repo.list_quiz_attempt_page(env, context, step, 2, cursor)


@pytest.mark.parametrize('limit', [0, -1, 101, True, '5', 2.5])
def test_invalid_limit_or_step_is_rejected(env, limit):
    with pytest.raises(AttemptValidationError):
        repo.list_quiz_attempt_page(env, 'ctx', None, limit)
    with pytest.raises(AttemptValidationError):
        repo.list_quiz_attempt_page(env, 'ctx', 'nope')


def test_first_item_matches_the_latest_attempt_of_each_step_in_scores(env):
    save(env, attempt('p1'), attempt('p2', submitted_at='2026-11-01T00:00:05+00:00'), attempt('p3', submitted_at='2026-11-01T00:00:05+00:00'),
         attempt('r1', step='review'))
    scores = {s.step: s for s in QuizAttemptService(env).step_scores('ctx')}
    for step in ('preview', 'review'):
        assert repo.list_quiz_attempt_page(env, 'ctx', step).items[0].attempt_id == scores[step].latest_attempt_id


def test_summary_fields_come_from_the_attempt_row(env):
    save(env, attempt('a1', correct=1))
    item = repo.list_quiz_attempt_page(env, 'ctx').items[0]
    assert (item.correct_count, item.total_count, item.score, item.quiz_id) == (1, 2, 0.5, 'quiz1')
    assert not hasattr(item, 'results')


def test_page_does_not_read_answers(env):
    save(env, *[attempt(f'a{i}') for i in range(5)])
    with closing(get_connection(env)) as conn, conn:        # 답안 테이블이 없어도 요약 목록은 읽혀야 한다
        conn.execute('DROP TABLE quiz_attempt_answers')
    assert len(repo.list_quiz_attempt_page(env, 'ctx', None, 3).items) == 3
    with pytest.raises(Exception):
        repo.list_quiz_attempts(env, 'ctx')                  # 기존 전체 조회는 답안이 필요해 실패한다(대비)


def test_large_history_reads_only_one_page_using_an_index(env):
    save(env, *[attempt(f'a{i:03d}', submitted_at=f'2026-11-01T00:{i // 60:02d}:{i % 60:02d}+00:00') for i in range(300)])
    page = repo.list_quiz_attempt_page(env, 'ctx', None, 20)
    assert len(page.items) == 20 and page.next_cursor == 'a280' and page.items[0].attempt_id == 'a299'
    with closing(get_connection(env)) as conn:
        for step_clause, params in (('', ['ctx']), (' AND step=?', ['ctx', 'preview'])):
            plan = ' '.join(r[3] for r in conn.execute(
                'EXPLAIN QUERY PLAN SELECT attempt_id FROM quiz_attempts WHERE context_id=?' + step_clause
                + ' ORDER BY submitted_at DESC, rowid DESC LIMIT 21', params))
            assert 'idx_quiz_attempts_context' in plan and 'TEMP B-TREE' not in plan
    assert walk(env, limit=100) == [f'a{i:03d}' for i in reversed(range(300))]
