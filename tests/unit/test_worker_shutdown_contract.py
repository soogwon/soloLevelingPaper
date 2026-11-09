"""#99 워커 종료 계약을 대역과 실제 Pipe·프로세스의 닫기 경로에서 검증한다."""

from contextlib import redirect_stderr
from io import StringIO
import json
from multiprocessing import Pipe

import pytest

from solo_leveling.infrastructure.embeddings import process_embedder as module
from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream


class Process:
    pid = 123
    exitcode = None

    def __init__(self, fault):
        self.fault = fault
        self.alive = True
        self.closed = False
        self.calls = []

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.calls.append('terminate')
        if self.fault == 'terminate':
            raise OSError('SYNTHETIC_PRIVATE_TERMINATION')
        if self.fault not in ('kill', 'survivor'):
            self.alive = False
            self.exitcode = -1

    def join(self, timeout):
        self.calls.append('join')
        if self.fault == 'join':
            raise OSError('SYNTHETIC_PRIVATE_WAIT')

    def kill(self):
        self.calls.append('kill')
        if self.fault == 'kill':
            raise OSError('SYNTHETIC_PRIVATE_KILL')
        if self.fault != 'survivor':
            self.alive = False
            self.exitcode = -1

    def close(self):
        assert not self.alive
        self.calls.append('close')
        self.closed = True


class Connection:
    closed = False

    def send(self, request):
        pass

    def recv(self):
        return {'ok': False}

    def close(self):
        self.closed = True


class ClosedStateFailure:
    @property
    def closed(self):
        raise OSError('SYNTHETIC_PRIVATE_CLOSED_STATE')

    def close(self):
        raise OSError('SYNTHETIC_PRIVATE_CLOSE')


class FailingFinalizer:
    def still_active(self):
        raise OSError('SYNTHETIC_PRIVATE_FINALIZER_STATE')


class PopenWithFailingFinalizer:
    finalizer = FailingFinalizer()


class FinalizerStateFailure:
    pid = 123
    exitcode = 0
    _popen = PopenWithFailingFinalizer()

    def is_alive(self):
        return False

    def join(self, timeout):
        pass

    def close(self):
        raise OSError('SYNTHETIC_PRIVATE_CLOSE')


def request_failure(tmp_path, fault):
    embedder = module.ProcessEmbedder(1, log_dir=tmp_path)
    process, connection = Process(fault), Connection()
    embedder._process, embedder._connection = process, connection
    try:
        embedder(['synthetic'], model_name='synthetic')
    except Exception as error:
        return embedder, process, connection, error
    pytest.fail('REQUEST_FAILURE_NOT_RETURNED')


def test_normal_stop_preserves_fixed_request_error_and_releases_resources(tmp_path):
    embedder, process, connection, error = request_failure(tmp_path, None)
    assert isinstance(error, module.EmbeddingProcessError)
    assert error.code == module.EmbeddingFailure.UNAVAILABLE
    assert not process.alive and process.closed and connection.closed
    assert embedder._process is None and embedder._connection is None
    assert process.calls == ['terminate', 'join', 'close']
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('fault', ['terminate', 'join', 'kill'])
def test_cleanup_failure_preserves_fixed_request_error(tmp_path, fault):
    _, _, _, error = request_failure(tmp_path, fault)
    assert isinstance(error, module.EmbeddingProcessError), 'RAW_CLEANUP_ERROR_RETURNED'
    assert error.code == module.EmbeddingFailure.UNAVAILABLE
    assert 'SYNTHETIC_PRIVATE' not in str(error)


@pytest.mark.parametrize('fault', ['terminate', 'join', 'kill'])
def test_cleanup_failure_closes_parent_ipc(tmp_path, fault):
    _, _, connection, _ = request_failure(tmp_path, fault)
    assert connection.closed, 'PARENT_IPC_LEFT_OPEN'


@pytest.mark.parametrize('fault', ['terminate', 'kill', 'survivor'])
def test_process_ownership_matches_confirmed_exit(tmp_path, fault):
    embedder, process, _, _ = request_failure(tmp_path, fault)
    if process.alive:
        assert embedder._process is process, 'LIVE_WORKER_OWNERSHIP_LOST'
        assert embedder._stop_unconfirmed
        assert not process.closed
    else:
        assert process.closed
        assert embedder._process is None
        assert not embedder._stop_unconfirmed


