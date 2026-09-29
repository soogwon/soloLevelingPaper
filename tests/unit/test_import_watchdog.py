"""import 감시의 취소와 실제 스택 출력을 검증한다."""

from unittest.mock import Mock
from pathlib import Path
import subprocess
import sys
import pytest
from solo_leveling import diagnostics as diag


@pytest.mark.parametrize('fail', [False, True])
def test_watchdog_cancel_and_nested(tmp_path, monkeypatch, fail):
    monkeypatch.setattr(diag, '_stack_directory', tmp_path)
    arm, cancel = Mock(), Mock()
    monkeypatch.setattr(diag.faulthandler, 'dump_traceback_later', arm)
    monkeypatch.setattr(diag.faulthandler, 'cancel_dump_traceback_later', cancel)

    @diag.traced('answer', request=True)
    def run():
        with diag.import_watchdog():
            with diag.import_watchdog():
                if fail:
                    raise ValueError('비밀 본문')

    if fail:
        with pytest.raises(ValueError):
            run()
    else:
        run()
    arm.assert_called_once()
    assert arm.call_args.kwargs['exit'] is False
    cancel.assert_called_once()
    assert not diag._stack_lock.locked()
    assert arm.call_args.kwargs['file'].closed


def test_real_dump_does_not_terminate(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = '''
import sys, time
sys.path.insert(0, sys.argv[1])
from solo_leveling.diagnostics import configure_stack_diagnostics, traced, import_watchdog
configure_stack_diagnostics(sys.argv[2])
@traced('probe', request=True)
def run():
    with import_watchdog(0.05):
        time.sleep(0.2)
run()
print('COMPLETED')
'''
    result = subprocess.run([sys.executable, '-c', code, str(root / 'src'), str(tmp_path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == 'COMPLETED'
    logs = list(tmp_path.glob('import-stack-*.log'))
    assert len(logs) == 1
    content = logs[0].read_text()
    assert 'Timeout' in content and 'run' in content
