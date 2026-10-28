"""등록 종료 시 진입 차단과 실행 작업의 수명 추적을 검증한다."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from solo_leveling.interfaces.mcp import ingestion as module
from solo_leveling.interfaces.mcp.errors import to_tool_error


@pytest.fixture
def manager(monkeypatch):
    store = Mock()
    store.import_pdf.return_value = SimpleNamespace(
        path='sample.pdf', original_filename='sample.pdf', file_hash='hash',
        input_fingerprint='fingerprint')
    prepared = SimpleNamespace(paper_id='paper', registration=SimpleNamespace(
        disposition=module.IngestionDisposition.STARTED, job_id='job'))
    monkeypatch.setattr(module, 'prepare_local_ingestion', Mock(return_value=prepared))
    monkeypatch.setattr(module, 'get_ingestion_status', Mock(return_value={}))
    instance = module.LocalIngestionManager('db', 'vectors', store,
        module.IngestionServices(None, None))
    yield instance
    instance.begin_shutdown()
    instance.executor.shutdown(wait=True)


def test_shutdown_rejects_before_file_or_database_access(manager):
    manager.begin_shutdown()
    manager.begin_shutdown()
    with pytest.raises(module.IngestionShuttingDownError) as caught:
        manager.add_local_pdf('sample.pdf', 'request')
    manager.pdf_store.import_pdf.assert_not_called()
    module.prepare_local_ingestion.assert_not_called()
    assert 'SERVER_SHUTTING_DOWN' in str(to_tool_error(caught.value))
    assert manager.wait_for_idle(0)


def test_timeout_keeps_running_and_queued_jobs_alive(manager, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    completed = []

    def run(*args, **kwargs):
        started.set()
        assert release.wait(5)
        completed.append(True)

    monkeypatch.setattr(module, 'run_prepared_local_ingestion', run)
    try:
        manager.add_local_pdf('sample.pdf', 'first')
        assert started.wait(5)
        manager.add_local_pdf('sample.pdf', 'second')
        manager.begin_shutdown()
        assert not manager.wait_for_idle(0)
        assert not completed
    finally:
        release.set()
    assert manager.wait_for_idle(5)
    assert len(completed) == 2


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
def test_immediate_completion_does_not_deadlock_or_leave_pending(manager, monkeypatch, outcome):
    future = Future()
    if outcome == 'cancelled':
        future.cancel()
    elif outcome == 'failure':
        future.set_exception(RuntimeError('비공개 오류'))
    else:
        future.set_result(None)
    monkeypatch.setattr(manager.executor, 'submit', Mock(return_value=future))
    manager.add_local_pdf('sample.pdf', 'request')
    assert manager.wait_for_idle(0)


@pytest.mark.parametrize('timeout', [-1, float('inf'), float('nan'), True])
def test_invalid_timeout_is_rejected(manager, timeout):
    with pytest.raises(ValueError):
        manager.wait_for_idle(timeout)
