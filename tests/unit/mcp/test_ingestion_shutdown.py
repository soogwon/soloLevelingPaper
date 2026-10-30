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
    monkeypatch.setattr(module.repo, 'mark_job_cancelled_before_start', Mock(return_value=True))
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


def test_shutdown_cancels_only_queued_job_and_records_state(manager, monkeypatch):
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
    assert len(completed) == 1
    module.repo.mark_job_cancelled_before_start.assert_called_once_with('db', 'job')


def test_cancel_state_failure_does_not_skip_other_jobs(manager, monkeypatch, capsys):
    futures = [Future(), Future()]
    monkeypatch.setattr(manager.executor, 'submit', Mock(side_effect=futures))
    manager.add_local_pdf('sample.pdf', 'first')
    manager.add_local_pdf('sample.pdf', 'second')
    module.repo.mark_job_cancelled_before_start.side_effect = [ValueError('synthetic-private'), True]
    manager.begin_shutdown()
    assert all(future.cancelled() for future in futures)
    assert manager.wait_for_idle(0)
    assert module.repo.mark_job_cancelled_before_start.call_count == 2
    text = capsys.readouterr().err
    assert 'INGESTION_CANCEL_STATE_FAILED' in text
    assert 'synthetic-private' not in text


def test_cancel_loses_to_execution_without_changing_job_state(manager, monkeypatch):
    future = Future()
    future.set_running_or_notify_cancel()
    monkeypatch.setattr(manager.executor, 'submit', Mock(return_value=future))
    manager.add_local_pdf('sample.pdf', 'request')
    manager.begin_shutdown()
    module.repo.mark_job_cancelled_before_start.assert_not_called()
    assert not manager.wait_for_idle(0)
    future.set_result(None)
    assert manager.wait_for_idle(0)


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
