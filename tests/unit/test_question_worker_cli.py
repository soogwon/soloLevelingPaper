"""질문 실행 진입점은 명시 실행·상한·고정 진단을 강제한다."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from solo_leveling.interfaces.cli import question_worker


def test_default_never_imports_runtime(monkeypatch):
    load = Mock(); monkeypatch.setattr(question_worker.importlib, 'import_module', load)
    assert question_worker.main([]) == 2
    load.assert_not_called()


def test_runtime_closed_and_limit_enforced(monkeypatch, capsys):
    runner = Mock(); runner.run_once.return_value = 'finished'
    closed = []
    @contextmanager
    def factory():
        try:
            print('비공개 입력')
            yield runner
        finally:
            closed.append(True)
    monkeypatch.setattr(question_worker.importlib, 'import_module', lambda _: SimpleNamespace(factory=factory))
    assert question_worker.main(['--execute', '--runtime', 'approved:factory', '--max-jobs', '2']) == 0
    assert runner.run_once.call_count == 2 and closed == [True]
    assert '비공개' not in capsys.readouterr().out


def test_factory_failure_hides_details(monkeypatch, capsys):
    load = Mock(side_effect=RuntimeError('비밀 DSN'))
    monkeypatch.setattr(question_worker.importlib, 'import_module', load)
    assert question_worker.main(['--execute', '--runtime', 'approved:factory']) == 1
    assert '비밀' not in capsys.readouterr().out


def test_execution_runtime_closes_pool_on_composition_failure(monkeypatch):
    from solo_leveling.workers.question_runtime import execution_runtime
    import pytest
    pool=Mock()
    with pytest.raises(ValueError):
        with execution_runtime(lambda: pool, generation=None,semantic=None,provider_profile_id='',embedding_runtime=None,window_key='',token_upper_bound=None):
            pytest.fail('설정 실패 시 실행 금지')
    pool.close.assert_called_once()