def test_surviving_worker_is_not_reported_as_stopped(tmp_path, capsys):
    _, process, connection, error = request_failure(tmp_path, 'survivor')
    assert isinstance(error, module.EmbeddingProcessError)
    assert error.code == module.EmbeddingFailure.UNAVAILABLE
    assert process.alive and not process.closed and connection.closed
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'worker_stop_failed' in captured.err
    assert 'worker_stopped' not in captured.err
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('fault', ['terminate', 'join', 'kill'])
def test_cleanup_failure_is_redacted_at_mcp_error_boundary(tmp_path, capsys, fault):
    from solo_leveling.interfaces.mcp.errors import to_tool_error

    embedder, _, _, error = request_failure(tmp_path, fault)
    mapped = str(to_tool_error(error))
    assert mapped.startswith(('INTERNAL_ERROR:', 'UPSTREAM_UNAVAILABLE:'))
    assert 'SYNTHETIC_PRIVATE' not in mapped
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'SYNTHETIC_PRIVATE' not in captured.err
    assert not list(tmp_path.iterdir())
    # 오류 뒤 요청 직렬화 잠금 자체가 영구 잠금으로 남는지도 따로 확인한다.
    assert embedder._serial.acquire(blocking=False)
    embedder._serial.release()


@pytest.mark.parametrize('fault', ['terminate', 'kill', 'survivor'])
def test_followup_spawn_matches_worker_cleanup_state(tmp_path, monkeypatch, fault):
    embedder, process, _, _ = request_failure(tmp_path, fault)
    spawns = []
    cleanup_calls = list(process.calls)

    def blocked_context(method):
        # 시도만 관찰하고 실제 새 프로세스는 절대 생성하지 않는다.
        spawns.append(method)
        raise RuntimeError('SYNTHETIC_SPAWN_BLOCKED')

    monkeypatch.setattr(module.multiprocessing, 'get_context', blocked_context)
    try:
        embedder(['synthetic'], model_name='synthetic')
    except Exception:
        pass
    if process.alive:
        assert not spawns, 'NEW_SPAWN_WITH_UNCOLLECTED_WORKER'
        assert embedder._process is process
        assert process.calls == cleanup_calls, 'SHUTDOWN_API_RETRIED_WITHOUT_NEW_EVIDENCE'
    else:
        assert spawns == ['spawn']


def test_quarantined_worker_is_reaped_after_natural_exit(tmp_path, monkeypatch):
    embedder, process, _, _ = request_failure(tmp_path, 'survivor')
    assert embedder._process is process and embedder._stop_unconfirmed
    process.alive = False
    process.exitcode = -1
    spawns = []

    def blocked_context(method):
        spawns.append(method)
        raise RuntimeError('SYNTHETIC_SPAWN_BLOCKED')

    monkeypatch.setattr(module.multiprocessing, 'get_context', blocked_context)
    with pytest.raises(module.EmbeddingProcessError):
        embedder(['synthetic'], model_name='synthetic')
    assert process.closed
    assert spawns == ['spawn']
    assert embedder._process is None and not embedder._stop_unconfirmed


def test_pid_lookup_failure_blocks_spawn_until_worker_exits(tmp_path, monkeypatch):
    def fail_pid(process):
        raise OSError('SYNTHETIC_PRIVATE_PID')

    monkeypatch.setattr(Process, 'pid', property(fail_pid))
    embedder, process, connection, error = request_failure(tmp_path, 'survivor')
    spawns = []

    def blocked_context(method):
        spawns.append(method)
        raise RuntimeError('SYNTHETIC_SPAWN_BLOCKED')

    monkeypatch.setattr(module.multiprocessing, 'get_context', blocked_context)
    assert error.code == module.EmbeddingFailure.UNAVAILABLE
    with pytest.raises(module.EmbeddingProcessError) as followup:
        embedder(['synthetic'], model_name='synthetic')
    assert followup.value.code == module.EmbeddingFailure.UNAVAILABLE
    assert spawns == []
    assert process.alive and embedder._process is process and connection.closed
    process.alive = False
    with pytest.raises(module.EmbeddingProcessError):
        embedder(['synthetic'], model_name='synthetic')
    assert process.closed and spawns == ['spawn']


