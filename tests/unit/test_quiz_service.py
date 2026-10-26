"""퀴즈 생성 연결(M1-09, #54): 가짜 생성기와 OpenAI 응답 대체로 정답·근거 검증과 저장을 확인한다."""
import json
from contextlib import closing
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError
from solo_leveling.application.quiz.service import DraftQuestion, NoSupportedQuiz, QuizService
from solo_leveling.domain.evidence_qa import EvidenceInput
from solo_leveling.domain.quiz import QuizValidationError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.generation.openai_generator import (
    OpenAIGenerationError, OpenAIGenerationSettings, OpenAIQuizGenerator,
)
from tests.unit.test_learning_persistence import setup_db, publish


class FakeGenerator:
    def __init__(self, make):
        self.make, self.calls = make, []

    def generate_quiz(self, step, evidence):
        self.calls.append((step, [e.evidence_id for e in evidence]))
        return self.make(evidence)


def good_mc(ev, **kw):
    base = dict(question_type='multiple_choice', prompt=f'객관식 {ev.evidence_id}', evidence_ids=(ev.evidence_id,),
                choices=('가', '나', '다'), correct_index=2)
    base.update(kw)
    return DraftQuestion(**base)


def good_short(ev, **kw):
    base = dict(question_type='short_answer', prompt=f'단답 {ev.evidence_id}', evidence_ids=(ev.evidence_id,),
                answer='정답', grading_criteria='정답이라고 쓰면 인정')
    base.update(kw)
    return DraftQuestion(**base)


def count(db, table):
    with closing(get_connection(db)) as conn:
        return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


@pytest.fixture
def ctx(setup_db):
    db, chunks = setup_db
    return db, publish(db, chunks).context_id


@pytest.fixture
def sectioned(setup_db):
    """c1은 abstract 섹션, c2는 results 섹션."""
    db, chunks = setup_db
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET section_id='01-abstract' WHERE chunk_id='c1'")
        conn.execute("UPDATE chunks SET section_id='02-results' WHERE chunk_id='c2'")
    return db, publish(db, chunks).context_id


def test_generate_saves_questions_with_answers_and_only_their_evidence(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_mc(ev[0]), good_short(ev[1])])
    result = QuizService(db, gen).generate(cid, 'preview')
    assert result.dropped_count == 0
    first, second = result.quiz.questions
    assert (first.answer, first.correct_index) == ('다', 2)
    assert (second.answer, second.grading_criteria) == ('정답', '정답이라고 쓰면 인정')
    detail = repo.get_quiz_evidence(db, result.quiz).evidence
    assert [(d.quote_original, d.pdf_page) for d in detail] == [('First.', 1), ('Second.', 2)]
    assert repo.get_quiz(db, cid, 'preview').quiz_id == result.quiz.quiz_id


def test_invalid_questions_are_dropped(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [
        good_mc(ev[0]),
        good_short(ev[0], answer=''),                          # 정답 없음
        good_short(ev[0], grading_criteria=' '),               # 채점 기준 없음
        good_mc(ev[0], correct_index=None),                    # 객관식 정답 없음
        good_mc(ev[0], answer='가'),                           # 정답 문구가 선택지 정답과 어긋남
        good_mc(ev[0], evidence_ids=()),                       # 근거 없음
        good_short(ev[0], evidence_ids=('invented',)),         # 넘기지 않은 근거
        DraftQuestion('essay', '유형 오류', (ev[0].evidence_id,)),
        DraftQuestion('multiple_choice', '타입 오류', (ev[0].evidence_id,), 5, 0),  # choices가 정수
    ])
    result = QuizService(db, gen).generate(cid, 'preview')
    assert len(result.quiz.questions) == 1
    assert result.dropped_count == 8
    assert count(db, 'evidences') == 1  # 쓰이지 않은 청크의 근거는 저장하지 않는다


