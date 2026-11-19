"""요약 실행 opt-in·오류 출력의 필수 경계."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock
from threading import Event
import pytest
from solo_leveling.interfaces.cli import summary_worker
from solo_leveling.workers.summary_runtime import build


def test_cli_requires_execute_and_never_imports_runtime(monkeypatch,capsys):
    factory=Mock();monkeypatch.setattr(summary_worker,'factory',factory)
    assert summary_worker.main([])==2
    factory.assert_not_called()
    assert 'execute_required' in capsys.readouterr().out


def test_cli_stops_after_failure_and_hides_raw_error(monkeypatch,capsys):
    @contextmanager
    def fail():
        print('synthetic-secret')
        raise ValueError('synthetic-secret')
        yield
    monkeypatch.setattr(summary_worker,'factory',fail)
    assert summary_worker.main(['--execute'])==1
    text=capsys.readouterr().out
    assert 'summary_worker_failed' in text and 'synthetic-secret' not in text


def test_queue_cancel_is_terminal_and_failure_does_not_retry():
    runner=NS(run_once=Mock(side_effect=['cancelled','finished']))
    result=summary_worker.run_queue(runner,Event(),max_jobs=2)
    assert result.status=='limit_reached' and result.processed==2
    runner.run_once=Mock(return_value='failed')
    assert summary_worker.run_queue(runner,Event(),max_jobs=2).status=='failed'
    assert runner.run_once.call_count==1


def test_disabled_build_never_connects():
    pool=Mock()
    with pytest.raises(ValueError):
        with build(None,{},pool_factory=pool):pass
    pool.assert_not_called()


def test_bootstrap_closes_pool_on_wrong_identity():
    plan=NS(raw={'database_role':'summary_worker','database_name':'test_db','schema_name':'summary_test'})
    pool=Mock();connection=Mock()
    pool.connection.return_value.__enter__=Mock(return_value=connection)
    pool.connection.return_value.__exit__=Mock(return_value=False)
    connection.execute.return_value.fetchone.return_value={'database':'wrong','schema':'summary_test'}
    source={'SUMMARY_WORKER_EXECUTION_ENABLED':'true','SUMMARY_WORKER_DATABASE_URL':'postgresql://summary_worker:synthetic@127.0.0.1/test_db','SUMMARY_WORKER_OPENAI_API_KEY':'synthetic'}
    with pytest.raises(ValueError):
        with build(plan,source,pool_factory=lambda *_:pool):pass
    pool.close.assert_called_once()


def test_bootstrap_rejects_admin_role_flags():
    plan=NS(raw={'database_role':'summary_worker','database_name':'test_db','schema_name':'summary_test'})
    pool=Mock();connection=Mock()
    pool.connection.return_value.__enter__=Mock(return_value=connection)
    pool.connection.return_value.__exit__=Mock(return_value=False)
    connection.execute.return_value.fetchone.side_effect=[{'database':'test_db','schema':'summary_test'},
        dict(rolname='summary_worker',rolsuper=True,rolcreatedb=False,rolcreaterole=False,rolbypassrls=False,rolinherit=False)]
    source={'SUMMARY_WORKER_EXECUTION_ENABLED':'true','SUMMARY_WORKER_DATABASE_URL':'postgresql://summary_worker:synthetic@127.0.0.1/test_db','SUMMARY_WORKER_OPENAI_API_KEY':'synthetic'}
    with pytest.raises(ValueError):
        with build(plan,source,pool_factory=lambda *_:pool):pass
    pool.close.assert_called_once()
