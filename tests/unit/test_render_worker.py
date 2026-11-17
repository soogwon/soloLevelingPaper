"""합성 자식 프로세스로 Render 수명과 출력 차단을 검증한다."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from solo_leveling.workers import render_service


def test_default_mode_never_constructs_external_runtime(monkeypatch):
    captured = {}
    def run(stop, command, **kwargs):
        captured.update(command=command, **kwargs)
        return 0
    monkeypatch.setattr(render_service, 'supervise', run)
    assert render_service.main([]) == 0
    assert captured['command'] is None
    assert captured['preflight'][-1] == '/opt/worker/image_preflight.py'


@pytest.mark.parametrize('args', [ ['--mode', 'verify-once'],
    ['--mode', 'verify-once', '--config', 'relative.json'], ['--config', '/secure/config.json']])
def test_invalid_mode_config_rejected(args):
    with pytest.raises(SystemExit): render_service.main(args)


def test_verify_once_uses_fixed_factory(monkeypatch):
    captured = []
    monkeypatch.setattr(render_service, 'supervise', lambda stop, command, **kw: captured.extend(command) or 0)
    assert render_service.main(['--mode', 'verify-once', '--config', '/secure/config.json']) == 0
    assert captured[-1] == 'solo_leveling.workers.verification_bootstrap:factory'
    assert '--execute' in captured


def test_failed_preflight_never_runs_job(tmp_path, capsys):
    marker = tmp_path / 'called'
    assert render_service.supervise(threading.Event(), [sys.executable, '-c', f'open({str(marker)!r},"w").close()'],
        preflight=[sys.executable, '-c', 'print("SECRET"); raise SystemExit(1)']) == 1
    assert not marker.exists()
    assert 'SECRET' not in capsys.readouterr().out


@pytest.mark.parametrize('exit_code', [0, 1, 3])
def test_completed_child_not_repeated_and_output_hidden(tmp_path, capsys, exit_code):
    marker = tmp_path / 'count'
    stop = threading.Event()
    timer = threading.Timer(0.4, stop.set)
    timer.start()
    try:
        assert render_service.supervise(stop, [sys.executable, '-c',
            f'open({str(marker)!r}, "a").write("once"); print("SECRET"); raise SystemExit({exit_code})'],
            poll_seconds=0.01) == 0
    finally:
        timer.cancel()
    assert marker.read_text() == 'once'
    output = capsys.readouterr().out
    assert 'SECRET' not in output
    assert ('render_verification_finished' if exit_code == 0 else 'render_verification_failed') in output


@pytest.mark.skipif(os.name != 'posix', reason='Render POSIX 프로세스 그룹 계약')
@pytest.mark.parametrize('during_preflight', [False, True])
def test_stop_kills_unresponsive_child_and_reaps(tmp_path, during_preflight):
    marker = tmp_path / 'pid'
    code = f'import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open({str(marker)!r},"w").write(str(os.getpid())); time.sleep(30)'
    command = [sys.executable, '-c', code]
    stop = threading.Event()
    thread = threading.Thread(target=render_service.supervise, args=(stop, None if during_preflight else command),
        kwargs={'preflight': command if during_preflight else None, 'poll_seconds': 0.01, 'grace_seconds': 0.05})
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while (not marker.exists() or not marker.read_text()) and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = int(marker.read_text())
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    with pytest.raises(ProcessLookupError): os.kill(pid, 0)


def test_preexisting_stop_never_claims(tmp_path):
    stop = threading.Event()
    stop.set()
    assert render_service.supervise(stop, ['does-not-exist']) == 0


def test_build_plan_is_amd64_offline_and_no_publish(tmp_path):
    path = Path(__file__).resolve().parents[2] / 'deploy/worker/build_render.py'
    spec = importlib.util.spec_from_file_location('render_build', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'runtime-lock.json').write_text('{}')
    commands = module.commands('approved@sha256:' + 'a' * 64, tmp_path, tmp_path, 'local:test', path.parents[2])
    assert '--load' in commands[0] and 'linux/amd64' in commands[0] and 'render' in commands[0]
    assert '--network=none' in commands[1]
    assert all('--push' not in cmd and 'push' not in cmd for cmd in commands)
    with pytest.raises(ValueError): module.commands('image:latest', tmp_path, tmp_path, 'local:test', path.parents[2])


@pytest.mark.skipif(os.name != 'posix', reason='Render POSIX 종료 신호 계약')
@pytest.mark.parametrize('sig', [signal.SIGTERM, signal.SIGINT])
def test_real_service_signal_stops_standby(tmp_path, sig):
    output = tmp_path / 'output'
    code = '''import sys
from solo_leveling.workers import render_service as r
original = r.subprocess.Popen
def synthetic_preflight(args, **kw):
    assert args[-1] == '/opt/worker/image_preflight.py'
    return original([sys.executable, '-c', 'pass'], **kw)
r.subprocess.Popen = synthetic_preflight
raise SystemExit(r.main([]))
'''
    with output.open('w') as stream:
        child = subprocess.Popen([sys.executable, '-c', code], stdout=stream, stderr=stream)
        try:
            deadline = time.monotonic() + 5
            while 'render_standby' not in output.read_text() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert 'render_standby' in output.read_text()
            child.send_signal(sig)
            assert child.wait(timeout=5) == 0
            assert 'render_stopped' in output.read_text()
        finally:
            if child.poll() is None: child.kill()
            child.wait()


def test_spawn_error_is_fixed_diagnostic(capsys):
    assert render_service.supervise(threading.Event(), ['missing-secret-command']) == 1
    output = capsys.readouterr().out
    assert 'render_service_failed' in output and 'missing-secret-command' not in output


def test_cleanup_error_is_not_raw(monkeypatch, capsys):
    class Child:
        pid = 123
        def poll(self): return None
    stop = threading.Event()
    def spawn(*args, **kwargs):
        stop.set()
        return Child()
    monkeypatch.setattr(render_service.subprocess, 'Popen', spawn)
    monkeypatch.setattr(render_service.os, 'killpg', lambda *args: (_ for _ in ()).throw(OSError('SECRET')))
    assert render_service.supervise(stop, preflight=['test']) == 1
    output = capsys.readouterr().out
    assert 'render_cleanup_failed' in output and 'SECRET' not in output


def test_build_cli_defaults_to_plan_without_docker(tmp_path, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[2] / 'deploy/worker/build_render.py'
    spec = importlib.util.spec_from_file_location('render_build_plan', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'runtime-lock.json').write_text('{}')
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: pytest.fail('계획 모드에서 Docker 실행'))
    module.main(['--runtime-image', 'approved@sha256:' + 'a' * 64,
                 '--model-dir', str(tmp_path), '--runtime-dir', str(tmp_path)])
    assert 'linux/amd64' in capsys.readouterr().out


def test_question_worker_is_explicit_bounded_child(monkeypatch):
    captured=[]
    monkeypatch.setattr(render_service,'supervise',lambda stop,command,**kw: captured.extend(command) or 0)
    assert render_service.main(['--mode','question-worker','--runtime','approved:factory','--max-jobs','2'])==0
    assert captured[2:] == ['solo_leveling.interfaces.cli.question_worker','--execute','--runtime','approved:factory','--max-jobs','2']


@pytest.mark.parametrize('args', [
    ['--mode','question-worker'], ['--mode','question-worker','--runtime','approved:factory','--max-jobs','0'],
    ['--mode','question-worker','--runtime','approved:factory','--config','/secret'], ['--runtime','approved:factory']])
def test_question_config_never_runs_in_standby(args):
    with pytest.raises(SystemExit): render_service.main(args)
