"""로컬 실행기의 외부 전송 방어와 흐름 연결을 검증한다."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scripts import run_paper as runner


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    for key in list(runner.os.environ):
        if key.startswith(('OPENAI_', 'GENERATION_', 'TRANSLATION_')) or key in ('ALLOW_EXTERNAL_API', 'LOCAL_ONLY'):
            monkeypatch.delenv(key, raising=False)
    pdf = tmp_path / 'sample.pdf'
    pdf.write_bytes(b'%PDF-1.7\n')
    env = tmp_path / '.env'
    env.write_text('OPENAI_API_KEY=test-secret\nALLOW_EXTERNAL_API=true\n', encoding='utf-8')
    monkeypatch.setattr(runner, 'get_client', Mock(side_effect=AssertionError('저장소 접근 금지')))
    monkeypatch.setattr('requests.post', Mock(side_effect=AssertionError('외부 호출 금지')))
    return [str(pdf), '--question', '결과는?', '--env-file', str(env)]


def test_dry_does_not_create_storage(inputs, monkeypatch, capsys):
    create = Mock(side_effect=AssertionError('폴더 생성 금지'))
    monkeypatch.setattr(runner.tempfile, 'mkdtemp', create)
    assert runner.main(inputs) == 0
    create.assert_not_called()
    assert 'test-secret' not in capsys.readouterr().out


def test_blocked_live(inputs, monkeypatch):
    monkeypatch.setenv('LOCAL_ONLY', 'true')
    assert runner.main(inputs + ['--live']) == 2
    runner.get_client.assert_not_called()


@pytest.mark.parametrize('extra', [['--top-k', '0'], ['--top-k', '21'], ['--question', ' ']])
def test_bad_input(inputs, extra):
    assert runner.main(inputs + extra) == 2


@pytest.mark.parametrize('ready', [True, False])
def test_live_flow(inputs, tmp_path, monkeypatch, ready):
    monkeypatch.setattr(runner.tempfile, 'mkdtemp', lambda **kw: str(tmp_path))
    monkeypatch.setattr(runner, 'get_client', Mock(return_value=object()))
    bundle = SimpleNamespace(translation=object(), translation_settings=object(), answer=Mock(), evidence=Mock())
    bundle.answer.answer.return_value = SimpleNamespace(context_id='ctx', result=SimpleNamespace(
        citations=[SimpleNamespace(evidence_id='e1')]))
    monkeypatch.setattr(runner, 'build_services', Mock(return_value=bundle))
    register = Mock(return_value={'status': 'ready' if ready else 'failed', 'version_id': 'v'})
    monkeypatch.setattr(runner, 'register_and_ingest', register)
    monkeypatch.setattr(runner, 'serialize_answer_response', lambda value: {'answer': '답변'})
    monkeypatch.setattr(runner, 'serialize_stored_evidence_result', lambda value: {'evidence': []})
    assert runner.main(inputs + ['--live']) == (0 if ready else 1)
    assert register.call_args.kwargs['translation_service'] is bundle.translation
    if ready:
        bundle.evidence.get.assert_called_once_with('ctx', ['e1'])
    else:
        bundle.answer.answer.assert_not_called()


def test_failure_hides_exception(inputs, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner.tempfile, 'mkdtemp', lambda **kw: str(tmp_path))
    monkeypatch.setattr(runner, 'get_client', Mock(side_effect=RuntimeError('test-secret')))
    assert runner.main(inputs + ['--live']) == 1
    output = capsys.readouterr()
    assert 'test-secret' not in output.out + output.err
