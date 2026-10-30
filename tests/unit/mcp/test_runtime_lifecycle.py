"""공유 자원 사용·종료 경쟁과 초기화 실패의 정리 계약을 검증한다."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from solo_leveling.interfaces.mcp.lifecycle import RequestGate, RuntimeLifetime
from solo_leveling.interfaces.mcp.ingestion import IngestionShuttingDownError
from solo_leveling.interfaces.mcp import runtime
from solo_leveling.infrastructure.storage.vector_store import ChromaCloseResult


def test_active_request_and_background_job_both_finish_before_close():
    gate = RequestGate()
    request_started, request_release = threading.Event(), threading.Event()
    job_started, job_release = threading.Event(), threading.Event()
    order = []

    def request():
        request_started.set()
        assert request_release.wait(5)
        order.append('request')

    def drain(timeout):
        assert timeout is None
        job_started.set()
        assert job_release.wait(5)
        order.append('job')
        return True

    manager = Mock(wait_for_idle=Mock(side_effect=drain))
    embedder = Mock(close=Mock(side_effect=lambda: order.append('embedder')))
    client = SimpleNamespace(close=lambda: order.append('chroma'))
    lifetime = RuntimeLifetime(gate, manager, embedder, client)
    service = gate.wrap(SimpleNamespace(read=request))
    caller = threading.Thread(target=service.read)
    caller.start()
    try:
        assert request_started.wait(5)
        lifetime.begin()
        lifetime.begin()
        with pytest.raises(IngestionShuttingDownError):
            service.read()
        assert not lifetime.done.wait(.02)
        request_release.set()
        assert job_started.wait(5)
        embedder.close.assert_not_called()
        assert not lifetime.done.wait(.02)
    finally:
        request_release.set()
        job_release.set()
        caller.join(5)
        assert lifetime.done.wait(5)
    lifetime.begin()
    assert order == ['request', 'job', 'embedder', 'chroma']
    manager.begin_shutdown.assert_called_once()
    assert lifetime.close_result is ChromaCloseResult.CLOSED


def test_request_failure_releases_gate():
    gate = RequestGate()
    wrapped = gate.wrap(SimpleNamespace(read=Mock(side_effect=ValueError('synthetic-private'))))
    with pytest.raises(ValueError):
        wrapped.read()
    gate.stop()
    gate.wait()
    assert gate.active == 0


@pytest.mark.parametrize('failure', ['embedder', 'chroma', 'unsupported', 'drain'])
def test_cleanup_failure_is_safe_and_not_reported_as_success(failure, capsys):
    embedder = Mock()
    client = SimpleNamespace(close=Mock())
    manager = Mock()
    if failure == 'embedder':
        embedder.close.side_effect = ValueError('synthetic-private')
    elif failure == 'chroma':
        client.close.side_effect = ValueError('synthetic-private')
    elif failure == 'unsupported':
        client = object()
    else:
        manager.wait_for_idle.side_effect = ValueError('synthetic-private')
    lifetime = RuntimeLifetime(RequestGate(), manager, embedder, client)
    lifetime.begin()
    assert lifetime.done.wait(5)
    text = capsys.readouterr().err
    assert 'synthetic-private' not in text
    assert 'FAILED' in text or 'UNSUPPORTED' in text
    if failure == 'drain':
        embedder.close.assert_not_called()
        client.close.assert_not_called()
        assert lifetime.close_result is None
    elif failure == 'chroma':
        assert lifetime.close_result is ChromaCloseResult.FAILED
    elif failure == 'unsupported':
        assert lifetime.close_result is ChromaCloseResult.UNSUPPORTED
    else:
        client.close.assert_called_once()


def prepare_runtime(monkeypatch):
    embedder = Mock()
    client = SimpleNamespace(close=Mock())
    services = SimpleNamespace(answer=SimpleNamespace(semantic_verification=None), evidence=object(),
        search=SimpleNamespace(contexts=object()), translation=object(), translation_settings=object())
    monkeypatch.setattr(runtime, 'ProcessEmbedder', Mock(return_value=embedder))
    monkeypatch.setattr(runtime, 'get_client', Mock(return_value=client))
    monkeypatch.setattr(runtime, 'build_services', Mock(return_value=services))
    monkeypatch.setattr(runtime, 'init_db', Mock())
    monkeypatch.setattr(runtime, 'mark_interrupted_jobs_on_startup', Mock())
    manager = Mock()
    monkeypatch.setattr(runtime, 'LocalIngestionManager', Mock(return_value=manager))
    monkeypatch.setattr(runtime, 'create_server', Mock(side_effect=lambda services, *, lifespan: (services, lifespan)))
    return embedder, client, manager


@pytest.mark.parametrize('stage', ['init_db', 'get_client', 'build_services', 'LocalIngestionManager', 'create_server'])
def test_partial_initialization_cleans_owned_resources(tmp_path, monkeypatch, stage):
    embedder, client, manager = prepare_runtime(monkeypatch)
    original = ValueError('original-private')
    getattr(runtime, stage).side_effect = original
    with pytest.raises(ValueError) as caught:
        runtime.build_runtime_server({'DATA_ROOT': str(tmp_path), 'OPENAI_API_KEY': 'synthetic'})
    assert caught.value is original
    embedder.close.assert_called_once()
    if stage in ('init_db', 'get_client'):
        client.close.assert_not_called()
    else:
        client.close.assert_called_once()
    if stage == 'create_server':
        manager.begin_shutdown.assert_called_once()


def test_lifespan_timeout_keeps_resources_until_last_request(tmp_path, monkeypatch, capsys):
    embedder, client, manager = prepare_runtime(monkeypatch)
    services, lifespan = runtime.build_runtime_server({'DATA_ROOT': str(tmp_path),
        'OPENAI_API_KEY': 'synthetic', 'MCP_SHUTDOWN_TIMEOUT_SECONDS': '0'})
    gate = services.answer.gate
    with gate.use():
        async def run():
            async with lifespan(None):
                pass
            await asyncio.sleep(0)
        asyncio.run(run())
        embedder.close.assert_not_called()
        client.close.assert_not_called()
        assert 'RUNTIME_SHUTDOWN_DEFERRED' in capsys.readouterr().err
    # 임의 sleep 대신 자원 정리의 관측 가능한 이벤트로 완료를 기다린다.
    for thread in threading.enumerate():
        if thread.name == 'mcp-resource-cleanup':
            thread.join(5)
            assert not thread.is_alive()
    embedder.close.assert_called_once()
    client.close.assert_called_once()


@pytest.mark.parametrize('timeout', ['-1', 'nan', 'inf', 'invalid'])
def test_invalid_shutdown_timeout_does_not_create_resources(tmp_path, monkeypatch, timeout):
    factory = Mock()
    monkeypatch.setattr(runtime, 'ProcessEmbedder', factory)
    with pytest.raises(ValueError):
        runtime.build_runtime_server({'DATA_ROOT': str(tmp_path), 'OPENAI_API_KEY': 'synthetic',
                                      'MCP_SHUTDOWN_TIMEOUT_SECONDS': timeout})
    factory.assert_not_called()


def test_cleanup_codes_survive_safe_filter_without_private_fields():
    import io
    import json
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    output = io.StringIO()
    stream = SafeDiagnosticStream(output)
    codes = ['INGESTION_CANCEL_STATE_FAILED', 'RUNTIME_DRAIN_FAILED', 'EMBEDDER_CLOSE_FAILED', 'RUNTIME_SHUTDOWN_DEFERRED',
             'CHROMA_CLOSE_UNSUPPORTED', 'CHROMA_CLOSE_FAILED']
    for code in codes:
        stream.write(json.dumps({'event': 'runtime_cleanup', 'code': code}) + '\n')
    stream.write(json.dumps({'event': 'runtime_cleanup', 'code': 'synthetic-private'}) + '\n')
    stream.write(json.dumps({'event': 'runtime_cleanup', 'code': codes[0], 'text': 'synthetic-private'}) + '\n')
    stream.write(json.dumps({'event': 'runtime_cleanup', 'code': []}) + '\n')
    assert [json.loads(line)['code'] for line in output.getvalue().splitlines()] == codes


def test_diagnostic_runner_protects_output_until_deferred_cleanup(tmp_path, monkeypatch, capsys):
    import os
    import sys
    from solo_leveling.interfaces.cli import mcp_diagnostics

    embedder, client, _ = prepare_runtime(monkeypatch)
    embedder.close.side_effect = ValueError('synthetic-private-error')
    started, release = threading.Event(), threading.Event()
    returned, finished = threading.Event(), threading.Event()
    workers, results = [], []

    def module(*args, **kwargs):
        services, lifespan = runtime.build_runtime_server({'DATA_ROOT': str(tmp_path / 'data'),
            'OPENAI_API_KEY': 'synthetic', 'MCP_SHUTDOWN_TIMEOUT_SECONDS': '0'})

        def request():
            with services.answer.gate.use():
                started.set()
                assert release.wait(5)
                print('synthetic-private-python', file=sys.stderr)
                os.write(2, b'synthetic-private-native\n')

        worker = threading.Thread(target=request)
        workers.append(worker)
        worker.start()
        assert started.wait(5)

        async def run():
            async with lifespan(None):
                pass
        asyncio.run(run())
        returned.set()

    monkeypatch.setattr(mcp_diagnostics.runpy, 'run_module', module)

    def launch():
        try:
            results.append(mcp_diagnostics.main(['--log-dir', str(tmp_path / 'logs')]))
        finally:
            finished.set()

    runner = threading.Thread(target=launch)
    runner.start()
    try:
        assert returned.wait(5)
        assert not finished.wait(.02)
    finally:
        release.set()
        runner.join(5)
        for worker in workers:
            worker.join(5)
    assert not runner.is_alive()
    assert results == [0]
    log = next((tmp_path / 'logs').glob('*.log')).read_text()
    assert 'RUNTIME_SHUTDOWN_DEFERRED' in log
    assert 'EMBEDDER_CLOSE_FAILED' in log
    assert 'synthetic-private' not in log
    assert 'synthetic-private' not in capsys.readouterr().err
    client.close.assert_called_once()
