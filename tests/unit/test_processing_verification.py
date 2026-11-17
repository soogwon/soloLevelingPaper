"""검증 명령은 기본 오프라인이며 잘못된 설정/의존성 출력을 노출하지 않는다."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
import logging

import pytest

from solo_leveling.workers.verification import VerificationConfig, IsolatedPool, run_once
from solo_leveling.interfaces.cli import processing_verification as cli


def config():
    return VerificationConfig('verification', 'verification_test', 'synthetic-owner',
        '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002',
        'v1', 'a' * 64, 'verification-worker', 60, 100_000)


@pytest.mark.parametrize('change', [dict(schema_name='public'), dict(schema_name='pg_temp'),
    dict(schema_name='bad,name'), dict(job_id='bad'), dict(payload_hash='x'), dict(lease_seconds=True),
    dict(lease_seconds=1), dict(lease_seconds=3601), dict(max_staging_bytes=0), dict(owner_user_id='')])
def test_config_rejects_invalid_scope(change):
    with pytest.raises(ValueError): replace(config(), **change)


def test_default_check_never_imports_runtime(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(asdict(config())))
    load = Mock(side_effect=AssertionError('외부 의존성을 읽으면 안 됨'))
    monkeypatch.setattr(cli.importlib, 'import_module', load)
    assert cli.main(['--config', str(path), '--runtime', 'unknown:factory']) == 0
    load.assert_not_called()
    assert json.loads(capsys.readouterr().out) == dict(status='config_valid', executed=False)


@pytest.mark.parametrize('raw', ['{}', '{"job_id":1,"job_id":2}', 'x' * 16_385])
def test_bad_file_returns_fixed_error(tmp_path, capsys, raw):
    path = tmp_path / 'config.json'
    path.write_text(raw)
    assert cli.main(['--config', str(path)]) == 1
    assert json.loads(capsys.readouterr().out) == dict(status='verification_failed')


@pytest.mark.parametrize('failure', ['none', 'run', 'close', 'interrupt'])
def test_runtime_cleanup_safe_output_and_logging_restoration(tmp_path, monkeypatch, capsys, failure):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(asdict(config())))
    closed = []
    @contextmanager
    def factory(c):
        assert c == config()
        try:
            print('비공개 원문 및 자격')
            yield 'resources'
        finally:
            closed.append(True)
            if failure == 'close': raise RuntimeError('비공개 DSN')
    def run(c, resource):
        assert resource == 'resources'
        logging.error('비공개 로그')
        if failure == 'run': raise RuntimeError('비공개 provider 응답')
        if failure == 'interrupt': raise KeyboardInterrupt()
        return 'published'
    monkeypatch.setattr(cli.importlib, 'import_module', lambda _: NS(factory=factory))
    monkeypatch.setattr(cli, 'run_once', run)
    previous = logging.root.manager.disable
    assert cli.main(['--config', str(path), '--execute', '--runtime', 'trusted:factory']) == (0 if failure == 'none' else 1)
    assert closed == [True] and logging.root.manager.disable == previous
    captured = capsys.readouterr()
    assert '비공개' not in captured.out + captured.err


def test_execute_without_runtime_does_not_run(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(asdict(config())))
    run = Mock()
    monkeypatch.setattr(cli, 'run_once', run)
    assert cli.main(['--config', str(path), '--execute']) == 1
    run.assert_not_called()


def test_each_connection_checks_full_search_path():
    conn = Mock()
    rows = [dict(db='verification', schema='verification_test', schemas=['verification_test'], resolved_schemas=['pg_catalog', 'verification_test']),
            dict(db='verification', schema='verification_test', schemas=['verification_test', 'public'], resolved_schemas=['pg_catalog', 'verification_test', 'public'])]
    conn.execute.return_value.fetchone.side_effect = rows
    @contextmanager
    def borrow(): yield conn
    pool = IsolatedPool(NS(connection=borrow), config())
    with pool.connection(): pass
    with pytest.raises(RuntimeError, match='database_mismatch'):
        with pool.connection(): pytest.fail('격리되지 않은 연결이 전달됨')


def test_runner_uses_scoped_queue_and_does_not_retry(monkeypatch):
    import solo_leveling.workers.processing_composition as composition
    worker = Mock()
    worker.run_once.return_value = NS(published='publication')
    factory = Mock(return_value=worker)
    monkeypatch.setattr(composition, 'create_processing_worker', factory)
    resources = NS(pool=object(), objects=object(), stage_options={})
    assert run_once(config(), resources) == 'published'
    worker.run_once.assert_called_once()
    assert factory.call_args.kwargs['jobs'].config == config()
    resources.stage_options['jobs'] = object()
    with pytest.raises(ValueError): run_once(config(), resources)


def test_temporary_schema_cannot_shadow_verification_tables():
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = dict(db='verification', schema='verification_test',
        schemas=['verification_test'], resolved_schemas=['pg_temp_3', 'pg_catalog', 'verification_test'])
    @contextmanager
    def borrow(): yield conn
    with pytest.raises(RuntimeError, match='database_mismatch'):
        with IsolatedPool(NS(connection=borrow), config()).connection(): pytest.fail('임시 schema가 허용됨')
