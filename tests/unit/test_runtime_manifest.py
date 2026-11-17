"""외부 설치 없이 고정 wheel 입력과 runtime manifest 경계를 검사한다."""
from types import SimpleNamespace as NS

import pytest

from solo_leveling.workers import runtime_manifest as runtime

BASE = 'registry.example/approved@sha256:' + 'a' * 64


def bundle(tmp_path):
    path = tmp_path / 'inputs'
    path.mkdir()
    (path / 'synthetic.whl').write_bytes(b'synthetic-only')
    lines = [f'{name}==1.0 --hash=sha256:' + 'a' * 64 for name in sorted(runtime.REQUIRED_PACKAGES)]
    (path / 'requirements.lock').write_text('\n'.join(lines))
    return path


def test_exact_wheel_bundle_requires_pins_and_hashes(tmp_path):
    assert runtime.validate_inputs(bundle(tmp_path))


@pytest.mark.parametrize('change', ['url', 'index', 'nested', 'unpinned', 'nohash', 'duplicate', 'missing', 'secret', 'link'])
def test_invalid_dependency_bundle_rejected(tmp_path, change):
    root = bundle(tmp_path)
    lock = root / 'requirements.lock'
    extra = {'url': 'requests @ https://example.invalid/file.whl', 'index': '--extra-index-url https://example.invalid',
             'nested': '-r other.txt', 'unpinned': 'requests>=1', 'nohash': 'requests==1',
             'duplicate': 'requests==1 --hash=sha256:' + 'b' * 64}.get(change)
    if extra: lock.write_text(lock.read_text() + '\n' + extra)
    elif change == 'missing': lock.write_text('')
    elif change == 'secret': (root / '.env').write_text('synthetic')
    else:
        (root / 'synthetic.whl').unlink()
        (root / 'synthetic.whl').symlink_to(lock)
    with pytest.raises(ValueError): runtime.validate_inputs(root)


def distributions():
    return [NS(metadata={'Name': n}, version='1.0') for n in runtime.REQUIRED_PACKAGES]


def test_capture_all_installed_versions_without_importing_models(monkeypatch):
    monkeypatch.setattr(runtime.platform, 'system', lambda: 'Linux')
    rows = distributions() + [NS(metadata={'Name': 'Extra_Dep'}, version='2.0')]
    result = runtime.capture(BASE, distributions=rows)
    assert result['base_image'] == BASE and result['packages']['extra-dep'] == '2.0'
    assert len(result['packages']) == len(runtime.REQUIRED_PACKAGES) + 1


@pytest.mark.parametrize('change', ['os', 'image', 'missing', 'duplicate'])
def test_capture_rejects_wrong_or_incomplete_runtime(monkeypatch, change):
    monkeypatch.setattr(runtime.platform, 'system', lambda: 'Darwin' if change == 'os' else 'Linux')
    rows = distributions()
    if change == 'missing': rows.pop()
    if change == 'duplicate': rows.append(rows[0])
    with pytest.raises(ValueError): runtime.capture('python:latest' if change == 'image' else BASE, distributions=rows)


def test_cli_reports_fixed_error_without_partial_manifest(monkeypatch, capsys):
    monkeypatch.setattr(runtime.metadata, 'distributions', lambda: (_ for _ in ()).throw(RuntimeError('private-path')))
    assert runtime.main(['--image-reference', BASE]) == 1
    result = capsys.readouterr()
    assert result.out == '' and result.err == 'WORKER_RUNTIME_INVALID\n'
