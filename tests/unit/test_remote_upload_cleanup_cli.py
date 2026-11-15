from solo_leveling.interfaces.cli import remote_upload_cleanup


SETTINGS = {
    "DATABASE_URL": "postgresql://db/service",
    "OBJECT_STORAGE_ENDPOINT": "https://objects.example",
    "OBJECT_STORAGE_BUCKET": "private",
    "OBJECT_STORAGE_ACCESS_KEY_ID": "key",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
}


class Pool:
    closed = False

    def close(self):
        self.closed = True


def test_run_builds_scoped_cleanup_and_closes_pool(monkeypatch):
    pool = Pool()
    captured = {}

    class Service:
        def __init__(self, store, storage):
            captured.update(store=store, storage=storage)

        def cleanup_expired(self, *, limit):
            captured["limit"] = limit
            return {"claimed": 2, "deleted": 2, "failed": 0}

    monkeypatch.setattr(remote_upload_cleanup, "RemoteUploadVerificationService", Service)
    result = remote_upload_cleanup._run(
        SETTINGS, 7, pool_factory=lambda dsn: pool,
        storage_factory=lambda settings: "storage")
    assert result == {"claimed": 2, "deleted": 2, "failed": 0}
    assert captured["limit"] == 7
    assert captured["store"]._pool is pool
    assert pool.closed


def test_main_requires_explicit_apply_without_loading_settings(capsys):
    assert remote_upload_cleanup.main([]) == 2
    assert capsys.readouterr().out.strip() == '{"status": "apply_required"}'


def test_main_never_prints_configuration_detail(monkeypatch, capsys):
    monkeypatch.setattr(remote_upload_cleanup, "_run",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("private-dsn")))
    assert remote_upload_cleanup.main(["--apply"]) == 1
    output = capsys.readouterr().out
    assert "private-dsn" not in output
    assert "cleanup_unavailable" in output


def test_database_error_is_sanitized(monkeypatch, capsys):
    from psycopg import OperationalError

    def fail(*args):
        raise OperationalError('CANARY_PRIVATE_DATABASE')

    monkeypatch.setattr(remote_upload_cleanup, '_run', fail)
    assert remote_upload_cleanup.main(['--apply']) == 1
    captured = capsys.readouterr()
    assert 'cleanup_unavailable' in captured.out
    assert 'CANARY_PRIVATE_DATABASE' not in captured.out + captured.err


def test_invalid_limit_does_not_open_connections(monkeypatch, capsys):
    from unittest.mock import Mock

    run = Mock()
    monkeypatch.setattr(remote_upload_cleanup, '_run', run)
    for value in ('0', '-1', '1001'):
        assert remote_upload_cleanup.main(['--apply', '--limit', value]) == 1
    run.assert_not_called()


def test_pool_diagnostics_are_sanitized_and_filter_is_restored(monkeypatch, caplog):
    import logging

    logger = logging.getLogger('psycopg.pool')
    original_filters = list(logger.filters)

    def fail(*args):
        logger.warning('connection failed: %s', 'CANARY_PRIVATE_DATABASE')
        raise RuntimeError('CANARY_PRIVATE_DATABASE')

    monkeypatch.setattr(remote_upload_cleanup, '_run', fail)
    assert remote_upload_cleanup.main(['--apply']) == 1
    assert 'CANARY_PRIVATE_DATABASE' not in caplog.text
    assert logger.filters == original_filters
