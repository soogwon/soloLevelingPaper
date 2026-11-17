"""외부 게시 전 비공개 범위와 명시적 실행 경계를 검사한다."""
import io
import json
from urllib.error import HTTPError

import pytest
from scripts.verification import publish_worker as publish


@pytest.mark.parametrize('visibility', ['public', 'internal', None])
def test_non_private_package_blocks(visibility, monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    monkeypatch.setattr(publish, 'urlopen', lambda *a, **kw: io.BytesIO(json.dumps({'visibility': visibility}).encode()))
    with pytest.raises(RuntimeError, match='private'): publish.private_package(publish.NAMES[0])


@pytest.mark.parametrize('code,allowed', [(404,True),(404,False),(403,True),(500,True)])
def test_missing_or_failed_visibility_lookup(code, allowed, monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    def fail(*args, **kwargs): raise HTTPError('synthetic',code,'error',{},None)
    monkeypatch.setattr(publish, 'urlopen', fail)
    if code == 404 and allowed: publish.private_package(publish.NAMES[0], allow_missing=True)
    else:
        with pytest.raises(RuntimeError): publish.private_package(publish.NAMES[0], allow_missing=allowed)


def test_private_package_allowed(monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    monkeypatch.setattr(publish, 'urlopen', lambda *a, **kw: io.BytesIO(b'{"visibility":"private"}'))
    publish.private_package(publish.NAMES[1])


def test_unapproved_package_rejected_before_network(monkeypatch):
    monkeypatch.setattr(publish, 'urlopen', lambda *a, **kw: pytest.fail('외부 조회 발생'))
    with pytest.raises(ValueError): publish.private_package('other')


def test_publish_flag_required_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(publish.sys, 'argv', ['publish', '--work-dir', str(tmp_path), '--tag', 'verified-'+'a'*40])
    monkeypatch.setattr(publish, 'private_package', lambda *a, **kw: pytest.fail('외부 조회 발생'))
    with pytest.raises(SystemExit): publish.main()
