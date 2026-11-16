from pathlib import Path


def test_render_image_contains_remote_deletion_worker_import_tree():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    required = (
        "src/solo_leveling/domain/deletion_policy.py",
        "src/solo_leveling/infrastructure/__init__.py",
        "src/solo_leveling/infrastructure/storage/__init__.py",
        "src/solo_leveling/infrastructure/storage/remote_deletion_backend.py",
        "src/solo_leveling/interfaces/cli/remote_deletion_cleanup.py",
    )
    for source in required:
        assert source in dockerfile


def test_render_image_ci_imports_remote_deletion_command():
    workflow = Path(".github/workflows/render-image.yml").read_text(encoding="utf-8")
    assert "src/solo_leveling/interfaces/cli/remote_deletion_cleanup.py" in workflow
    assert "src/solo_leveling/infrastructure/storage/remote_deletion_backend.py" in workflow
    assert "solo-leveling-remote-deletion-cleanup --help" in workflow
