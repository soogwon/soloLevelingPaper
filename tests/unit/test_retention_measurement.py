"""측정 실행기는 원본 대신 새 복사본에서만 쓰기·정리한다."""

from hashlib import sha256
import os
import stat
import sqlite3

import pytest
from pathlib import Path
from scripts import measure_verification_retention as measurement

from scripts.measure_verification_retention import measure
from tests.unit.test_verification_retention import env, seed, setup_db  # noqa: F401


def test_measurement_preserves_source_and_protected_data(env, tmp_path):
    from pathlib import Path
    seed(env, [('sample1', 1), ('sample2', 2)])
    before = sha256(Path(env).read_bytes()).hexdigest()
    report = measure(env, tmp_path/'measurement', copies=2)
    assert report['sample']['runs'] == 2 and report['measured_runs'] == 4
    assert report['cleanup']['deleted_runs'] == 2
    assert report['save']['count'] == report['get']['count'] == 4
    assert report['protected_tables_unchanged'] and report['foreign_key_violations'] == 0
    assert sha256(Path(env).read_bytes()).hexdigest() == before
    assert (tmp_path/'measurement/report.json').exists()
    assert report['reclaim']['outcome'] == 'completed'
    assert report['source_files_unchanged'] and report['backup_verified']
    assert report['sqlite_settings']['journal_mode'] == 'delete'


def test_rejects_existing_destination_before_touching_source(env, tmp_path):
    folder = tmp_path/'existing'
    folder.mkdir()
    (folder/'sentinel').write_text('keep')
    with pytest.raises(FileExistsError):
        measure(env, folder)
    assert (folder/'sentinel').read_text() == 'keep'


def test_missing_source_is_not_created(tmp_path):
    source = tmp_path/'missing.db'
    with pytest.raises(ValueError):
        measure(source, tmp_path/'out')
    assert not source.exists() and not (tmp_path/'out').exists()


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_source_sidecars_are_not_touched_or_ignored(env, tmp_path, suffix):
    sidecar = Path(env+suffix)
    sidecar.write_bytes(b'private active state')
    before = Path(env).read_bytes()
    with pytest.raises(ValueError, match='SOURCE_SIDECAR_PRESENT'):
        measure(env, tmp_path/'out', copies=1)
    assert Path(env).read_bytes() == before
    assert sidecar.read_bytes() == b'private active state'
    assert not (tmp_path/'out/report.json').exists()


def test_source_is_never_opened_by_sqlite(env, tmp_path, monkeypatch):
    seed(env, [('sample', 1)])
    original = sqlite3.connect
    def connect(path, *args, **kwargs):
        assert str(path) != env and Path(env).as_uri() not in str(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    assert measure(env, tmp_path/'out', copies=1)['source_files_unchanged']


def test_changed_source_during_copy_aborts_without_report(env, tmp_path, monkeypatch):
    original = measurement.source_fingerprint
    calls = []
    def fingerprint(path):
        calls.append(path)
        if len(calls) == 2:
            with Path(path).open('ab') as stream:
                stream.write(b'changed')
        return original(path)
    monkeypatch.setattr(measurement, 'source_fingerprint', fingerprint)
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        measure(env, tmp_path/'out')
    assert not (tmp_path/'out/report.json').exists()


def test_same_count_backup_with_wrong_content_is_rejected(env, tmp_path, monkeypatch):
    seed(env, [('sample', 1)])
    original = measurement.retention.backup_database
    def backup(source, target):
        original(source, target)
        with sqlite3.connect(target) as conn:
            conn.execute("UPDATE verification_claims SET claim_text='changed'")
    monkeypatch.setattr(measurement.retention, 'backup_database', backup)
    with pytest.raises(ValueError, match='BACKUP_CONTENT_MISMATCH'):
        measure(env, tmp_path/'out', copies=1)
    assert not (tmp_path/'out/report.json').exists()


def test_validation_is_active_under_python_optimization():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-O', '-c',
        'from scripts.measure_verification_retention import require; require(False,"GUARD_ACTIVE")'],
        capture_output=True, text=True, timeout=30, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode != 0 and 'GUARD_ACTIVE' in result.stderr


def test_replication_limit_is_checked_before_inserting(env, tmp_path, monkeypatch):
    seed(env, [('sample', 1)])
    monkeypatch.setattr(measurement, 'MAX_MEASURED_RUNS', 1)
    with pytest.raises(ValueError, match='MEASUREMENT_LIMIT'):
        measure(env, tmp_path/'out', copies=2)
    with sqlite3.connect(tmp_path/'out/measurement.sqlite3') as conn:
        assert conn.execute('SELECT COUNT(*) FROM verification_runs').fetchone()[0] == 1
    assert not (tmp_path/'out/report.json').exists()


def test_changed_retained_claim_is_not_reported_as_success(env, tmp_path, monkeypatch):
    seed(env, [('sample', 1)])
    original = measurement.retention.run_verification_cleanup
    def cleanup(db, *args):
        result = original(db, *args)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE verification_claims SET claim_text='changed'")
        return result
    monkeypatch.setattr(measurement.retention, 'run_verification_cleanup', cleanup)
    with pytest.raises(ValueError, match='RETAINED_CONTENT_CHANGED'):
        measure(env, tmp_path/'out', copies=3)
    assert not (tmp_path/'out/report.json').exists()


def test_reclaim_cannot_change_retained_history(env, tmp_path, monkeypatch):
    seed(env, [('sample', 1)])
    original = measurement.retention.reclaim_space
    calls = []
    def reclaim(db):
        calls.append(db)
        result = original(db)
        if len(calls) == 2:  # 첫 호출은 빈 기준선 VACUUM이다.
            with sqlite3.connect(db) as conn:
                conn.execute("DELETE FROM verification_claim_refs")
        return result
    monkeypatch.setattr(measurement.retention, 'reclaim_space', reclaim)
    with pytest.raises(ValueError, match='RECLAIM_FAILED'):
        measure(env, tmp_path/'out', copies=3)
    assert not (tmp_path/'out/report.json').exists()


def test_digest_distinguishes_blob_from_text(tmp_path):
    db = tmp_path/'types.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE payload(value)')
        conn.execute('INSERT INTO payload VALUES (?)', (b'\xff',))
    before = measurement.protected_digest(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE payload SET value='ff'")
    assert measurement.protected_digest(db) != before


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 파일 권한 검증')
@pytest.mark.parametrize('has_samples', [False, True], ids=['after-copy-failure', 'success'])
def test_private_measurement_files_under_shared_umask(env, tmp_path, has_samples):
    if has_samples:
        seed(env, [('sample', 1)])
    source = Path(env)
    source.chmod(0o600)
    output = tmp_path / 'measurement'
    previous = os.umask(0o022)
    try:
        if has_samples:
            measure(source, output, copies=1)
        else:
            with pytest.raises(ValueError, match='NO_SAMPLES'):
                measure(source, output, copies=1)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / 'measurement.sqlite3').stat().st_mode) == 0o600
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    if has_samples:
        assert stat.S_IMODE((output / 'before-cleanup.sqlite3').stat().st_mode) == 0o600
