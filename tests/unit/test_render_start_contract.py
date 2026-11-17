from pathlib import Path
import os
import shutil
import subprocess

import pytest


def test_free_staging_runs_bounded_cleanups_after_migrations():
    script = Path("deploy/render-start.sh").read_text(encoding="utf-8")
    migration = script.index("solo-leveling-server-migrate")
    upload_gate = script.index('REMOTE_UPLOAD_CLEANUP_ON_START:-false')
    upload_cleanup = script.index("solo-leveling-remote-upload-cleanup")
    gate = script.index('REMOTE_DELETION_CLEANUP_ON_START:-false')
    cleanup = script.index("solo-leveling-remote-deletion-cleanup")
    api = script.index("exec solo-leveling-api")

    assert migration < upload_gate < upload_cleanup < gate < cleanup < api
    assert '--limit "${REMOTE_UPLOAD_CLEANUP_LIMIT:-100}"' in script
    assert '"event":"remote_upload_cleanup_on_start","status":"failed"' in script
    assert '--max-jobs "${REMOTE_DELETION_CLEANUP_MAX_JOBS:-10}"' in script
    assert '--lease-seconds "${REMOTE_DELETION_CLEANUP_LEASE_SECONDS:-300}"' in script
    assert '"status":"failed"' in script


def test_render_staging_preserves_limited_web_credentials_and_disables_cleanup():
    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    assert "fromDatabase:" not in blueprint
    assert "DATABASE_URL\n        sync: false" in blueprint
    assert "SERVER_MIGRATION_MODE\n        value: check" in blueprint
    assert "REMOTE_UPLOAD_CLEANUP_ON_START\n        value: \"false\"" in blueprint
    assert "REMOTE_UPLOAD_CLEANUP_LIMIT\n        value: \"100\"" in blueprint
    assert "REMOTE_DELETION_CLEANUP_ON_START\n        value: \"false\"" in blueprint
    assert "REMOTE_DELETION_CLEANUP_MAX_JOBS\n        value: \"10\"" in blueprint
    assert "REMOTE_DELETION_CLEANUP_LEASE_SECONDS\n        value: \"300\"" in blueprint


def test_deployment_image_contains_every_startup_cleanup_entrypoint():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "src/solo_leveling/interfaces/cli/remote_upload_cleanup.py" in dockerfile
    assert "src/solo_leveling/interfaces/cli/remote_deletion_cleanup.py" in dockerfile


@pytest.mark.parametrize(
    ("enabled", "migration_exit", "upload_exit", "cleanup_exit", "expected", "exit_code"),
    [
        ("false", 0, 0, 0, ["migration", "api"], 0),
        ("true", 0, 0, 0, [
            "migration", "upload-cleanup --apply --limit 100",
            "cleanup --apply --max-jobs 10 --lease-seconds 300", "api",
        ], 0),
        ("true", 0, 1, 0, [
            "migration", "upload-cleanup --apply --limit 100",
            "cleanup --apply --max-jobs 10 --lease-seconds 300", "api",
        ], 0),
        ("true", 0, 0, 1, [
            "migration", "upload-cleanup --apply --limit 100",
            "cleanup --apply --max-jobs 10 --lease-seconds 300", "api",
        ], 0),
        ("true", 1, 0, 0, ["migration"], 1),
    ],
)
def test_startup_gate_and_failure_behavior(
    enabled, migration_exit, upload_exit, cleanup_exit, expected, exit_code,
):
    shell = shutil.which("bash")
    if shell is None and Path("C:/Program Files/Git/bin/bash.exe").is_file():
        shell = "C:/Program Files/Git/bin/bash.exe"
    if shell is None:
        pytest.skip("시작 스크립트 실행 검증에 bash가 필요합니다.")
    # 외부 명령만 대체하고 저장소의 시작 스크립트와 set -e 동작은 그대로 실행한다.
    harness = '''
solo-leveling-server-migrate() { echo migration; return "$MIGRATION_EXIT"; }
solo-leveling-remote-upload-cleanup() { echo "upload-cleanup $*"; return "$UPLOAD_CLEANUP_EXIT"; }
solo-leveling-remote-deletion-cleanup() { echo "cleanup $*"; return "$CLEANUP_EXIT"; }
exec() { test "$1" = solo-leveling-api; echo api; }
'''
    harness += Path("deploy/render-start.sh").read_text(encoding="utf-8")
    env = dict(os.environ)
    env.update(SERVER_MIGRATION_MODE="apply", REMOTE_DELETION_CLEANUP_ON_START=enabled,
               REMOTE_UPLOAD_CLEANUP_ON_START=enabled,
               REMOTE_UPLOAD_CLEANUP_LIMIT="100",
               REMOTE_DELETION_CLEANUP_MAX_JOBS="10",
               REMOTE_DELETION_CLEANUP_LEASE_SECONDS="300",
               MIGRATION_EXIT=str(migration_exit), UPLOAD_CLEANUP_EXIT=str(upload_exit),
               CLEANUP_EXIT=str(cleanup_exit))
    result = subprocess.run([shell, "-c", harness], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == exit_code
    assert result.stdout.splitlines() == expected
    expected_failures = int(enabled == "true" and migration_exit == 0 and upload_exit != 0)
    expected_failures += int(enabled == "true" and migration_exit == 0 and cleanup_exit != 0)
    assert result.stderr.count('"status":"failed"') == expected_failures


@pytest.mark.parametrize(('mode', 'status', 'expected', 'exit_code'), [
    ('check', 0, ['migration --check', 'api'], 0),
    ('check', 2, ['migration --check'], 2),
    ('check', 1, ['migration --check'], 1),
    ('skip', 0, [], 1),
])
def test_separate_migration_check_blocks_start_on_pending_or_failure(mode, status, expected, exit_code):
    shell = shutil.which('bash')
    if shell is None:
        pytest.skip('bash가 필요합니다.')
    harness = '''
solo-leveling-server-migrate() { echo "migration $*"; return "$MIGRATION_EXIT"; }
exec() { echo api; }
'''
    env = dict(os.environ, SERVER_MIGRATION_MODE=mode, MIGRATION_EXIT=str(status),
               REMOTE_UPLOAD_CLEANUP_ON_START='false', REMOTE_DELETION_CLEANUP_ON_START='false')
    result = subprocess.run([shell, '-c', harness + Path('deploy/render-start.sh').read_text()],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == exit_code
    assert result.stdout.splitlines() == expected
