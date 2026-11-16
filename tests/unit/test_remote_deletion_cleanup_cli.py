import json
import logging
from unittest.mock import Mock

import pytest

from solo_leveling.interfaces.cli import remote_deletion_cleanup


SETTINGS = {
    "DATABASE_URL": "postgresql://db/service",
    "OBJECT_STORAGE_ENDPOINT": "https://objects.example",
    "OBJECT_STORAGE_BUCKET": "private",
    "OBJECT_STORAGE_ACCESS_KEY_ID": "key",
    "OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
}


@pytest.mark.parametrize("failed,expected_status,exit_code", [(0, "complete", 0), (1, "failed", 1)])
def test_main_reports_failure_consistently_with_exit_code(monkeypatch, capsys, failed, expected_status, exit_code):
    result = {"processed": 1, "completed": 1 - failed, "failed": failed, "idle": False}
    monkeypatch.setattr(remote_deletion_cleanup, "_run", lambda *_: result)
    assert remote_deletion_cleanup.main(["--apply"]) == exit_code
    assert json.loads(capsys.readouterr().out) == {"status": expected_status, **result}


class Pool:
    closed = False

    def close(self):
        self.closed = True


def test_run_processes_queue_until_idle_and_closes_pool(monkeypatch):
    pool = Pool()
    run_once = Mock(side_effect=[{"status": "complete"}, {"status": "complete"}, {"status": "idle"}])
    captured = {}

    class Service:
        def run_once(self):
            return run_once()

    def create(pool_arg, storage, *, worker_id, lease_seconds):
        captured.update(pool=pool_arg, storage=storage, worker_id=worker_id, lease_seconds=lease_seconds)
        return Service()

    monkeypatch.setattr(remote_deletion_cleanup, "create_remote_deletion_cleanup", create)
    result = remote_deletion_cleanup._run(
        SETTINGS, 10, 45, pool_factory=lambda dsn: pool, storage_factory=lambda settings: "storage")
    assert result == {"processed": 2, "completed": 2, "failed": 0, "idle": True}
    assert captured["pool"] is pool
    assert captured["storage"] == "storage"
    assert captured["worker_id"].startswith("deletion-")
    assert captured["lease_seconds"] == 45
    assert pool.closed


def test_run_stops_after_failure_instead_of_reclaiming_same_request(monkeypatch):
    pool = Pool()
    run_once = Mock(return_value={"status": "failed"})

    class Service:
        def run_once(self):
            return run_once()

    monkeypatch.setattr(remote_deletion_cleanup, "create_remote_deletion_cleanup", lambda *args, **kwargs: Service())
    result = remote_deletion_cleanup._run(
        SETTINGS, 100, 60, pool_factory=lambda dsn: pool, storage_factory=lambda settings: "storage")
    assert result == {"processed": 1, "completed": 0, "failed": 1, "idle": False}
    run_once.assert_called_once_with()
    assert pool.closed


def test_run_rejects_unknown_service_result_and_closes_pool(monkeypatch):
    pool = Pool()

    class Service:
        def run_once(self):
            return {"status": "unknown"}

    monkeypatch.setattr(remote_deletion_cleanup, "create_remote_deletion_cleanup", lambda *args, **kwargs: Service())
    with pytest.raises(RuntimeError, match="remote_deletion_cleanup_result_invalid"):
        remote_deletion_cleanup._run(
            SETTINGS, 1, 60, pool_factory=lambda dsn: pool, storage_factory=lambda settings: "storage")
    assert pool.closed


def test_main_requires_explicit_apply_without_loading_settings(monkeypatch, capsys):
    run = Mock()
    monkeypatch.setattr(remote_deletion_cleanup, "_run", run)
    assert remote_deletion_cleanup.main([]) == 2
    assert capsys.readouterr().out.strip() == '{"status": "apply_required"}'
    run.assert_not_called()


def test_main_rejects_limits_without_opening_connections(monkeypatch, capsys):
    run = Mock()
    monkeypatch.setattr(remote_deletion_cleanup, "_run", run)
    for args in (
        ["--apply", "--max-jobs", "0"],
        ["--apply", "--max-jobs", "1001"],
        ["--apply", "--lease-seconds", "29"],
        ["--apply", "--lease-seconds", "3601"],
    ):
        assert remote_deletion_cleanup.main(args) == 1
        assert "invalid_limits" in capsys.readouterr().out
    run.assert_not_called()


def test_main_sanitizes_configuration_and_pool_errors(monkeypatch, capsys, caplog):
    logger = logging.getLogger("psycopg.pool")
    original_filters = list(logger.filters)

    def fail(*args):
        logger.warning("connection failed: %s", "CANARY_PRIVATE_DATABASE")
        raise RuntimeError("CANARY_PRIVATE_DATABASE")

    monkeypatch.setattr(remote_deletion_cleanup, "_run", fail)
    assert remote_deletion_cleanup.main(["--apply"]) == 1
    captured = capsys.readouterr()
    assert "cleanup_unavailable" in captured.out
    assert "CANARY_PRIVATE_DATABASE" not in captured.out + captured.err + caplog.text
    assert logger.filters == original_filters
