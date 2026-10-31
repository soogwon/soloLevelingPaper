"""대역으로 C 경계만 검증한다. DB 멱등성·동시성의 통과 근거가 아니다."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from solo_leveling.domain.quiz import Quiz, QuizQuestion
from solo_leveling.domain.quiz_attempt import AnswerResult, QuizAttempt
from solo_leveling.interfaces.mcp.quiz_contracts import (
    ContractError, HistoryInput, InputPolicy, QuizContractAdapter, SubmissionResult,
)
from solo_leveling.interfaces.mcp.server import MCPServices, create_server


@pytest.fixture
def prepared():
    service = Mock()
    service.get_quiz.return_value = Quiz('q1', 'ctx', 'preview', (
        QuizQuestion('short_answer', '합성 질문', ('secret-evidence',),
                     answer='secret-answer', grading_criteria='secret-criteria'),
    ), '2026-10-31T00:00:00Z')
    result = AnswerResult(0, 'short_answer', '제출 당시 질문', (), None, '답',
                          'secret-answer', 'secret-criteria', True, ('secret-evidence',))
    attempt = QuizAttempt('a1', 'ctx', 'q1', 'preview', (result,), '2026-10-31T00:00:00Z')
    service.submit.return_value = SubmissionResult(attempt, False)
    service.get_attempt.return_value = attempt
    adapter = QuizContractAdapter(service, InputPolicy(10, 100, True))
    return adapter, service, attempt


def payload(**changes):
    return dict(dict(context_id='ctx', step='preview', quiz_id='q1', request_key=' key ',
                     answers=[{'question_index': 0, 'text': '  答 😀 e\u0301  '}]), **changes)


def test_public_quiz_excludes_answers_and_evidence_in_json(prepared):
    adapter, _, _ = prepared
    result = adapter.get_quiz({'context_id': 'ctx', 'step': 'preview'})
    data = result.model_dump(mode='json')
    assert set(data['questions'][0]) == {'question_index', 'question_type', 'prompt', 'choices'}
    assert json.loads(result.model_dump_json()) == data
    assert 'secret' not in result.model_dump_json()


def test_public_multiple_choice_hides_correct_index_and_keeps_choices(prepared):
    adapter, service, _ = prepared
    service.get_quiz.return_value = Quiz('q1', 'ctx', 'preview', (
        QuizQuestion('multiple_choice', '객관식 질문', ('secret-evidence',),
                     choices=('선택지 A', '선택지 B'), correct_index=1,
                     grading_criteria='secret-criteria'),
    ), '2026-10-31T00:00:00Z')
    result = adapter.get_quiz({'context_id': 'ctx', 'step': 'preview'})
    expected = {
        'context_id': 'ctx', 'step': 'preview', 'quiz_id': 'q1',
        'grading_mode': 'normalized_exact_match',
        'questions': [{'question_index': 0, 'question_type': 'multiple_choice',
                       'prompt': '객관식 질문', 'choices': ['선택지 A', '선택지 B']}],
    }
    # 정답인 선택지의 문구는 유지하되, 정답 표시와 비공개 메타데이터는 제외한다.
    assert result.model_dump(mode='json') == expected
    assert json.loads(result.model_dump_json()) == expected
    service.get_quiz.assert_called_once_with(context_id='ctx', step='preview')


def test_get_attempt_returns_saved_snapshot_without_fetching_latest(prepared):
    adapter, service, _ = prepared
    result = adapter.get_attempt({'context_id': 'ctx', 'attempt_id': 'a1'})
    expected = {
        'context_id': 'ctx', 'step': 'preview', 'attempt_id': 'a1', 'quiz_id': 'q1',
        'submitted_at': '2026-10-31T00:00:00Z', 'correct_count': 1,
        'total_count': 1, 'score': 1.0,
        'results': [{'question_index': 0, 'question_type': 'short_answer',
                     'prompt': '제출 당시 질문', 'choices': [], 'selected_index': None,
                     'submitted_text': '답', 'correct_answer': 'secret-answer',
                     'grading_criteria': 'secret-criteria', 'is_correct': True,
                     'evidence_ids': ['secret-evidence']}],
    }
    assert result.model_dump(mode='json') == expected
    assert json.loads(result.model_dump_json()) == expected
    service.get_attempt.assert_called_once_with(context_id='ctx', attempt_id='a1')
    service.get_quiz.assert_not_called()
    service.submit.assert_not_called()


@pytest.mark.parametrize('changes', [
    {'answers': []}, {'answers': [{'question_index': True, 'text': '답'}]},
    {'answers': [{'question_index': '0', 'text': '답'}]},
    {'answers': [{'question_index': 0.0, 'text': '답'}]},
    {'answers': [{'question_index': 0, 'selected_index': False}]},
    {'answers': [{'question_index': 0, 'selected_index': None}]},
    {'answers': [{'question_index': 0, 'text': '답', 'selected_index': 1}]},
    {'answers': [{'question_index': 0, 'text': '답', 'extra': 'private'}]},
    {'answers': [{'question_index': 0, 'text': '답'}] * 2},
    {'answers': [{'question_index': 0, 'text': ' '}]},
    {'answers': [{'question_index': 0, 'text': 'x' * 101}]},
    {'answers': [{'question_index': i, 'text': '답'} for i in range(11)]},
    {'request_key': ' '}, {'request_key': 'x' * 201}, {'context_id': True},
    {'step': 'unknown'}, {'unknown': 'private'},
])
def test_invalid_inputs_never_call_service(prepared, changes):
    adapter, service, _ = prepared
    args = payload()
    args.update(changes)
    with pytest.raises(ContractError, match='^INVALID_ARGUMENT$'):
        adapter.submit(args)
    service.submit.assert_not_called()
    service.get_quiz.assert_not_called()


def test_mapping_preserves_text_and_key_and_does_not_fetch_latest(prepared):
    adapter, service, _ = prepared
    args = payload()
    args['answers'].insert(0, {'question_index': 2, 'selected_index': 1})
    result = adapter.submit(args)
    call = service.submit.call_args.kwargs
    assert call['request_key'] == ' key '
    assert list(call['answers']) == [0, 2]
    assert call['answers'][0] == args['answers'][1]['text']
    assert 1 not in call['answers']
    assert result.results[0].prompt == '제출 당시 질문'
    service.get_quiz.assert_not_called()
    service.submit.assert_called_once()


def test_reuse_is_forwarded_without_local_cache_or_regrading(prepared):
    adapter, service, attempt = prepared
    service.submit.side_effect = [SubmissionResult(attempt, False), SubmissionResult(attempt, True)]
    first, second = adapter.submit(payload()), adapter.submit(payload())
    assert first.model_dump(exclude={'reused'}) == second.model_dump(exclude={'reused'})
    assert not first.reused and second.reused
    assert service.submit.call_count == 2
    service.get_quiz.assert_not_called()


@pytest.mark.parametrize('code', ['QUIZ_CHANGED', 'REQUEST_CONFLICT', 'NOT_FOUND'])
def test_service_rejection_is_forwarded_without_retry(prepared, code):
    adapter, service, _ = prepared
    service.submit.side_effect = ContractError(code)
    with pytest.raises(ContractError, match=f'^{code}$'):
        adapter.submit(payload())
    service.submit.assert_called_once()


def test_internal_exception_does_not_expose_body(prepared):
    adapter, service, _ = prepared
    service.submit.side_effect = RuntimeError('private answer and path')
    with pytest.raises(ContractError, match='^INTERNAL_ERROR$') as error:
        adapter.submit(payload())
    assert error.value.__suppress_context__


def test_other_context_attempt_is_not_returned(prepared):
    adapter, service, attempt = prepared
    service.get_attempt.return_value = replace(attempt, context_id='other')
    with pytest.raises(ContractError, match='^NOT_FOUND$'):
        adapter.get_attempt({'context_id': 'ctx', 'attempt_id': 'a1'})


def test_wrong_submission_identity_is_rejected(prepared):
    adapter, service, attempt = prepared
    service.submit.return_value = SubmissionResult(replace(attempt, quiz_id='q2'), False)
    with pytest.raises(ContractError, match='^INTERNAL_ERROR$'):
        adapter.submit(payload())


def test_history_input_does_not_coerce_boolean_limit():
    with pytest.raises(ValueError):
        HistoryInput(context_id='ctx', limit=True)


def test_malformed_service_response_is_sanitized(prepared):
    adapter, service, _ = prepared
    service.submit.return_value = {'private': 'answer'}
    with pytest.raises(ContractError, match='^INTERNAL_ERROR$'):
        adapter.submit(payload())


def test_blank_policy_can_preserve_input_when_explicitly_allowed(prepared):
    _, service, _ = prepared
    adapter = QuizContractAdapter(service, InputPolicy(10, 100, False))
    adapter.submit(payload(answers=[{'question_index': 0, 'text': '  '}]))
    assert service.submit.call_args.kwargs['answers'] == {0: '  '}


def test_preparation_does_not_register_quiz_tools():
    server = create_server(MCPServices(Mock(), Mock()))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert not names.intersection({'get_quiz', 'submit_quiz', 'get_quiz_attempt',
                                   'list_quiz_attempts', 'get_quiz_scores', 'review_quiz_attempt'})
