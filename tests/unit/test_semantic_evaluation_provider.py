"""실제 제공자 어댑터는 사용하되 HTTP는 합성 응답으로만 대체한다."""

import json
from pathlib import Path
import socket
from unittest.mock import Mock

import pytest
import requests

from evaluation.semantic.runner import load_dataset
from evaluation.semantic.provider import select_cases, execution_plan, run_provider


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('테스트 중 실제 네트워크 연결 금지')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


@pytest.fixture
def sample():
    data, digest = load_dataset(Path(__file__).resolve().parents[2] / 'evaluation/semantic/cases.json')
    return select_cases(data, case_ids=['crosspage-normal'], include_drafts=True), digest


@pytest.fixture
def values():
    return {'OPENAI_API_KEY': 'fake-key-never-send', 'SEMANTIC_VERIFICATION_ENABLED': 'true',
            'ALLOW_EXTERNAL_API': 'true', 'LOCAL_ONLY': 'false', 'VERIFICATION_MODEL': 'test-model'}


def mock_response(payload, *, bad_id=False, invalid_json=False):
    inputs = json.loads(payload['input'][0]['content'])
    # 각 근거의 전체 원문 구절은 해당 근거에서 가장 긴 구절이다.
    longest = {}
    for quote in inputs['original_quotes']:
        previous = longest.get(quote['evidence_id'])
        if previous is None or len(quote['text']) > len(previous['text']):
            longest[quote['evidence_id']] = quote
    quotes = [q['quote_id'] for q in longest.values()]
    checks = {name: {'status': 'supported', 'quote_ids': quotes}
              for name in ('content', 'direction', 'conditions', 'metrics')}
    checks['causality'] = {'status': 'not_applicable', 'quote_ids': []}
    if bad_id:
        checks['conditions']['quote_ids'] = ['unknown-quote']
    body = {'judgments': [{'claim_id': 'claim-1', 'verdict': 'supported',
                          'reason': 'full_support', 'checks': checks}]}
    response = Mock(status_code=200)
    response.json.return_value = {'status': 'completed', 'output': [
        {'type': 'message', 'role': 'assistant', 'status': 'completed',
         'content': [{'type': 'output_text', 'text': 'not-json' if invalid_json else json.dumps(body)}]}]}
    return response


def test_default_is_plan_and_never_creates_provider(sample, values, monkeypatch):
    from solo_leveling.infrastructure.generation.semantic_verifier import OpenAISemanticVerifier
    monkeypatch.setattr(OpenAISemanticVerifier, '__init__', Mock(side_effect=AssertionError('provider created')))
    data, digest = sample
    result = run_provider(data, digest, values, repeats=2, max_calls=2)
    assert result['mode'] == 'plan' and result['external_calls'] == 0
    assert result['planned_calls'] == 2


@pytest.mark.parametrize('change', [
    {'ALLOW_EXTERNAL_API': 'false'}, {'LOCAL_ONLY': 'true'},
    {'SEMANTIC_VERIFICATION_ENABLED': 'false'}, {'VERIFICATION_MODEL': ''},
    {'OPENAI_API_KEY': ''}, {'VERIFICATION_TIMEOUT_SECONDS': 'nan'},
])
def test_config_gate_prevents_http(sample, values, change, monkeypatch):
    post = Mock(side_effect=AssertionError('HTTP called'))
    monkeypatch.setattr(requests, 'post', post)
    values.update(change)
    with pytest.raises(ValueError, match='EVALUATION_PROVIDER_CONFIG_INVALID'):
        run_provider(*sample, values, repeats=1, max_calls=1, execute=True)
    post.assert_not_called()


def test_selection_and_preflight_limits(sample):
    data, _ = sample
    assert select_cases(data)['cases'] == []
    with pytest.raises(ValueError, match='CASE_NOT_ELIGIBLE'):
        select_cases(data, case_ids=['crosspage-normal'])
    with pytest.raises(ValueError, match='CASE_SELECTION_INVALID'):
        select_cases(data, case_ids=['unknown'], include_drafts=True)
    with pytest.raises(ValueError, match='CALL_LIMIT'):
        execution_plan(data, repeats=2, max_calls=1)
    data['cases'][0]['review_status'] = 'ambiguous'
    assert select_cases(data, include_drafts=True)['cases'] == []


