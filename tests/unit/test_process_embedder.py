"""실제 모델 없이 프로세스 재사용·제한 시간·잘못된 응답을 검증한다."""

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from solo_leveling.infrastructure.embeddings.process_embedder import (
    ProcessEmbedder, EmbeddingProcessError, EmbeddingFailure,
)


def fake_worker(connection):
    try:
        while True:
            request = connection.recv()
            model = request['model']
            if model == 'hang':
                time.sleep(60)
            if model == 'crash':
                os._exit(1)
            if model == 'bad':
                connection.send({'ok': True, 'dimension': 2, 'vectors': [[float('nan'), 0.0]]})
            elif model == 'fail':
                connection.send({'ok': False})
            else:
                connection.send({'ok': True, 'dimension': 2,
                                 'vectors': [[float(os.getpid()), 1.0] for _ in request['texts']]})
    except EOFError:
        pass


def test_reuses_process_and_closes():
    embedder = ProcessEmbedder(10, worker_target=fake_worker)
    try:
        first = embedder(['sample'], model_name='ok')
        second = embedder(['sample', 'other'], model_name='ok')
        assert first[0] == second[0] == second[1]
        process = embedder._process
        assert process.is_alive()
    finally:
        embedder.close()
    assert embedder._process is None
    with pytest.raises(EmbeddingProcessError) as error:
        embedder(['sample'], model_name='ok')
    assert error.value.code == EmbeddingFailure.CLOSED


@pytest.mark.parametrize(('mode', 'code'), [
    ('hang', EmbeddingFailure.TIMEOUT), ('crash', EmbeddingFailure.UNAVAILABLE),
    ('bad', EmbeddingFailure.INVALID_RESULT), ('fail', EmbeddingFailure.UNAVAILABLE),
])
def test_failure_resets_worker_without_retry(mode, code, tmp_path, capsys):
    embedder = ProcessEmbedder(3, worker_target=fake_worker, log_dir=tmp_path)
    try:
        start = time.monotonic()
        with pytest.raises(EmbeddingProcessError) as error:
            embedder(['sample'], model_name=mode)
        assert error.value.code == code
        assert time.monotonic() - start < 7
        assert embedder._process is None
        child_logs = list(tmp_path.glob('worker-*.log'))
        assert child_logs
        events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
        assert any(event.get('event') == 'worker_stopped' and event['alive'] is False for event in events)
        entries = [path for path in child_logs if not path.name.startswith('worker-stack-')]
        assert json.loads(entries[0].read_text().splitlines()[0])['event'] == 'worker_enter'
        if mode == 'hang':
            stack = next(tmp_path.glob('worker-stack-*.log')).read_text()
            assert 'Timeout' in stack and 'fake_worker' in stack
        embedder.timeout = 10
        assert len(embedder(['sample'], model_name='ok')) == 1
    finally:
        embedder.close()


def test_queue_timeout_does_not_kill_other_request():
    embedder = ProcessEmbedder(0.1, worker_target=fake_worker)
    embedder._serial.acquire()
    try:
        with pytest.raises(EmbeddingProcessError) as error:
            embedder(['sample'], model_name='ok')
        assert error.value.code == EmbeddingFailure.TIMEOUT
        assert embedder._process is None
    finally:
        embedder._serial.release()
        embedder.close()


def test_close_interrupts_active_request():
    embedder = ProcessEmbedder(10, worker_target=fake_worker)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(embedder, ['sample'], model_name='hang')
        deadline = time.monotonic() + 5
        while embedder._process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        embedder.close()
        with pytest.raises(EmbeddingProcessError):
            future.result(timeout=5)


def test_safe_mcp_error_code():
    from solo_leveling.interfaces.mcp.errors import to_tool_error
    error = to_tool_error(EmbeddingProcessError(EmbeddingFailure.TIMEOUT))
    assert 'UPSTREAM_UNAVAILABLE' in str(error)
    assert 'EMBEDDING_TIMEOUT' in str(error)


_STDIN_READER_SCRIPT = '''
import sys, threading, time
from solo_leveling.infrastructure.embeddings.process_embedder import ProcessEmbedder, EmbeddingProcessError


def echo_worker(connection):
    request = connection.recv()
    connection.send({'ok': True, 'dimension': 1, 'vectors': [[1.0] for _ in request['texts']]})


if __name__ == '__main__':
    received = []
    reader = threading.Thread(target=lambda: received.append(sys.stdin.buffer.readline()), daemon=True)
    reader.start()
    time.sleep(0.5)
    embedder = ProcessEmbedder(10, worker_target=echo_worker)
    try:
        embedder(['sample'], model_name='ok')
        print('spawn_ok', flush=True)
    except EmbeddingProcessError as error:
        print(error.code.value, flush=True)
    finally:
        embedder.close()
    reader.join(timeout=10)
    print(received[0].decode().strip() if received else 'no_input', flush=True)
'''


@pytest.mark.skipif(os.name != 'nt', reason='Windows stdin 파이프 상속 문제')
def test_spawn_while_stdin_pipe_is_being_read(tmp_path):
    """MCP stdio처럼 stdin 블로킹 읽기 중에도 자식이 부팅되고 이후 stdin 입력도 유지된다."""
    import subprocess
    import sys
    from pathlib import Path
    script = tmp_path / 'stdin_reader.py'
    script.write_text(_STDIN_READER_SCRIPT, encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    process = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, env=env)
    try:
        assert process.stdout.readline().decode().strip() == 'spawn_ok'
        process.stdin.write(b'after_spawn\n')
        process.stdin.flush()
        assert process.stdout.readline().decode().strip() == 'after_spawn'
    finally:
        process.kill()
        process.wait(timeout=5)