@pytest.mark.parametrize('fail_process,fail_ipc', [(True, False), (False, True), (True, True)])
def test_close_retries_only_unreleased_handles(tmp_path, monkeypatch, fail_process, fail_ipc):
    def count_closes(cls, fail_first):
        calls = []
        original_close = cls.close

        def close(handle):
            calls.append(handle)
            if fail_first and len(calls) == 1:
                raise OSError('SYNTHETIC_PRIVATE_CLOSE')
            original_close(handle)

        monkeypatch.setattr(cls, 'close', close)
        return calls

    process_calls = count_closes(Process, fail_process)
    ipc_calls = count_closes(Connection, fail_ipc)
    embedder, process, connection, error = request_failure(tmp_path, None)
    assert error.code == module.EmbeddingFailure.UNAVAILABLE
    assert process.closed == (not fail_process)
    assert connection.closed == (not fail_ipc)
    embedder.close()
    embedder.close()
    assert process.closed and connection.closed
    assert process_calls == [process] * (2 if fail_process else 1)
    assert ipc_calls == [connection] * (2 if fail_ipc else 1)


@pytest.mark.parametrize('release_before_error', [False, True])
def test_pipe_close_with_lost_handle_reports_uncertainty_once(monkeypatch, release_before_error):
    parent, peer = Pipe()
    raw_handle = parent.fileno()
    close_os_handle = parent._close
    close_calls = []

    def fail_close():
        close_calls.append(True)
        if release_before_error:
            close_os_handle()
        raise OSError('SYNTHETIC_PRIVATE_CLOSE')

    embedder = module.ProcessEmbedder()
    embedder._connection = parent
    output = StringIO()
    try:
        monkeypatch.setattr(parent, '_close', fail_close)
        with redirect_stderr(SafeDiagnosticStream(output)):
            embedder.close()
            assert parent.closed
            assert not embedder._pending_closes
            embedder.close()
        assert len(close_calls) == 1
        assert [json.loads(line) for line in output.getvalue().splitlines()] == [
            {'event': 'runtime_cleanup', 'code': 'WORKER_HANDLE_RELEASE_UNCONFIRMED'}]
    finally:
        peer.close()
        if not release_before_error and close_calls:
            # 이 대역이 해제를 생략한 경우에만 테스트가 소유한 핸들을 회수한다.
            type(parent)(raw_handle).close()


def test_pipe_close_with_retained_handle_retries(monkeypatch):
    parent, peer = Pipe()
    close = parent.close
    calls = []
    output = StringIO()

    def fail_once():
        calls.append(True)
        if len(calls) == 1:
            raise OSError('SYNTHETIC_PRIVATE_CLOSE')
        close()

    embedder = module.ProcessEmbedder()
    embedder._connection = parent
    try:
        monkeypatch.setattr(parent, 'close', fail_once)
        with redirect_stderr(SafeDiagnosticStream(output)):
            embedder.close()
            assert not parent.closed
            peer.send('probe')
            assert parent.recv() == 'probe'
            embedder.close()
            embedder.close()
        assert parent.closed and len(calls) == 2
        assert output.getvalue() == ''
    finally:
        close()
        peer.close()


@pytest.mark.parametrize('release_before_error', [False, True])
def test_process_close_retries_only_while_finalizer_is_active(monkeypatch, release_before_error):
    process = module.multiprocessing.get_context('spawn').Process(target=int)
    process.start()
    embedder = module.ProcessEmbedder()
    output = StringIO()
    try:
        process.join(timeout=10)
        assert process.exitcode == 0
        embedder._process = process
        close_resources = process._popen.close
        calls = []

        def close():
            calls.append(True)
            if len(calls) == 1:
                if release_before_error:
                    close_resources()
                raise OSError('SYNTHETIC_PRIVATE_CLOSE')
            close_resources()

        with monkeypatch.context() as patch, redirect_stderr(SafeDiagnosticStream(output)):
            patch.setattr(process._popen, 'close', close)
            embedder.close()
            embedder.close()
        if release_before_error:
            assert len(calls) == 1
            assert json.loads(output.getvalue()) == {
                'event': 'runtime_cleanup', 'code': 'WORKER_HANDLE_RELEASE_UNCONFIRMED'}
        else:
            assert len(calls) == 2
            assert output.getvalue() == ''
    finally:
        process.close()


@pytest.mark.parametrize('attribute', ['closed', 'finalizer'])
def test_close_state_lookup_failure_is_redacted_and_not_retried(attribute):
    embedder = module.ProcessEmbedder()
    if attribute == 'closed':
        embedder._connection = ClosedStateFailure()
    else:
        embedder._process = FinalizerStateFailure()
    output = StringIO()

    with redirect_stderr(SafeDiagnosticStream(output)):
        embedder.close()
        embedder.close()

    assert not embedder._pending_closes
    assert [json.loads(line) for line in output.getvalue().splitlines()] == [
        {'event': 'runtime_cleanup', 'code': 'WORKER_HANDLE_RELEASE_UNCONFIRMED'}]
