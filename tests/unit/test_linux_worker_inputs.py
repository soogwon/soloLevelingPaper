"""실제 검증 준비의 입력 해시와 잘못된 artifact 거부를 확인한다."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

spec = importlib.util.spec_from_file_location('linux_worker', Path(__file__).resolve().parents[2] / 'scripts/verification/linux_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def test_wheel_lock_records_exact_downloaded_hash(tmp_path):
    wheel = tmp_path / 'package-1.0-py3-none-any.whl'
    with ZipFile(wheel, 'w') as archive:
        archive.writestr('package-1.0.dist-info/METADATA', 'Name: package\nVersion: 1.0\n')
    worker.wheel_lock(tmp_path)
    assert (tmp_path / 'requirements.lock').read_text() == f'package==1.0 --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n'


def test_no_wheels_rejected(tmp_path):
    with pytest.raises(ValueError): worker.wheel_lock(tmp_path)


@pytest.mark.parametrize('body', [b'ab', b'abc', b'abcde'])
def test_changed_model_rejected(tmp_path, monkeypatch, body):
    lock_dir = tmp_path / 'deploy/worker'
    lock_dir.mkdir(parents=True)
    lock = dict(model_id='synthetic/model', revision='a' * 40,
                files=[dict(path='config.json', size=3, sha256=hashlib.sha256(b'xyz').hexdigest())])
    (lock_dir / 'model-lock.json').write_text(json.dumps(lock))
    monkeypatch.setattr(worker, 'ROOT', tmp_path)
    monkeypatch.setattr(worker, 'urlopen', lambda *args, **kwargs: io.BytesIO(body))
    with pytest.raises(ValueError): worker.download_model(tmp_path / 'model')
