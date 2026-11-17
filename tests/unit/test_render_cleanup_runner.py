"""정리 계획의 무접근과 승인·실패 경계를 대역으로 검증한다."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("cleanup_runner", "deploy/cleanup/run.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def settings():
    return dict(APP_ENV="staging", SLP_CLEANUP_APPROVED="true",
                SLP_CLEANUP_DATABASE_URL="test-cleanup-dsn", DATABASE_URL="web-must-not-be-used",
                OBJECT_STORAGE_ENDPOINT="test", OBJECT_STORAGE_BUCKET="test",
                OBJECT_STORAGE_ACCESS_KEY_ID="test", OBJECT_STORAGE_SECRET_ACCESS_KEY="test")


def test_plan_does_not_read_settings_or_run(capsys):
    class Forbidden(dict):
        def get(self, *args):
            raise AssertionError("자격 접근 금지")
    def runner(*args, **kwargs):
        raise AssertionError("명령 실행 금지")
    assert module.main(["uploads"], source=Forbidden(), runner=runner) == 0
    assert '"status": "plan_only"' in capsys.readouterr().out


@pytest.mark.parametrize("key,value", [("APP_ENV", "production"), ("SLP_CLEANUP_APPROVED", "false"),
                                       ("SLP_CLEANUP_DATABASE_URL", ""), ("OBJECT_STORAGE_SECRET_ACCESS_KEY", "")])
def test_missing_approval_or_cleanup_credentials_blocks_execution(key, value):
    env = settings(); env[key] = value
    assert module.main(["uploads", "--apply"], source=env,
                       runner=lambda *a, **k: pytest.fail("실행되면 안 됨")) == 2


@pytest.mark.parametrize("target", ["uploads", "deletions"])
def test_checks_migrations_and_runs_only_selected_bounded_command(target):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        assert kwargs['env']['DATABASE_URL'] == 'test-cleanup-dsn'
        assert kwargs['timeout'] == 600
        assert kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
        return SimpleNamespace(returncode=0)
    assert module.main([target, "--apply"], source=settings(), runner=runner) == 0
    assert calls == [['solo-leveling-server-migrate', '--check'], module.COMMANDS[target]]


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_failure_stops_without_retry_or_secret_output(failure, capsys):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('private-secret', 600)
        if failure == 'missing':
            raise OSError('private-secret')
        return SimpleNamespace(returncode=1)
    assert module.main(['deletions', '--apply'], source=settings(), runner=runner) == 1
    assert len(calls) == 1
    assert 'private-secret' not in capsys.readouterr().out


def test_server_image_contains_separate_runner():
    assert 'COPY deploy/cleanup/run.py ./deploy/cleanup/run.py' in Path('Dockerfile').read_text()


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_cleanup_failure_after_successful_check_does_not_retry(failure, capsys):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private-secret", 600)
        if failure == "missing":
            raise OSError("private-secret")
        return SimpleNamespace(returncode=1)

    assert module.main(["uploads", "--apply"], source=settings(), runner=runner) == 1
    assert len(calls) == 2
    output = capsys.readouterr().out
    assert "private-secret" not in output
    assert '"status": "complete"' not in output
