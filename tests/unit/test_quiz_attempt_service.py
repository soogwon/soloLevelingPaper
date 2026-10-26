"""퀴즈 제출·채점·시도 조회(M1-10, #55): 채점 규칙, 점수 집계, 오답 복습을 실제 SQLite로 검증한다."""
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.quiz.attempts import (
    NormalizedAnswerGrader, QuizAttemptService, normalize_answer,
)
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.quiz import QuizQuestion
from solo_leveling.domain.quiz_attempt import AttemptValidationError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import setup_db, publish


@pytest.fixture
def ctx(setup_db):
    """preview 단계에 객관식(정답=1) 1개, 단답형(정답 'Self Attention') 2개짜리 퀴즈를 저장해 둔다."""
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.save_evidences(db, context.context_id, [
        Evidence('e1', 'c1', '첫째.', 'First.'),
        Evidence('e2', 'c2', '둘째.', 'Second.'),
    ])
    repo.save_quiz(db, context.context_id, 'preview', [
        QuizQuestion('multiple_choice', '객관식', ('e1',), choices=('가', '나', '다'), correct_index=1),
        QuizQuestion('short_answer', '단답 하나', ('e2',), answer='Self Attention',
                     grading_criteria='Self Attention이라고 쓰면 정답'),
        QuizQuestion('short_answer', '단답 둘', ('e1', 'e2'), answer='0.5', grading_criteria='0.5'),
    ])
    return db, context.context_id


def svc(db, **kw):
    counter = {'n': 0}

    def next_id():
        counter['n'] += 1
        return f'attempt-{counter["n"]}'

    times = iter(f'2026-10-26T00:{i:02d}:00+00:00' for i in range(60))
    return QuizAttemptService(db, id_factory=next_id, clock=lambda: next(times), **kw)


ALL_CORRECT = {0: 1, 1: 'self attention', 2: '0.5'}


# ── 정규화·기본 채점기 ─────────────────────────────────────────
@pytest.mark.parametrize('submitted', ['Self Attention', ' self  attention. ', 'SELF-ATTENTION'.replace('-', ' '),
                                       'selfattention', '"Self Attention"', 'ＳＥＬＦ ＡＴＴＥＮＴＩＯＮ'])
def test_normalized_grader_accepts_equivalent_spellings(submitted):
    q = QuizQuestion('short_answer', 'q', ('e1',), answer='Self Attention', grading_criteria='c')
    assert NormalizedAnswerGrader().is_correct(q, submitted)


@pytest.mark.parametrize('submitted', ['Self-Attention', 'attention', 'self attention layer', '', '05'])
def test_normalized_grader_rejects_different_answers(submitted):
    q = QuizQuestion('short_answer', 'q', ('e1',), answer='Self Attention', grading_criteria='c')
    assert not NormalizedAnswerGrader().is_correct(q, submitted)


def test_normalize_keeps_inner_punctuation():
    assert normalize_answer('0.5') != normalize_answer('05')
    assert normalize_answer('  자기 어텐션. ') == normalize_answer('자기어텐션')


# ── 제출·채점 ──────────────────────────────────────────────
def test_all_correct_attempt_is_saved_with_full_score(ctx):
    db, cid = ctx
    attempt = svc(db).submit(cid, 'preview', ALL_CORRECT)
    assert (attempt.correct_count, attempt.total_count, attempt.score) == (3, 3, 1.0)
    assert [r.is_correct for r in attempt.results] == [True, True, True]
    saved = repo.get_quiz_attempt(db, attempt.attempt_id)
    assert saved.score == 1.0 and saved.quiz_id == repo.get_quiz(db, cid, 'preview').quiz_id


def test_per_question_results_show_what_was_submitted_and_expected(ctx):
    db, cid = ctx
    attempt = svc(db).submit(cid, 'preview', {0: 2, 1: 'cross attention', 2: '0.5'})
    mc, short, ok = attempt.results
    assert (mc.selected_index, mc.correct_answer, mc.is_correct) == (2, '나', False)
    assert (short.submitted_text, short.correct_answer, short.grading_criteria, short.is_correct) == (
        'cross attention', 'Self Attention', 'Self Attention이라고 쓰면 정답', False)
    assert ok.is_correct and attempt.correct_count == 1


def test_unanswered_questions_count_as_wrong(ctx):
    db, cid = ctx
    attempt = svc(db).submit(cid, 'preview', {0: 1})
    assert [r.is_correct for r in attempt.results] == [True, False, False]
    assert [r.answered for r in attempt.results] == [True, False, False]
    assert attempt.score == pytest.approx(1 / 3)


def test_blank_short_answer_is_wrong_even_with_a_permissive_grader(ctx):
    db, cid = ctx

    class AlwaysYes:
        def is_correct(self, question, submitted):
            return True
    attempt = svc(db, grader=AlwaysYes()).submit(cid, 'preview', {0: 0, 1: '   ', 2: '아무거나'})
    assert [r.is_correct for r in attempt.results] == [False, False, True]  # 빈 답안은 채점기를 거치지 않는다


