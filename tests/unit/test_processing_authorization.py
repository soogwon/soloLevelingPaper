"""실행별 권한 callback과 영속 원장 연결의 안전 경계를 검증한다."""
from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest

from solo_leveling.application.provider_dispatch import AttemptRequest
from solo_leveling.workers.processing_authorization import PostgresProcessingAuthorization
from solo_leveling.workers.remote_processing import (
    RemoteProcessingWorker, RemoteProcessingHeartbeatFailed, RemoteProcessingInput,
)
from solo_leveling.interfaces.http.object_storage import DownloadedObject
from tests.unit.test_remote_processing_worker import Connection, Pool, job, source, SHA


@pytest.mark.parametrize('blocked', [None, {'blocked': 1}])
def test_current_usage_is_checked_without_losing_source_identity(blocked):
    conn = Connection(({'input_hash': SHA}, blocked))
    auth = PostgresProcessingAuthorization(Pool(conn))
    assert auth.allowed(source(), 'embedding') is (blocked is None)
    assert conn.calls[0][1] == ('owner', 'job', 'lease', 'paper', 'version', SHA, 9, 'embedding')
    assert conn.calls[1][1] == ('owner', 'job')


def test_ledger_checks_worker_before_borrowing_connection():
    conn = Connection(({'input_hash': SHA, 'storage_consent_id': 'storage',
                        'ai_consent_id': 'ai', 'notice_version': 'v1'},))
    auth = PostgresProcessingAuthorization(Pool(conn))
    check = Mock(return_value=False)
    expected = auth.dispatch_intent(source())
    conn.rows.append({'input_hash': SHA, 'storage_consent_id': 'storage',
                      'ai_consent_id': 'ai', 'notice_version': 'v1'})
    ledger = auth.ledger(source(), check, policy_version='p', window_key='w')
    count = len(conn.calls)
    assert ledger.authorize(AttemptRequest(expected, 'key', 'translation', 100, 50)) is None
    assert len(conn.calls) == count
    check.assert_called_once_with(source(), 'translation')


def test_scoped_factory_checks_heartbeat_even_when_input_store_still_allows():
    attempted = Event()
    class Jobs:
        def claim(self, *args, **kwargs): return job()
        def heartbeat(self, *args, **kwargs):
            attempted.set()
            raise RuntimeError('private driver detail')
    inputs = Mock()
    inputs.load.return_value = RemoteProcessingInput(source(), 'key', 'application/pdf')
    inputs.allowed.return_value = True
    objects = Mock()
    objects.read_object.return_value = DownloadedObject('key', 9, 'application/pdf', SHA, True, b'%PDF-data')
    reached = []
    def factory(current, check):
        assert check(replace(current, job_id='other'), 'translation') is False
        class Stages:
            def run(self, current, pdf):
                assert attempted.wait(2)
                # thread의 오류 기록까지 완료하도록 종료 경계를 사용한다.
                from time import monotonic
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    try:
                        check(current, 'embedding')
                    except RemoteProcessingHeartbeatFailed:
                        reached.append('blocked')
                        raise
                pytest.fail('heartbeat 실패가 callback에 전달되지 않음')
        return Stages()
    with pytest.raises(RemoteProcessingHeartbeatFailed):
        RemoteProcessingWorker(Jobs(), inputs, objects, None, worker_id='worker',
            lease_seconds=1, heartbeat_interval_seconds=0.01, stages_factory=factory).run_once()
    assert reached == ['blocked']
