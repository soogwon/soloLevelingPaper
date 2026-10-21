"""별도 프로세스에서 stdout 보존과 stderr 파일 수집을 확인한다."""

import json
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('options', [[], ['--preload-embedding-library']])
@pytest.mark.parametrize('raw', [False, True])
def test_probe_collects_stderr_without_starting_server(tmp_path, options, raw):
    if raw:
        options = [*options, '--collect-raw-logs']
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
    assert logs[0]['raw_logs'] is raw
    if raw:
        assert Path(logs[0]['modules']['solo_leveling.diagnostics']).is_file()
        assert [r['event'] for r in logs[1:]] == ['start', 'end']
        assert logs[1]['request_id'] == logs[2]['request_id']
    else:
        assert not {'python', 'modules', 'log_file'} & logs[0].keys()
        assert [r['event'] for r in logs[1:]] == ['start', 'end']


@pytest.mark.parametrize('raw', [False, True])
def test_raw_python_and_native_output_requires_opt_in(tmp_path, raw):
    # 실제 라이브러리·API 대신 합성 문자열을 출력하는 서버 대역을 별도 프로세스에서 실행한다.
    code = '''
import os, runpy, sys
sys.path.insert(0, sys.argv.pop(1))
from solo_leveling.interfaces.cli.mcp_diagnostics import main
def server(*args, **kwargs):
    print('SYNTHETIC_PRIVATE_TEXT', file=sys.stderr, flush=True)
    os.write(2, b'SYNTHETIC_PRIVATE_KEY\\n')
    print('MCP_STDOUT_UNCHANGED', flush=True)
    raise RuntimeError('SYNTHETIC_EXCEPTION_SECRET')
runpy.run_module = server
raise SystemExit(main(sys.argv[1:]))
'''
    source = Path(__file__).resolve().parents[2] / 'src'
    result = subprocess.run([sys.executable, '-c', code, str(source), '--log-dir', str(tmp_path),
                             *(['--collect-raw-logs'] if raw else [])],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert result.stdout.strip() == 'MCP_STDOUT_UNCHANGED'
    assert result.stderr == ''
    text = next(tmp_path.glob('mcp-*.log')).read_text(encoding='utf-8')
    assert ('SYNTHETIC_PRIVATE_TEXT' in text) is raw
    assert ('SYNTHETIC_PRIVATE_KEY' in text) is raw
    assert 'SYNTHETIC_EXCEPTION_SECRET' not in text
    assert 'MCP_DIAGNOSTIC_STARTUP_FAILED' in text


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
