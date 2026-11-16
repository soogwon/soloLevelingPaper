"""합성 파일만으로 이미지 입력 거부와 실제 모델 잠금 파일의 일치를 검사한다."""
import hashlib
import json
from pathlib import Path
import platform
from unittest.mock import Mock

import pytest

from solo_leveling.workers.image_preflight import artifact_check, runtime_check, REQUIRED_PACKAGES, read_json

REVISION = 'a' * 40
BASE = 'python@sha256:' + 'a' * 64


def synthetic_bundle(root):
    folder = root / REVISION
    folder.mkdir()
    files = []
    hashes = []
    for name, data in [('config.json', b'{}'), ('1_Pooling/config.json', b'{}'),
                       ('model.safetensors', b'synthetic-only')]:
        target = folder / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        files.append(dict(path=name, size=len(data), sha256=sha))
        hashes.append((name, sha))
    digest = hashlib.sha256(json.dumps(sorted(hashes), separators=(',', ':')).encode()).hexdigest()
    return folder, dict(schema_version=1, revision=REVISION, files=files, artifact_sha256=digest)


def test_exact_synthetic_artifact_and_approved_manifest(tmp_path):
    folder, lock = synthetic_bundle(tmp_path)
    artifact_check(folder, lock)
    approved = read_json(Path('evaluation/embedding/approved-preflight-20261116.json'))['model']
    actual = read_json(Path('deploy/worker/model-lock.json'))
    assert actual['revision'] == approved['snapshot']
    assert actual['artifact_sha256'] == approved['pytorch_artifact_sha256']
    assert [(v['path'], v['size'], v['sha256']) for v in actual['files']] == [tuple(v) for v in approved['files']]


@pytest.mark.parametrize('change', ['modified', 'missing', 'extra', 'duplicate', 'traversal', 'absolute',
                                    'backslash', 'link', 'directory-link', 'digest', 'revision'])
def test_invalid_bundle_is_rejected(tmp_path, change):
    folder, lock = synthetic_bundle(tmp_path)
    if change == 'modified': (folder / 'config.json').write_bytes(b'[]')
    elif change == 'missing': (folder / 'config.json').unlink()
    elif change == 'extra': (folder / '.env').write_text('synthetic')
    elif change == 'duplicate': lock['files'].append(lock['files'][0])
    elif change == 'traversal': lock['files'][0]['path'] = '../config.json'
    elif change == 'absolute': lock['files'][0]['path'] = '/config.json'
    elif change == 'backslash': lock['files'][0]['path'] = 'a\\config.json'
    elif change == 'link':
        target = tmp_path / 'outside'
        target.write_bytes(b'{}')
        (folder / 'config.json').unlink()
        (folder / 'config.json').symlink_to(target)
    elif change == 'directory-link': (folder / 'extra').symlink_to(tmp_path, target_is_directory=True)
    elif change == 'digest': lock['artifact_sha256'] = 'b' * 64
    else: lock['revision'] = 'b' * 40
    with pytest.raises((ValueError, OSError)):
        artifact_check(folder, lock)


def runtime_lock():
    return dict(schema_version=1, base_image=BASE, system='Linux', machine=platform.machine(),
                python=platform.python_version(), packages={n: '1.2.3' for n in REQUIRED_PACKAGES})


def test_runtime_metadata_only_does_not_load_model(monkeypatch):
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    get = Mock(return_value='1.2.3')
    runtime_check(runtime_lock(), BASE, get_version=get)
    assert {call.args[0] for call in get.call_args_list} == REQUIRED_PACKAGES


@pytest.mark.parametrize('change', ['tag', 'base', 'os', 'machine', 'python', 'missing', 'version'])
def test_unfrozen_or_mismatched_runtime_rejected(monkeypatch, change):
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    lock, base = runtime_lock(), BASE
    if change == 'tag': base = 'python:3.12-slim'
    elif change == 'base': lock['base_image'] = 'python@sha256:' + 'b' * 64
    elif change == 'os': lock['system'] = 'Darwin'
    elif change == 'machine': lock['machine'] = 'wrong'
    elif change == 'python': lock['python'] = '0'
    elif change == 'missing': del lock['packages']['torch']
    get = Mock(return_value='0' if change == 'version' else '1.2.3')
    with pytest.raises(ValueError): runtime_check(lock, base, get_version=get)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / 'lock.json'
    path.write_text('{"revision": "a", "revision": "b"}')
    with pytest.raises(ValueError): read_json(path)
