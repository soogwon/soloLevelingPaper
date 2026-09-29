"""모델을 다운로드하지 않고 준비 명령의 성공·실패를 확인한다."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scripts import prepare_embedding_model as runner


@pytest.mark.parametrize('vector,expected', [([1.0, 0.0], 0), ([float('nan'), 0.0], 1), ([1.0], 1)])
def test_preparation_checks_vectors(monkeypatch, vector, expected):
    model = Mock()
    model.encode.return_value = [vector]
    model.get_sentence_embedding_dimension.return_value = 2
    constructor = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=constructor))
    assert runner.prepare('fixture', True) == expected
    constructor.assert_called_once_with('fixture', local_files_only=True)


def test_offline_worker_and_timeout(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(runner.subprocess, 'run', run)
    assert runner.main(['--offline', '--timeout', '5']) == 0
    assert run.call_args.kwargs['env']['HF_HUB_OFFLINE'] == '1'
    assert '--offline' in run.call_args.args[0]
    run.side_effect = subprocess.TimeoutExpired('probe', 5)
    assert runner.main(['--timeout', '5']) == 124


def test_failure_redacts_exception(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(
        SentenceTransformer=Mock(side_effect=RuntimeError('SECRET'))))
    assert runner.prepare('fixture', False) == 1
    assert 'SECRET' not in capsys.readouterr().err