def test_custom_grader_is_used_for_short_answers_only(ctx):
    db, cid = ctx
    calls = []

    class Recording:
        def is_correct(self, question, submitted):
            calls.append((question.prompt, submitted))
            return True
    attempt = svc(db, grader=Recording()).submit(cid, 'preview', {0: 0, 1: '문장형 답', 2: '또 다른 답'})
    assert calls == [('단답 하나', '문장형 답'), ('단답 둘', '또 다른 답')]
    assert [r.is_correct for r in attempt.results] == [False, True, True]


@pytest.mark.parametrize('answers', [
    {}, None, [1, 2], {5: 1}, {-1: 1}, {True: 1}, {'0': 1},
    {0: 3}, {0: -1}, {0: True}, {0: '1'}, {1: 5}, {1: None},
])
def test_invalid_submissions_are_rejected_and_nothing_is_saved(ctx, answers):
    db, cid = ctx
    with pytest.raises(AttemptValidationError):
        svc(db).submit(cid, 'preview', answers)
    assert repo.list_quiz_attempts(db, cid) == []


def test_missing_quiz_step_and_context(ctx):
    db, cid = ctx
    with pytest.raises(ResourceNotFoundError):
        svc(db).submit(cid, 'review', {0: 0})      # 이 단계는 퀴즈가 없다
    with pytest.raises(AttemptValidationError):
        svc(db).submit(cid, 'bogus', {0: 0})
    with pytest.raises(ResourceNotFoundError):
        svc(db).submit('missing', 'preview', {0: 0})


def test_grader_failure_saves_nothing(ctx):
    db, cid = ctx

    class Broken:
        def is_correct(self, question, submitted):
            raise RuntimeError('grader down')
    with pytest.raises(RuntimeError):
        svc(db, grader=Broken()).submit(cid, 'preview', {1: '답'})
    assert repo.list_quiz_attempts(db, cid) == []


# ── 이력·단계별 점수 ───────────────────────────────────────
def test_history_lists_attempts_in_order_and_filters_by_step(ctx):
    db, cid = ctx
    s = svc(db)
    first = s.submit(cid, 'preview', {0: 0})
    second = s.submit(cid, 'preview', ALL_CORRECT)
    assert [a.attempt_id for a in s.history(cid)] == [first.attempt_id, second.attempt_id]
    assert s.history(cid, 'review') == ()
    assert s.get_attempt(first.attempt_id).correct_count == 0
    with pytest.raises(ResourceNotFoundError):
        s.get_attempt('missing')
    with pytest.raises(ResourceNotFoundError):
        s.history('missing')


def test_step_scores_report_latest_and_best_per_step(ctx):
    db, cid = ctx
    s = svc(db)
    s.submit(cid, 'preview', ALL_CORRECT)       # 3/3
    latest = s.submit(cid, 'preview', {0: 1})   # 1/3, 가장 최근
    scores = {x.step: x for x in s.step_scores(cid)}
    assert list(scores) == ['preview', 'structure', 'deep_read', 'review']
    preview = scores['preview']
    assert (preview.attempt_count, preview.latest_attempt_id, preview.latest_correct, preview.latest_total) == (
        2, latest.attempt_id, 1, 3)
    assert preview.latest_score == pytest.approx(1 / 3) and preview.best_score == 1.0
    assert scores['review'].attempt_count == 0 and scores['review'].best_score is None


def test_history_survives_quiz_regeneration(ctx):
    db, cid = ctx
    attempt = svc(db).submit(cid, 'preview', {0: 0, 1: 'x', 2: 'y'})
    repo.save_quiz(db, cid, 'preview', [QuizQuestion('short_answer', '새 질문', ('e2',), answer='새',
                                                    grading_criteria='새')])
    old = svc(db).get_attempt(attempt.attempt_id)
    assert old.total_count == 3 and [r.prompt for r in old.results][0] == '객관식'
    assert svc(db).review(attempt.attempt_id)  # 오답 복습도 그대로 된다


# ── 오답 복습 ───────────────────────────────────────────────
def test_review_returns_only_wrong_questions_with_original_text(ctx):
    db, cid = ctx
    attempt = svc(db).submit(cid, 'preview', {0: 1, 1: 'cross attention', 2: '0.5'})
    items = svc(db).review(attempt.attempt_id)
    assert [i.result.prompt for i in items] == ['단답 하나']
    item = items[0]
    assert item.result.correct_answer == 'Self Attention'
    assert [(d.quote_original, d.pdf_page) for d in item.evidence] == [('Second.', 2)]


def test_review_handles_duplicate_evidence_ids_and_all_correct(ctx):
    db, cid = ctx
    s = svc(db)
    wrong = s.submit(cid, 'preview', {2: 'x'})  # '단답 둘'은 근거 e1, e2를 인용한다
    assert [d.quote_original for d in s.review(wrong.attempt_id)[2].evidence] == ['First.', 'Second.']
    perfect = s.submit(cid, 'preview', ALL_CORRECT)
    assert s.review(perfect.attempt_id) == ()
    assert repo.get_attempt_wrong_evidence(db, perfect).evidence == ()
    with pytest.raises(ResourceNotFoundError):
        s.review('missing')


def test_attempts_do_not_modify_the_quiz(ctx):
    db, cid = ctx
    before = repo.get_quiz(db, cid, 'preview')
    svc(db).submit(cid, 'preview', ALL_CORRECT)
    assert repo.get_quiz(db, cid, 'preview') == before
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM quiz_questions').fetchone()[0] == 3
