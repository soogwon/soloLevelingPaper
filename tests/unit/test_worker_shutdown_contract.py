"""#99의 A 인계용 종료 실패 계약. 실제 프로세스·모델·로그는 사용하지 않는다.

기존 정상 종료 검증과 달리 OS 호출 실패를 대역으로 주입한다. strict xfail은 현재
확인한 결함이며 합격 결과가 아니다. A 수정 후 XPASS가 나면 표식을 제거한다.
"""

import pytest

from solo_leveling.infrastructure.embeddings import process_embedder as module


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
