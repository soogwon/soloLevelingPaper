"""서지 정보 채우기 관리자 명령은 설정 누락·잘못된 한도에서 접속 정보 없이 고정 상태만 출력한다."""
import json

from solo_leveling.interfaces.cli import paper_front_matter_backfill as cli


def test_invalid_limit(capsys):
    assert cli.main(['--limit', '0']) == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'invalid_limit'}


def test_missing_settings_are_reported_without_details(capsys, monkeypatch):
    monkeypatch.setattr(cli, 'environ', {'DATABASE_URL': 'postgresql://secret@host/db'})
    assert cli.main([]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {'status': 'backfill_unavailable'} and 'secret' not in out


def test_preview_is_default_and_apply_is_explicit(capsys, monkeypatch):
    calls = []

    def fake_run(source, limit, apply):
        calls.append((limit, apply))
        return dict(checked=1, updated=0, would_update=1, not_found=0, unchanged=0, failed=0)

    monkeypatch.setattr(cli, '_run', fake_run)
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'preview'
    assert cli.main(['--apply', '--limit', '5']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'complete'
    assert calls == [(200, False), (5, True)]
