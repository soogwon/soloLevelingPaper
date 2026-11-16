from pathlib import Path


def test_free_staging_runs_bounded_deletion_cleanup_after_migrations():
    script = Path("deploy/render-start.sh").read_text(encoding="utf-8")
    migration = script.index("solo-leveling-server-migrate")
    gate = script.index('REMOTE_DELETION_CLEANUP_ON_START:-false')
    cleanup = script.index("solo-leveling-remote-deletion-cleanup")
    api = script.index("exec solo-leveling-api")

    assert migration < gate < cleanup < api
    assert '--max-jobs "${REMOTE_DELETION_CLEANUP_MAX_JOBS:-10}"' in script
    assert '--lease-seconds "${REMOTE_DELETION_CLEANUP_LEASE_SECONDS:-300}"' in script
    assert '"status":"failed"' in script


def test_render_staging_explicitly_enables_bounded_startup_cleanup():
    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    assert "REMOTE_DELETION_CLEANUP_ON_START\n        value: \"true\"" in blueprint
    assert "REMOTE_DELETION_CLEANUP_MAX_JOBS\n        value: \"10\"" in blueprint
    assert "REMOTE_DELETION_CLEANUP_LEASE_SECONDS\n        value: \"300\"" in blueprint