def test_matching_answer_text_is_accepted(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_mc(ev[0], answer='다')])
    assert len(QuizService(db, gen).generate(cid, 'preview').quiz.questions) == 1


def test_nothing_supported_saves_nothing(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_short(ev[0], evidence_ids=('invented',))])
    with pytest.raises(NoSupportedQuiz):
        QuizService(db, gen).generate(cid, 'preview')
    assert (count(db, 'quizzes'), count(db, 'quiz_questions'), count(db, 'evidences')) == (0, 0, 0)


def test_empty_generation_saves_nothing(ctx):
    db, cid = ctx
    with pytest.raises(NoSupportedQuiz):
        QuizService(db, FakeGenerator(lambda ev: [])).generate(cid, 'preview')
    assert count(db, 'quizzes') == 0


def test_generator_failure_propagates_without_saving(ctx):
    db, cid = ctx
    def boom(ev):
        raise GenerationUnavailable('down')
    with pytest.raises(GenerationUnavailable):
        QuizService(db, FakeGenerator(boom)).generate(cid, 'preview')
    assert count(db, 'quizzes') == 0


def test_duplicate_prompts_are_dropped_and_total_is_capped(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_short(ev[0], prompt='같은  질문'), good_short(ev[0], prompt='같은 질문'),
                                    good_short(ev[1], prompt='다른 질문 1'), good_short(ev[1], prompt='다른 질문 2')])
    result = QuizService(db, gen, max_questions=2).generate(cid, 'preview')
    assert [q.prompt for q in result.quiz.questions] == ['같은  질문', '다른 질문 1']
    assert result.dropped_count == 1


def test_stops_calling_generator_once_enough_questions(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_short(ev[0], prompt=f'질문 {len(gen.calls)}')])
    result = QuizService(db, gen, max_chunks_per_call=1, max_questions=1).generate(cid, 'review')
    assert len(gen.calls) == 1 and len(result.quiz.questions) == 1


def test_calls_are_split_by_max_chunks(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_short(ev[0], prompt=f'질문 {len(gen.calls)}')])
    result = QuizService(db, gen, max_chunks_per_call=1).generate(cid, 'review')
    assert [len(ids) for _, ids in gen.calls] == [1, 1]
    assert len(result.quiz.questions) == 2


def test_steps_use_the_sections_the_reading_guide_assigns(sectioned):
    db, cid = sectioned
    gen = FakeGenerator(lambda ev: [good_short(ev[0])])
    svc = QuizService(db, gen)
    svc.generate(cid, 'preview')      # abstract
    svc.generate(cid, 'deep_read')    # abstract 제외
    svc.generate(cid, 'review')       # results
    svc.generate(cid, 'structure')    # 전체
    assert [len(ids) for _, ids in gen.calls] == [1, 1, 1, 1, 1]  # structure는 섹션별로 두 번 호출
    quotes = {}
    for step in ('preview', 'deep_read', 'review'):
        quiz = repo.get_quiz(db, cid, step)
        quotes[step] = repo.get_quiz_evidence(db, quiz).evidence[0].quote_original
    assert quotes == {'preview': 'First.', 'deep_read': 'Second.', 'review': 'Second.'}
    assert repo.list_quiz_steps(db, cid) == ['preview', 'structure', 'deep_read', 'review']


def test_papers_without_sections_use_page_ranges(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_short(ev[0])])
    QuizService(db, gen).generate(cid, 'deep_read')
    assert gen.calls[0][0] == 'deep_read' and len(gen.calls[0][1]) == 2


def test_regenerating_replaces_the_step_quiz(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [good_short(ev[0], prompt=f'질문 {len(gen.calls)}')])
    svc = QuizService(db, gen)
    svc.generate(cid, 'preview')
    second = svc.generate(cid, 'preview')
    assert repo.get_quiz(db, cid, 'preview').quiz_id == second.quiz.quiz_id
    assert count(db, 'quizzes') == 1