def test_real_adapter_checks_original_quote_ids_without_network(sample, values, monkeypatch):
    responses = []
    def post(url, **kwargs):
        payload = kwargs['json']
        assert payload['store'] is False
        inputs = json.loads(payload['input'][0]['content'])
        assert 'crosspage-normal' not in json.dumps(inputs)
        assert 'expected' not in inputs and 'rationale' not in inputs
        assert inputs['claims'][0]['evidence_ids'] == ['evidence-1', 'evidence-2']
        assert inputs['evidence'][1]['follows_evidence_id'] == 'evidence-1'
        responses.append(mock_response(payload))
        return responses[-1]
    mocked = Mock(side_effect=post)
    monkeypatch.setattr(requests, 'post', mocked)
    result = run_provider(*sample, values, repeats=1, max_calls=1, execute=True)
    assert result['results'][0]['verdict'] == 'supported'
    assert result['results'][0]['failure'] is None
    assert result['prompt_version'] == 'semantic-v7'
    assert result['max_output_tokens'] == 2048
    mocked.assert_called_once()
    responses[0].close.assert_called_once()
    assert 'fake-key' not in json.dumps(result)


@pytest.mark.parametrize('bad_id,invalid_json,code,check', [
    (True, False, 'ORIGINAL_QUOTE_ID_UNKNOWN', 'original.conditions'),
    (False, True, 'PROVIDER_JSON_INVALID', None),
])
def test_safe_rejection_details_from_adapter(sample, values, monkeypatch, bad_id, invalid_json, code, check):
    monkeypatch.setattr(requests, 'post', lambda url, **kw: mock_response(
        kw['json'], bad_id=bad_id, invalid_json=invalid_json))
    result = run_provider(*sample, values, repeats=1, max_calls=1, execute=True)
    row = result['results'][0]
    assert row['failure'] == 'invalid_response'
    assert row['detail_code'] == code and row['check'] == check


def test_timeout_is_not_retried(sample, values, monkeypatch):
    post = Mock(side_effect=requests.Timeout('sensitive-error-do-not-log'))
    monkeypatch.setattr(requests, 'post', post)
    result = run_provider(*sample, values, repeats=1, max_calls=1, execute=True)
    post.assert_called_once()
    assert result['summary']['failures'] == {'timeout': 1}
    assert 'sensitive-error' not in json.dumps(result)


def test_input_limit_is_honored_before_http(sample, values, monkeypatch):
    values['VERIFICATION_MAX_INPUT_CHARS'] = '1'
    post = Mock(side_effect=AssertionError('HTTP called'))
    monkeypatch.setattr(requests, 'post', post)
    result = run_provider(*sample, values, repeats=1, max_calls=1, execute=True)
    assert result['summary']['failures'] == {'input_limit': 1}
    post.assert_not_called()


def test_cli_plan_does_not_read_environment_file(monkeypatch, capsys):
    from scripts import evaluate_semantic_provider as cli
    monkeypatch.setattr(cli, 'load_values', Mock(side_effect=AssertionError('env read')))
    assert cli.main(['--max-calls', '3']) == 0
    assert '"planned_calls": 0' in capsys.readouterr().out
    assert cli.main(['--include-drafts', '--case', 'crosspage-normal', '--max-calls', '1']) == 0
    assert '"planned_calls": 1' in capsys.readouterr().out


def test_cli_live_requires_explicit_config_before_env_read(monkeypatch, capsys):
    from scripts import evaluate_semantic_provider as cli
    monkeypatch.setattr(cli, 'load_values', Mock(side_effect=AssertionError('env read')))
    assert cli.main(['--live', '--max-calls', '1', '--include-drafts', '--case', 'crosspage-normal']) == 2
    assert '평가 준비 실패' in capsys.readouterr().err
