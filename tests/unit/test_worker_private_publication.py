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


@pytest.mark.parametrize('code', [404, 403, 500])
def test_missing_or_failed_visibility_lookup_blocks(code, monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    def fail(*args, **kwargs): raise HTTPError('synthetic', code, 'error', {}, None)
    monkeypatch.setattr(publish, 'urlopen', fail)
    with pytest.raises(RuntimeError): publish.private_package(publish.NAMES[0])


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


@pytest.mark.parametrize('missing_name', publish.NAMES)
def test_missing_package_blocks_before_build_or_push(missing_name, monkeypatch, tmp_path):
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    monkeypatch.setattr(publish.sys, 'argv', ['publish', '--work-dir', str(tmp_path / 'release'),
        '--tag', 'verified-' + 'a' * 40, '--publish'])
    def lookup(request, **kwargs):
        if request.full_url.endswith('/' + missing_name):
            raise HTTPError('synthetic', 404, 'missing', {}, None)
        return io.BytesIO(b'{"visibility":"private"}')
    monkeypatch.setattr(publish, 'urlopen', lookup)
    monkeypatch.setattr(publish, 'run', lambda *a, **kw: pytest.fail('빌드 또는 push 실행'))
    with pytest.raises(RuntimeError, match='미리 생성'): publish.main()
    assert not (tmp_path / 'release').exists()


def test_empty_initializer_has_no_payload_or_followup(monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    def missing(*args, **kwargs): raise HTTPError('synthetic', 404, 'missing', {}, None)
    monkeypatch.setattr(publish, 'urlopen', missing)
    builds, pushes = [], []
    monkeypatch.setattr(publish.subprocess, 'run', lambda args, **kw: builds.append((args, kw)))
    monkeypatch.setattr(publish, 'run', lambda args: pushes.append(args))
    publish.initialize_empty_worker()
    assert len(builds) == len(pushes) == 1
    args, kwargs = builds[0]
    assert args[-1] == '-'
    assert kwargs['input'].splitlines()[0] == 'FROM scratch'
    assert all(line.startswith(('FROM ', 'LABEL ')) for line in kwargs['input'].splitlines())
    assert pushes == [['docker', 'push', 'ghcr.io/soogwon/solo-leveling-worker:empty-initialization']]


@pytest.mark.parametrize('existing', ['private', 'public'])
def test_empty_initializer_never_overwrites_existing_package(existing, monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'synthetic')
    monkeypatch.setattr(publish, 'urlopen', lambda *a, **kw: io.BytesIO(json.dumps({'visibility': existing}).encode()))
    monkeypatch.setattr(publish.subprocess, 'run', lambda *a, **kw: pytest.fail('기존 package 덮어쓰기'))
    monkeypatch.setattr(publish, 'run', lambda *a, **kw: pytest.fail('push 발생'))
    if existing == 'private': publish.initialize_empty_worker()
    else:
        with pytest.raises(RuntimeError): publish.initialize_empty_worker()