def test_unknown_step_and_context(ctx):
    db, cid = ctx
    svc = QuizService(db, FakeGenerator(lambda ev: []))
    with pytest.raises(QuizValidationError):
        svc.generate(cid, 'bogus')
    with pytest.raises(ResourceNotFoundError):
        svc.generate('missing', 'preview')


def test_invalid_limits():
    with pytest.raises(ValueError):
        QuizService('db', FakeGenerator(lambda ev: []), max_questions=0)
    with pytest.raises(ValueError):
        QuizService('db', FakeGenerator(lambda ev: []), max_chunks_per_call=0)


# ── OpenAI 어댑터 ──────────────────────────────────────────────
def reply(body):
    response = Mock(status_code=200)
    response.json.return_value = {'status': 'completed', 'output': [{
        'type': 'message', 'role': 'assistant', 'status': 'completed',
        'content': [{'type': 'output_text', 'text': body if isinstance(body, str) else json.dumps(body)}]}]}
    return response


def settings(**kw):
    return OpenAIGenerationSettings('test-key', allow_external_api=True, **kw)


def test_openai_quiz_end_to_end_with_stubbed_api(ctx, monkeypatch):
    db, cid = ctx
    sent = {}

    def post(url, **kwargs):
        sent.update(kwargs)
        ev = json.loads(kwargs['json']['input'][0]['content'])['evidence']
        return reply({'questions': [
            {'question_type': 'multiple_choice', 'prompt': '첫 문장은?', 'choices': ['A', 'B', 'C'],
             'correct_index': 0, 'answer': '', 'grading_criteria': '', 'evidence_ids': [ev[0]['evidence_id']]},
            {'question_type': 'short_answer', 'prompt': '둘째 문장은?', 'choices': [], 'correct_index': None,
             'answer': '둘째', 'grading_criteria': '둘째라고 쓰면 정답', 'evidence_ids': [ev[1]['evidence_id']]},
            {'question_type': 'short_answer', 'prompt': '근거 위조', 'choices': [], 'correct_index': None,
             'answer': 'x', 'grading_criteria': 'x', 'evidence_ids': ['invented']}]})
    monkeypatch.setattr(requests, 'post', post)
    result = QuizService(db, OpenAIQuizGenerator(settings())).generate(cid, 'preview')
    assert [q.answer for q in result.quiz.questions] == ['A', '둘째'] and result.dropped_count == 1
    payload = sent['json']
    assert payload['store'] is False and payload['text']['format']['name'] == 'paper_quiz'
    body = json.loads(payload['input'][0]['content'])
    assert set(body) == {'step', 'step_focus', 'evidence'} and body['step'] == 'preview'
    assert 'chunk_id' not in body['evidence'][0]


@pytest.mark.parametrize('body', ['not json', '{"questions": 3}', '{"questions": [{"prompt": "x"}]}', '[]'])
def test_openai_quiz_rejects_malformed_output(monkeypatch, body):
    monkeypatch.setattr(requests, 'post', lambda *a, **k: reply(body))
    ev = [EvidenceInput('e1', 'c1', '본문', 'text', None, 1)]
    with pytest.raises(GenerationFormatError):
        OpenAIQuizGenerator(settings()).generate_quiz('preview', ev)


def test_openai_quiz_respects_external_api_flag_step_and_http_errors(monkeypatch):
    ev = [EvidenceInput('e1', 'c1', '본문', 'text', None, 1)]
    with pytest.raises(GenerationUnavailable):
        OpenAIQuizGenerator(OpenAIGenerationSettings('k')).generate_quiz('preview', ev)
    with pytest.raises(ValueError):
        OpenAIQuizGenerator(settings()).generate_quiz('bogus', ev)
    monkeypatch.setattr(requests, 'post', lambda *a, **k: Mock(status_code=429))
    with pytest.raises(OpenAIGenerationError):
        OpenAIQuizGenerator(settings()).generate_quiz('preview', ev)
