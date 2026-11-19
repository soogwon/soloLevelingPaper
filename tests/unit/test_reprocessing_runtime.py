"""실행 전 설정 차단과 안전한 단발 명령 계약."""
from contextlib import contextmanager
import json
import pytest
from solo_leveling.workers.reprocessing_runtime import build,load_plan,objects_for
from solo_leveling.interfaces.cli import reprocessing_worker as cli


def test_disabled_build_does_not_connect():
    with pytest.raises(ValueError),build({}, {},pool_factory=lambda *_:pytest.fail('연결 금지')):pass


@pytest.mark.parametrize('dsn',[
    'postgresql://worker:synthetic@remote.test/db?sslmode=require',
    'postgresql://admin:synthetic@localhost/db',
    'postgresql://worker:synthetic@localhost/other',
    'postgresql://worker:synthetic@localhost/db?sslmode=disable&options=bad',
])
def test_wrong_identity_or_tls_rejected_before_connect(dsn):
    plan=dict(database_role='worker',database_name='db',schema_name='test')
    with pytest.raises(ValueError),build(plan,{'REPROCESSING_WORKER_EXECUTION_ENABLED':'true','REPROCESSING_WORKER_DATABASE_URL':dsn},
        pool_factory=lambda *_:pytest.fail('연결 금지')):pass


def test_duplicate_plan_keys_are_rejected(tmp_path):
    path=tmp_path/'plan.json';path.write_text('{"schema_name":"one","schema_name":"two"}')
    with pytest.raises(ValueError):load_plan(path)


@pytest.mark.parametrize('endpoint',['http://bad.test','https://example.com','https://user@'+'a'*32+'.r2.cloudflarestorage.com'])
def test_non_r2_endpoint_rejected(endpoint):
    with pytest.raises(ValueError):objects_for({'r2':dict(endpoint=endpoint,bucket='test',region='auto')},{})


def test_cli_no_execute_never_builds(monkeypatch,capsys):
    monkeypatch.setattr(cli,'factory',lambda:pytest.fail('실행 금지'))
    assert cli.main([])==2
    assert json.loads(capsys.readouterr().out)=={'status':'execute_required'}


def test_cli_suppresses_raw_error_and_stops_after_one(monkeypatch,capsys):
    calls=[]
    @contextmanager
    def factory():
        class Worker:
            def run_once(self):calls.append(1);print('비밀 원문');raise RuntimeError('비밀 자격')
        yield Worker()
    monkeypatch.setattr(cli,'factory',factory)
    assert cli.main(['--execute'])==1 and calls==[1]
    output=capsys.readouterr()
    assert json.loads(output.out)=={'status':'reprocessing_worker_failed'} and not output.err
