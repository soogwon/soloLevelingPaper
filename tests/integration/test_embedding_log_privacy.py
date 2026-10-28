"""モデルや外部 API を使わず、C の実行設定から A の実プロセスまで検証する。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROBE = r'''
import os, runpy, sys, time
from pathlib import Path
from solo_leveling.infrastructure.embeddings.process_embedder import ProcessEmbedder, EmbeddingProcessError

def worker(connection):
    for stream in (sys.stdout, sys.stderr):
        print('SYNTHETIC_CHILD_PRIVATE', file=stream, flush=True)
    os.write(1, b'SYNTHETIC_NATIVE_PRIVATE\n')
    os.write(2, b'SYNTHETIC_NATIVE_PRIVATE\n')
    request = connection.recv()
    if request['model'] == 'hang':
        time.sleep(60)
    elif request['model'] == 'fail':
        connection.send({'ok': False})
    else:
        connection.send({'ok': True, 'dimension': 1, 'vectors': [[1.0]]})

def server(*args, **kwargs):
    from solo_leveling.interfaces.mcp.runtime import load_environment
    values = load_environment()
    raw = values.get('SOLO_LEVELING_COLLECT_RAW_LOGS') == '1'
    folder = Path(values['DATA_ROOT']) / 'diagnostics'
    embedder = ProcessEmbedder(4, worker_target=worker, log_dir=folder, collect_raw_logs=raw)
    try:
        embedder(['synthetic'], model_name=mode)
        assert mode == 'ok'
    except EmbeddingProcessError as error:
        assert error.code.value == ('EMBEDDING_TIMEOUT' if mode == 'hang' else 'EMBEDDING_UNAVAILABLE')
        assert mode != 'ok'
    finally:
        embedder.close()
    print('MCP_STDOUT_OK', flush=True)

if __name__ == '__main__':
    entry, env_file, mode, raw = sys.argv[1:]
    os.environ['SOLO_LEVELING_ENV_FILE'] = env_file
    before = os.environ.get('SOLO_LEVELING_COLLECT_RAW_LOGS')
    runpy.run_module = server
    options = ['--collect-raw-logs'] if raw == '1' else []
    if entry == 'desktop':
        from solo_leveling.interfaces.cli.desktop import main
        result = main(['serve', '--env-file', env_file, *options])
    elif entry == 'diagnostics':
        from solo_leveling.interfaces.cli.mcp_diagnostics import main
        result = main(options)
    else:
        server()
        result = 0
    assert os.environ.get('SOLO_LEVELING_COLLECT_RAW_LOGS') == before
    raise SystemExit(result)
'''


@pytest.mark.parametrize('entry', ['desktop', 'diagnostics', 'direct'])
@pytest.mark.parametrize('raw', [False, True])
@pytest.mark.parametrize('mode', ['ok', 'fail', 'hang'])
def test_entry_policy_reaches_real_child(tmp_path, entry, raw, mode):
    script = tmp_path / 'probe.py'
    script.write_text(PROBE, encoding='utf-8')
    folder = tmp_path / 'data'
    env_file = tmp_path / 'settings.env'
    # CLI 실행은 파일의 1보다 옵션 유무가 우선한다. 직접 실행은 파일 값을 따른다.
    file_raw = str(int(raw)) if entry == 'direct' else '1'
    env_file.write_text(
        f'DATA_ROOT={folder.as_posix()}\nOPENAI_API_KEY=synthetic-key\n'
        f'LOCAL_ONLY=true\nALLOW_EXTERNAL_API=false\nSOLO_LEVELING_COLLECT_RAW_LOGS={file_raw}\n',
        encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    for key in ('SOLO_LEVELING_COLLECT_RAW_LOGS', 'DATA_ROOT', 'SOLO_LEVELING_ENV_FILE'):
        env.pop(key, None)
    # 부모에 남은 설정도 CLI 옵션 유무에 따라 덮어쓴다.
    if entry != 'direct':
        env['SOLO_LEVELING_COLLECT_RAW_LOGS'] = '1'
    result = subprocess.run([sys.executable, str(script), entry, str(env_file), mode, str(int(raw))],
                            env=env, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'MCP_STDOUT_OK'
    text = ''.join(p.read_text(encoding='utf-8') for p in folder.rglob('*.log'))
    for secret in ('SYNTHETIC_CHILD_PRIVATE', 'SYNTHETIC_NATIVE_PRIVATE'):
        assert (secret in text) is raw
        assert secret not in result.stdout + result.stderr
    logs = list(folder.rglob('worker-*.log'))
    assert logs
    stacks = list(folder.rglob('worker-stack-*.log'))
    assert bool(stacks) is raw
    if raw and mode == 'hang':
        assert 'Timeout' in stacks[0].read_text(encoding='utf-8')
    if not raw:
        for path in logs:
            assert [json.loads(line)['event'] for line in path.read_text().splitlines()] == ['worker_enter']


def test_restart_without_option_stops_new_raw_logs_and_keeps_old_files(tmp_path):
    script = tmp_path / 'probe.py'
    script.write_text(PROBE, encoding='utf-8')
    folder = tmp_path / 'data'
    env_file = tmp_path / 'settings.env'
    env_file.write_text(f'DATA_ROOT={folder.as_posix()}\nSOLO_LEVELING_COLLECT_RAW_LOGS=1\n',
                        encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    env.pop('DATA_ROOT', None)
    old = {}
    for raw in ('1', '0'):
        result = subprocess.run([sys.executable, str(script), 'diagnostics', str(env_file), 'ok', raw],
                                env=env, capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'MCP_STDOUT_OK'
        current = {p: p.read_bytes() for p in folder.rglob('*.log')}
        if raw == '1':
            old = current
            assert any(b'SYNTHETIC_CHILD_PRIVATE' in content for content in old.values())
        else:
            assert all(current[p] == content for p, content in old.items())
            added = {p: content for p, content in current.items() if p not in old}
            assert added
            assert not any(p.name.startswith('worker-stack-') for p in added)
            assert all(b'SYNTHETIC_' not in content for content in added.values())
