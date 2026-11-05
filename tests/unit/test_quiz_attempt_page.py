"""시도 목록 페이지 조회(#145): 정렬·동률·커서·격리·요약만 읽기를 실제 SQLite로 검증한다."""
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import DataIntegrityError
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


def test_step_scores_do_not_read_answers_and_keep_empty_steps(env):
    save(env, attempt('p1', correct=2),
         attempt('p2', correct=1, submitted_at='2026-11-01T00:00:05+00:00'),
         attempt('r1', step='review', correct=0))
    with closing(get_connection(env)) as conn, conn:
        conn.execute('DROP TABLE quiz_attempt_answers')
    scores = {score.step: score for score in QuizAttemptService(env).step_scores('ctx')}
    assert (scores['preview'].attempt_count, scores['preview'].latest_attempt_id,
            scores['preview'].latest_score, scores['preview'].best_score) == (2, 'p2', 0.5, 1.0)
    assert scores['review'].attempt_count == 1 and scores['review'].latest_attempt_id == 'r1'
    assert scores['structure'].attempt_count == 0 and scores['deep_read'].best_score is None


def test_step_scores_same_time_latest_is_the_later_saved_attempt(env):
    save(env, attempt('z', correct=2), attempt('a', correct=0))
    preview = {score.step: score for score in QuizAttemptService(env).step_scores('ctx')}['preview']
    assert preview.latest_attempt_id == 'a' and preview.latest_score == 0.0 and preview.best_score == 1.0


def test_step_score_summary_matches_the_previous_full_attempt_aggregation(env):
    save(env, attempt('p1', correct=2),
         attempt('r1', step='review', correct=0),
         attempt('p2', correct=1, submitted_at='2026-11-01T00:00:05+00:00'))
    attempts = repo.list_quiz_attempts(env, 'ctx')
    expected = {}
    for step in ('preview', 'review'):
        same_step = [item for item in attempts if item.step == step]
        latest = same_step[-1]
        expected[step] = (len(same_step), latest.attempt_id, latest.correct_count,
                          latest.total_count, latest.score, max(item.score for item in same_step))
    actual = {score.step: score for score in QuizAttemptService(env).step_scores('ctx')}
    for step, values in expected.items():
        score = actual[step]
        assert (score.attempt_count, score.latest_attempt_id, score.latest_correct,
                score.latest_total, score.latest_score, score.best_score) == values


def test_step_scores_are_context_isolated(env):
    save(env, attempt('ctx-attempt', correct=2), attempt('other-attempt', context_id='ctx2', correct=0))
    first = {score.step: score for score in QuizAttemptService(env).step_scores('ctx')}['preview']
    other = {score.step: score for score in QuizAttemptService(env).step_scores('ctx2')}['preview']
    assert (first.attempt_count, first.latest_attempt_id) == (1, 'ctx-attempt')
    assert (other.attempt_count, other.latest_attempt_id) == (1, 'other-attempt')


@pytest.mark.parametrize('step,correct,total', [
    ('unknown', 1, 2),
    ('preview', -1, 2),
    ('preview', 3, 2),
    ('preview', 0, 0),
])
def test_step_scores_reject_corrupt_summary_rows(env, step, correct, total):
    with closing(get_connection(env)) as conn, conn:
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.execute('INSERT INTO quiz_attempts VALUES (?, ?, ?, ?, ?, ?, ?)',
                     ('broken', 'ctx', 'quiz1', step, correct, total, T0))
    with pytest.raises(DataIntegrityError):
        QuizAttemptService(env).step_scores('ctx')


def test_step_scores_large_history_uses_two_summary_queries_without_answers(env, monkeypatch):
    bulk_insert(env, 10_000, per_second=4)
    with closing(get_connection(env)) as conn, conn:
        conn.execute('DROP TABLE quiz_attempt_answers')
    statements = []
    real_get_connection = repo.get_connection

    class TracedConnection:
        def __init__(self, path):
            self.connection = real_get_connection(path)

        def execute(self, sql, params=()):
            statements.append(' '.join(sql.split()))
            return self.connection.execute(sql, params)

        def close(self):
            self.connection.close()

    monkeypatch.setattr(repo, 'get_connection', TracedConnection)
    scores = {score.step: score for score in QuizAttemptService(env).step_scores('ctx')}
    assert scores['preview'].attempt_count == 5000
    assert scores['review'].attempt_count == 5000
    assert scores['preview'].latest_attempt_id == 'b09998'
    assert scores['review'].latest_attempt_id == 'b09999'
    assert scores['preview'].latest_score == scores['review'].latest_score == 0.5
    assert len(statements) == 2
    assert all('quiz_attempt_answers' not in sql for sql in statements)
    assert any('COUNT(*) OVER' in sql and 'MAX(' in sql for sql in statements)


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


