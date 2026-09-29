"""별도 프로세스에서 stdout 보존과 stderr 파일 수집을 확인한다."""

import json
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('options', [[], ['--preload-embedding-library']])
def test_probe_collects_stderr_without_starting_server(tmp_path, options):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'run_mcp_diagnostics.py'
    result = subprocess.run([sys.executable, str(script), '--check', '--log-dir', str(tmp_path), *options],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert result.stdout == result.stderr == ''
    paths = list(tmp_path.glob('*.log'))
    assert len(paths) == 1
    logs = [json.loads(line) for line in paths[0].read_text(encoding='utf-8').splitlines()]
    assert logs[0]['event'] == 'diagnostic_startup'
    assert logs[0]['check_only'] is True
    assert Path(logs[0]['modules']['solo_leveling.diagnostics']).is_file()
    assert [r['event'] for r in logs[1:]] == ['start', 'end']
    assert logs[1]['request_id'] == logs[2]['request_id']


def test_preload_import_does_not_construct_model(monkeypatch, capsys):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from scripts.run_mcp_diagnostics import preload_embedding_library
    constructor = Mock(side_effect=AssertionError('모델 생성 금지'))
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=constructor))
    preload_embedding_library()
    constructor.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ''
    records = [json.loads(line) for line in output.err.splitlines()]
    assert [r['event'] for r in records] == ['start', 'end']
    assert all(r['stage'] == 'startup_embedding_import' for r in records)
