"""모델을 다운로드하지 않고 준비 명령의 성공·실패를 확인한다."""

import subprocess
import sys
import os
from pathlib import Path
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


def test_source_launcher_runs_real_child_without_installed_package(tmp_path):
    """모델 대역만 제공하고 저장소 래퍼와 자식 프로세스는 실제로 실행한다."""
    source = Path(__file__).resolve().parents[2]
    fake_modules = tmp_path / '한글 모델 대역'
    fake_modules.mkdir()
    (fake_modules / 'sentence_transformers.py').write_text('''
from pathlib import Path
import os
import solo_leveling.interfaces.cli.prepare_embedding_model as runner

class SentenceTransformer:
    def __init__(self, model_name, *, local_files_only):
        assert local_files_only
        assert Path(runner.__file__).resolve() == Path(os.environ['EXPECTED_RUNNER']).resolve()
    def encode(self, texts, *, convert_to_numpy):
        return [[1.0, 0.0]]
    def get_sentence_embedding_dimension(self):
        return 2
''', encoding='utf-8')
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('PYTHON')}
    environment.update(
        PYTHONPATH=str(fake_modules),
        PYTHONIOENCODING='utf-8',
        EXPECTED_RUNNER=str(source / 'src/solo_leveling/interfaces/cli/prepare_embedding_model.py'))
    # -S로 부모의 설치 패키지 경로를 제거하고 저장소 밖에서 실행한다.
    result = subprocess.run(
        [sys._base_executable, '-S', '-X', 'utf8',
         str(source / 'scripts/prepare_embedding_model.py'), '--offline', '--timeout', '15'],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        encoding='utf-8', timeout=25)
    assert result.returncode == 0, result.stderr
    assert '준비 완료: 차원=2' in result.stdout