# ── C 리뷰 반영: 커서가 있는 깊은 페이지도 인덱스 범위로 시작해야 한다 ─────────────────────────────
def bulk_insert(db, total, per_second=1, steps=('preview', 'review')):
    """요약 행만 대량으로 넣는다(답안은 쓰지 않는 조회이므로 외래 키를 끄고 빠르게 만든다)."""
    with closing(get_connection(db)) as conn, conn:
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.executemany(
            'INSERT INTO quiz_attempts VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(f'b{i:05d}', 'ctx', 'quiz1', steps[i % len(steps)], 1, 2,
              f'2026-11-01T{(i // per_second) // 3600:02d}:{((i // per_second) % 3600) // 60:02d}:{(i // per_second) % 60:02d}+00:00')
             for i in range(total)])


def page_work(db, step, cursor, limit=20):
    """실제 조회 SQL(quiz_attempt_page_query)의 실행 계획과 SQLite 실행 명령 수를 잰다."""
    with closing(get_connection(db)) as conn:
        anchor = None
        if cursor is not None:
            anchor = conn.execute('SELECT submitted_at, rowid AS pos FROM quiz_attempts WHERE attempt_id=?',
                                  (cursor,)).fetchone()
        sql, params = repo.quiz_attempt_page_query('ctx', step, limit, anchor)
        plan = ' | '.join(r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + sql, params))
        count = [0]
        conn.set_progress_handler(lambda: count.__setitem__(0, count[0] + 1), 1)
        rows = conn.execute(sql, params).fetchall()
        conn.set_progress_handler(None, 0)
    return plan, count[0], len(rows)


@pytest.mark.parametrize('step', [None, 'review'])
def test_cursor_page_starts_at_the_cursor_in_the_index(env, step):
    bulk_insert(env, 5000)
    early, deep = 'b04990', 'b00010'        # 최신순이므로 b04990이 앞쪽, b00010이 뒤쪽 커서
    plan_early, work_early, n_early = page_work(env, step, early)
    plan_deep, work_deep, n_deep = page_work(env, step, deep)
    for plan in (plan_early, plan_deep):
        assert 'submitted_at<' in plan and 'TEMP B-TREE' not in plan, plan   # 커서 위치가 탐색 범위에 들어간다
    assert n_early == 21 and n_deep >= 1
    # 뒤쪽 커서라고 앞쪽 행을 훑지 않는다: 작업량은 위치와 거의 무관하다(수정 전에는 수백 배였다).
    assert work_deep < work_early * 3 and work_deep < 2000, (work_early, work_deep)


def test_deep_pages_have_no_gap_or_duplicate_against_a_full_listing(env):
    bulk_insert(env, 1200, per_second=1)
    expected = [f'b{i:05d}' for i in reversed(range(1200))]
    assert walk(env, limit=100) == expected
    assert walk(env, step='review', limit=37) == [a for a in expected if int(a[1:]) % 2 == 1]
    assert walk(env, step='preview', limit=1)[:5] == [a for a in expected if int(a[1:]) % 2 == 0][:5]


@pytest.mark.parametrize('limit', [1, 2, 3, 5, 7])
def test_same_timestamp_boundary_keeps_saved_order_without_gap_or_duplicate(env, limit):
    # 한 시각에 여러 건(per_second=6)이 몰리고, 페이지 경계가 그 한가운데를 지나도 누락·중복이 없어야 한다.
    bulk_insert(env, 60, per_second=6)
    assert walk(env, limit=limit) == [f'b{i:05d}' for i in reversed(range(60))]


def test_cursor_contract_is_unchanged_for_missing_or_out_of_scope_cursor(env):
    bulk_insert(env, 30)
    with pytest.raises(AttemptValidationError):
        repo.list_quiz_attempt_page(env, 'ctx', None, 5, 'no-such-attempt')
    with pytest.raises(AttemptValidationError):
        repo.list_quiz_attempt_page(env, 'ctx', 'review', 5, 'b00000')       # b00000은 preview 단계
