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
                + ' ORDER BY submitted_at DESC, attempt_seq DESC LIMIT 21', params))
            assert 'idx_quiz_attempts_context' in plan and 'TEMP B-TREE' not in plan
    assert walk(env, limit=100) == [f'a{i:03d}' for i in reversed(range(300))]


# ── C 리뷰 반영: 커서가 있는 깊은 페이지도 인덱스 범위로 시작해야 한다 ─────────────────────────────
def bulk_insert(db, total, per_second=1, steps=('preview', 'review')):
    """요약 행만 대량으로 넣는다(답안은 쓰지 않는 조회이므로 외래 키를 끄고 빠르게 만든다)."""
    with closing(get_connection(db)) as conn, conn:
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.executemany(
            'INSERT INTO quiz_attempts (attempt_id, context_id, quiz_id, step, correct_count, total_count, '
            'submitted_at, attempt_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [(f'b{i:05d}', 'ctx', 'quiz1', steps[i % len(steps)], 1, 2,
              f'2026-11-01T{(i // per_second) // 3600:02d}:{((i // per_second) % 3600) // 60:02d}:{(i // per_second) % 60:02d}+00:00',
              i + 1)
             for i in range(total)])


def page_work(db, step, cursor, limit=20):
    """실제 조회 SQL(quiz_attempt_page_query)의 실행 계획과 SQLite 실행 명령 수를 잰다."""
    with closing(get_connection(db)) as conn:
        anchor = None
        if cursor is not None:
            anchor = conn.execute('SELECT submitted_at, attempt_seq AS pos FROM quiz_attempts WHERE attempt_id=?',
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
        assert '(submitted_at,attempt_seq)<' in plan and 'TEMP B-TREE' not in plan, plan   # 커서 위치가 탐색 범위에 들어간다
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


# ── #176: 같은 시각의 순서는 영속 저장 순번(attempt_seq)으로 정한다 ───────────────────────────
import sqlite3  # noqa: E402
import threading  # noqa: E402

from solo_leveling.infrastructure.database.schema import init_db  # noqa: E402


def seqs(db):
    with closing(get_connection(db)) as conn:
        return {r[0]: r[1] for r in conn.execute('SELECT attempt_id, attempt_seq FROM quiz_attempts')}


def test_each_saved_attempt_gets_the_next_persistent_sequence(env):
    save(env, attempt('a1'), attempt('a2'), attempt('a3'))
    assert seqs(env) == {'a1': 1, 'a2': 2, 'a3': 3}
    with closing(get_connection(env)) as conn, conn:        # 가운데·마지막을 지워도 순번은 이어서 커진다
        conn.execute("DELETE FROM quiz_attempt_answers WHERE attempt_id IN ('a2', 'a3')")
        conn.execute("DELETE FROM quiz_attempts WHERE attempt_id IN ('a2', 'a3')")
    save(env, attempt('a4'))
    assert seqs(env) == {'a1': 1, 'a4': 2}                  # 남은 것보다 항상 크다. 순서는 a4가 최신이다
    assert ids(repo.list_quiz_attempt_page(env, 'ctx', None, 5)) == ['a4', 'a1']


def test_order_follows_the_sequence_not_the_implicit_rowid(env):
    """암묵적 rowid가 바뀌어도(VACUUM·복원·재적재 등) 같은 시각의 순서는 저장 순번을 따른다."""
    save(env, *[attempt(f'a{i}') for i in range(1, 7)])      # 모두 같은 제출 시각
    expected = ['a6', 'a5', 'a4', 'a3', 'a2', 'a1']
    with closing(get_connection(env)) as conn, conn:         # rowid를 뒤섞는다(순번은 그대로)
        conn.execute('UPDATE quiz_attempts SET rowid = rowid + 100')
        conn.execute("UPDATE quiz_attempts SET rowid = 1 WHERE attempt_id = 'a6'")
        conn.execute("UPDATE quiz_attempts SET rowid = 2 WHERE attempt_id = 'a1'")
    assert ids(repo.list_quiz_attempt_page(env, 'ctx', None, 10)) == expected
    assert walk(env, limit=2) == expected and walk(env, step='preview', limit=4) == expected
    assert [a.attempt_id for a in repo.list_quiz_attempts(env, 'ctx')] == list(reversed(expected))


@pytest.mark.parametrize('limit', [1, 2, 3, 4])
def test_order_and_cursors_survive_delete_and_vacuum(env, limit):
    save(env, *[attempt(f'a{i:02d}') for i in range(24)])    # 같은 시각 24건
    with closing(get_connection(env)) as conn, conn:
        conn.execute("DELETE FROM quiz_attempt_answers WHERE attempt_id IN ('a03','a10','a11','a12','a20')")
        conn.execute("DELETE FROM quiz_attempts WHERE attempt_id IN ('a03','a10','a11','a12','a20')")
    expected = [f'a{i:02d}' for i in reversed(range(24)) if f'a{i:02d}' not in ('a03', 'a10', 'a11', 'a12', 'a20')]
    first = repo.list_quiz_attempt_page(env, 'ctx', None, limit)
    cursor = first.next_cursor
    before = seqs(env)
    conn = sqlite3.connect(env, isolation_level=None)
    conn.execute('VACUUM')
    conn.close()
    assert seqs(env) == before                               # 순번은 VACUUM 뒤에도 같다
    assert walk(env, limit=limit) == expected                # 누락·중복 없이 같은 순서
    rest = []
    while cursor is not None:                                # VACUUM 전에 받은 커서로도 이어 읽는다
        page = repo.list_quiz_attempt_page(env, 'ctx', None, limit, cursor)
        rest += ids(page)
        cursor = page.next_cursor
    assert ids(first) + rest == expected


def test_concurrent_saves_get_distinct_sequences(env):
    errors = []

    def work(prefix):
        try:
            for i in range(8):
                repo.save_quiz_attempt(env, attempt(f'{prefix}{i}'))
        except Exception as error:   # pragma: no cover - 실패하면 아래에서 드러난다
            errors.append(error)

    threads = [threading.Thread(target=work, args=(p,)) for p in 'wxyz']
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors
    values = sorted(seqs(env).values())
    assert values == list(range(1, 33))                      # 32건, 중복·빈틈 없음
    assert walk(env, limit=5) == [a for a, _ in sorted(seqs(env).items(), key=lambda kv: -kv[1])]


OLD_SCHEMA = """
CREATE TABLE quiz_attempts (
    attempt_id TEXT PRIMARY KEY, context_id TEXT NOT NULL, quiz_id TEXT NOT NULL, step TEXT NOT NULL,
    correct_count INTEGER NOT NULL, total_count INTEGER NOT NULL, submitted_at TEXT NOT NULL);
CREATE INDEX idx_quiz_attempts_context ON quiz_attempts(context_id, step, submitted_at);
CREATE INDEX idx_quiz_attempts_context_time ON quiz_attempts(context_id, submitted_at);
"""


def _old_db(tmp_path):
    db = str(tmp_path / 'old.db')
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executescript(OLD_SCHEMA)
        rows = [('z1', 'ctx', 'q', 'preview', 1, 2, '2026-11-01T00:00:00+00:00'),
                ('b2', 'ctx', 'q', 'preview', 1, 2, '2026-11-01T00:00:00+00:00'),   # 같은 시각, id 사전순은 반대
                ('a3', 'ctx', 'q', 'review', 2, 2, '2026-11-01T00:00:00+00:00'),
                ('m4', 'ctx', 'q', 'review', 0, 2, '2026-10-31T00:00:00+00:00')]
        conn.executemany('INSERT INTO quiz_attempts VALUES (?, ?, ?, ?, ?, ?, ?)', rows)
    return db


def test_migration_adds_the_sequence_in_the_current_visible_order(tmp_path):
    db = _old_db(tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        old_order = [r[0] for r in conn.execute(
            'SELECT attempt_id FROM quiz_attempts ORDER BY submitted_at DESC, rowid DESC')]
    init_db(db)
    assert seqs(db) == {'z1': 1, 'b2': 2, 'a3': 3, 'm4': 4}
    with closing(sqlite3.connect(db)) as conn:
        new_order = [r[0] for r in conn.execute(
            'SELECT attempt_id FROM quiz_attempts ORDER BY submitted_at DESC, attempt_seq DESC')]
        names = {r[1] for r in conn.execute("PRAGMA index_list('quiz_attempts')")}
    assert new_order == old_order                            # 이관 전과 같은 순서
    assert {'idx_quiz_attempts_seq', 'idx_quiz_attempts_context_seq', 'idx_quiz_attempts_context_time_seq'} <= names
    assert not ({'idx_quiz_attempts_context', 'idx_quiz_attempts_context_time'} & names)


def test_migration_is_repeatable_and_keeps_existing_sequences(tmp_path):
    db = _old_db(tmp_path)
    init_db(db)
    first = seqs(db)
    init_db(db)
    init_db(db)
    assert seqs(db) == first
    with closing(sqlite3.connect(db)) as conn, conn:         # 일부만 빈 상태(중단·수동 편집)도 뒤로 이어 채운다
        conn.execute("UPDATE quiz_attempts SET attempt_seq = NULL WHERE attempt_id IN ('b2', 'm4')")
    init_db(db)
    after = seqs(db)
    assert after['z1'] == 1 and after['a3'] == 3             # 있던 순번은 그대로
    assert sorted(after.values()) == [1, 3, 4, 5] and len(set(after.values())) == 4


def test_new_attempt_after_migration_is_the_newest_and_unique(tmp_path):
    db = _old_db(tmp_path)
    init_db(db)
    with closing(sqlite3.connect(db)) as conn, conn:         # 기존 행에는 FK 대상이 없으니 요약만 읽는 조회로 확인한다
        conn.execute("INSERT INTO quiz_attempts (attempt_id, context_id, quiz_id, step, correct_count, total_count, "
                     "submitted_at, attempt_seq) VALUES ('n5', 'ctx', 'q', 'preview', 1, 2, '2026-11-01T00:00:00+00:00', "
                     "(SELECT COALESCE(MAX(attempt_seq), 0) + 1 FROM quiz_attempts))")
        with pytest.raises(sqlite3.IntegrityError):          # 순번은 유일하다
            conn.execute("UPDATE quiz_attempts SET attempt_seq = 1 WHERE attempt_id = 'n5'")
    assert seqs(db)['n5'] == 5
    assert ids(repo.list_quiz_attempt_page(db, 'ctx', None, 3)) == ['n5', 'a3', 'b2']


# ── 같은 시각이 매우 많아도 깊은 페이지 비용이 앞쪽과 비슷하다(수치는 PR에 기록) ──────────────────
def _same_timestamp_bulk(db, total):
    with closing(get_connection(db)) as conn, conn:
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.executemany(
            'INSERT INTO quiz_attempts (attempt_id, context_id, quiz_id, step, correct_count, total_count, '
            'submitted_at, attempt_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [(f's{i:05d}', 'ctx', 'quiz1', ('preview', 'review')[i % 2], 1, 2, T0, i + 1) for i in range(total)])


@pytest.mark.parametrize('step', [None, 'review'])
def test_deep_page_cost_with_ten_thousand_same_timestamp_attempts(env, step):
    _same_timestamp_bulk(env, 10_000)
    plan_early, work_early, n_early = page_work(env, step, 's09990')      # 최신순이라 앞쪽
    plan_deep, work_deep, n_deep = page_work(env, step, 's00020')         # 뒤쪽
    for plan in (plan_early, plan_deep):
        assert 'idx_quiz_attempts_context' in plan and 'TEMP B-TREE' not in plan, plan
        assert '(submitted_at,attempt_seq)<' in plan, plan
    assert n_early == 21 and n_deep >= 1
    assert work_deep < work_early * 3 and work_deep < 2000, (work_early, work_deep)
    expected = [f's{i:05d}' for i in reversed(range(10_000)) if step is None or i % 2 == 1]
    assert walk(env, step=step, limit=100)[:300] == expected[:300] and len(walk(env, step=step, limit=100)) == len(expected)
