"""검증 이력 보존·정리(#82)를 임시 SQLite로 검증한다: dry-run 무변경, 기한 경계, 범위 제한, 배치·중단·재실행, 동시 저장, 공간 회수."""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from solo_leveling.domain.verification_history import HistoryOutcome
from solo_leveling.domain.verification_retention import (
    CleanupOutcome, RetentionPolicy, RetentionValidationError, SpaceOutcome)
from solo_leveling.infrastructure.database import verification_history as vh
from solo_leveling.infrastructure.database import verification_retention as vr
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.interfaces.cli import verification_retention as cli
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401  (fixture)
from tests.unit.test_verification_history import claim, record, ref

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def age(db, run_id, days):
    when = (NOW - timedelta(days=days)).isoformat()
    with closing(get_connection(db)) as conn, conn:
        conn.execute('UPDATE verification_runs SET created_at=? WHERE run_id=?', (when, run_id))


@pytest.fixture
def env(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    return db


def seed(db, specs):
    """specs: [(run_id, 나이(일))]. 각 실행에 주장 2개·근거 3개."""
    for run_id, days in specs:
        rec = record(run_id, claims=[claim('k1', refs=[ref('c1', 'First.'), ref('c2', 'Second.')]),
                                     claim('k2', refs=[ref('c1', 'First.')])])
        assert vh.save_verification_history(db, rec).outcome is HistoryOutcome.SAVED
        age(db, run_id, days)


def table_counts(db, *tables):
    with closing(get_connection(db)) as conn:
        return tuple(conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables)


PROTECTED = ('learning_contexts', 'evidences', 'chunks', 'parse_revisions', 'paper_versions', 'search_index_chunks')
HISTORY = ('verification_runs', 'verification_claims', 'verification_claim_refs')


def policy(days=30, batch=200):
    return RetentionPolicy(days, batch)


# ── dry-run ────────────────────────────────────────────────
def test_plan_reports_counts_and_changes_nothing(env):
    seed(env, [('old1', 90), ('old2', 40), ('new', 5)])
    before = table_counts(env, *HISTORY, *PROTECTED)
    plan = vr.plan_verification_cleanup(env, policy(30), NOW)
    assert (plan.total_runs, plan.eligible_runs, plan.eligible_claims, plan.eligible_refs) == (3, 2, 4, 6)
    assert plan.oldest_created_at < plan.newest_eligible_created_at
    assert table_counts(env, *HISTORY, *PROTECTED) == before


def test_plan_on_empty_history(env):
    plan = vr.plan_verification_cleanup(env, policy(30), NOW)
    assert (plan.total_runs, plan.eligible_runs, plan.oldest_created_at) == (0, 0, None)


# ── 정리 범위 ──────────────────────────────────────────────
def test_cleanup_deletes_only_runs_older_than_retention_with_their_claims_and_refs(env):
    seed(env, [('old', 90), ('edge_new', 29), ('new', 1)])
    protected_before = table_counts(env, *PROTECTED)
    result = vr.run_verification_cleanup(env, policy(30), NOW)
    assert (result.outcome, result.deleted_runs, result.deleted_claims, result.deleted_refs) == \
        (CleanupOutcome.COMPLETED, 1, 2, 3)
    assert table_counts(env, *HISTORY) == (2, 4, 6)
    assert table_counts(env, *PROTECTED) == protected_before
    assert vr.count_foreign_key_violations(env) == 0
    with closing(get_connection(env)) as conn:
        assert {r[0] for r in conn.execute('SELECT run_id FROM verification_runs')} == {'edge_new', 'new'}


def test_run_at_exact_cutoff_is_kept(env):
    seed(env, [('edge', 30)])
    assert vr.run_verification_cleanup(env, policy(30), NOW).deleted_runs == 0


def test_failed_run_without_claims_is_also_cleaned(env):
    assert vh.save_verification_history(
        env, record('failed', claims=[], failure='timeout', level='structural_only')).outcome is HistoryOutcome.SAVED
    age(env, 'failed', 100)
    assert vr.run_verification_cleanup(env, policy(30), NOW).deleted_runs == 1
    assert table_counts(env, 'verification_runs') == (0,)


def test_cleanup_releases_chunk_restrict_so_chunks_stay_and_history_free(env):
    seed(env, [('old', 90)])
    vr.run_verification_cleanup(env, policy(30), NOW)
    with closing(get_connection(env)) as conn, conn:  # 참조가 사라졌으므로 청크 삭제 제한이 풀린다(청크 자체는 지우지 않았다)
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_id='c1'").fetchone()[0] == 1


# ── 배치·중단·재실행 ───────────────────────────────────────
def test_batches_stop_and_resume_without_breaking_integrity(env):
    seed(env, [(f'old{i}', 100 + i) for i in range(5)] + [('new', 1)])
    first = vr.run_verification_cleanup(env, policy(30, batch=2), NOW, max_batches=1)
    assert (first.outcome, first.deleted_runs, first.batches) == (CleanupOutcome.STOPPED, 2, 1)
    assert table_counts(env, 'verification_runs') == (4,) and vr.count_foreign_key_violations(env) == 0
    rest = vr.run_verification_cleanup(env, policy(30, batch=2), NOW)
    assert (rest.outcome, rest.deleted_runs) == (CleanupOutcome.COMPLETED, 3)
    again = vr.run_verification_cleanup(env, policy(30, batch=2), NOW)
    assert (again.outcome, again.deleted_runs, again.batches) == (CleanupOutcome.COMPLETED, 0, 0)
    assert table_counts(env, *HISTORY) == (1, 2, 3)


def test_oldest_runs_are_deleted_first(env):
    seed(env, [('a', 200), ('b', 100), ('c', 50)])
    vr.run_verification_cleanup(env, policy(30, batch=1), NOW, max_batches=1)
    with closing(get_connection(env)) as conn:
        assert {r[0] for r in conn.execute('SELECT run_id FROM verification_runs')} == {'b', 'c'}


def test_failure_in_a_batch_rolls_back_that_batch(env):
    seed(env, [('old1', 100), ('old2', 90)])
    with closing(get_connection(env)) as conn, conn:  # 세 번째 삭제(실행)에서 실패하도록 만든다
        conn.execute("CREATE TRIGGER block_run_delete BEFORE DELETE ON verification_runs "
                     "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    result = vr.run_verification_cleanup(env, policy(30), NOW)
    assert result.outcome is CleanupOutcome.FAILED and result.deleted_runs == 0
    assert table_counts(env, *HISTORY) == (2, 4, 6)  # 참조·주장 삭제도 함께 되돌려졌다


def test_cleanup_reports_locked_when_another_writer_holds_the_database(env):
    seed(env, [('old', 90)])
    blocker = sqlite3.connect(env, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        original = vr.get_connection
        vr.get_connection = lambda path: _short_timeout(original, path)
        result = vr.run_verification_cleanup(env, policy(30), NOW)
    finally:
        vr.get_connection = original
        blocker.execute('ROLLBACK')
        blocker.close()
    assert result.outcome is CleanupOutcome.LOCKED and result.deleted_runs == 0
    assert table_counts(env, 'verification_runs') == (1,)


def _short_timeout(original, path):
    conn = original(path)
    conn.execute('PRAGMA busy_timeout = 50')
    return conn


def test_save_after_cleanup_and_concurrent_saves_stay_consistent(env):
    seed(env, [('old', 90)])
    vr.run_verification_cleanup(env, policy(30), NOW, max_batches=0)
    assert vh.save_verification_history(env, record('fresh')).outcome is HistoryOutcome.SAVED
    vr.run_verification_cleanup(env, policy(30), NOW)
    assert table_counts(env, 'verification_runs') == (1,)  # 방금 저장한 실행은 기한 안이라 남는다
    assert vr.count_foreign_key_violations(env) == 0


# ── 설정 검증 ──────────────────────────────────────────────
@pytest.mark.parametrize('bad', [RetentionPolicy(0), RetentionPolicy(-1), RetentionPolicy(True),
                                 RetentionPolicy(30, 0), RetentionPolicy(30, 1001), RetentionPolicy('30')])
def test_invalid_policy_is_rejected_before_touching_data(env, bad):
    seed(env, [('old', 90)])
    with pytest.raises(RetentionValidationError):
        vr.run_verification_cleanup(env, bad, NOW)
    with pytest.raises(RetentionValidationError):
        vr.plan_verification_cleanup(env, bad, NOW)
    assert table_counts(env, 'verification_runs') == (1,)


# ── 공간 회수 ──────────────────────────────────────────────
def test_row_delete_keeps_file_size_and_reclaim_shrinks_it(env):
    with closing(get_connection(env)) as conn, conn:
        conn.execute('CREATE TABLE filler (x TEXT)')
        conn.executemany('INSERT INTO filler VALUES (?)', [('x' * 2000,)] * 2000)
        conn.execute('DROP TABLE filler')
    seed(env, [('old', 90)])
    vr.run_verification_cleanup(env, policy(30), NOW)
    report = vr.space_report(env)
    assert report.free_bytes_in_file > 0 and report.file_bytes >= report.free_bytes_in_file
    result = vr.reclaim_space(env)
    assert result.outcome is SpaceOutcome.COMPLETED and result.after_bytes < result.before_bytes
    assert vr.space_report(env).free_bytes_in_file == 0 and vr.count_foreign_key_violations(env) == 0


def test_reclaim_refuses_without_enough_disk_and_changes_nothing(env):
    before = os.path.getsize(env)
    result = vr.reclaim_space(env, free_bytes=lambda path: 0)
    assert (result.outcome, result.before_bytes, result.after_bytes) == (SpaceOutcome.INSUFFICIENT_DISK, before, before)


def test_reclaim_reports_locked_while_a_writer_holds_the_database(env):
    blocker = sqlite3.connect(env, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        original = sqlite3.connect
        result = None
        sqlite3_connect = vr.sqlite3.connect
        vr.sqlite3.connect = lambda path, **kw: original(path, timeout=0.05, **kw)
        try:
            result = vr.reclaim_space(env)
        finally:
            vr.sqlite3.connect = sqlite3_connect
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()
    assert result.outcome is SpaceOutcome.LOCKED


def test_backup_creates_a_copy_and_refuses_to_overwrite(env, tmp_path):
    seed(env, [('old', 90)])
    target = str(tmp_path / 'backup.db')
    vr.backup_database(env, target)
    assert table_counts(target, 'verification_runs') == (1,)
    with pytest.raises(FileExistsError):
        vr.backup_database(env, target)


# ── 명령줄 ────────────────────────────────────────────────
def run_cli(capsys, *args):
    code = cli.main(list(args))
    return code, capsys.readouterr().out


def test_cli_plan_is_dry_run_and_requires_retention(env, capsys):
    seed(env, [('old', 90)])
    code, out = run_cli(capsys, 'plan', '--db', env)
    assert code == 2 and 'retention_required' in out
    code, out = run_cli(capsys, 'plan', '--db', env, '--older-than-days', '30')
    assert code == 0 and 'mode=dry-run' in out and 'eligible_runs=1' in out
    assert table_counts(env, 'verification_runs') == (1,)


def test_cli_run_and_reclaim_need_explicit_yes(env, capsys):
    seed(env, [('old', 90)])
    assert run_cli(capsys, 'run', '--db', env, '--older-than-days', '30')[1].strip() == 'result=confirmation_required'
    assert run_cli(capsys, 'reclaim', '--db', env)[1].strip() == 'result=confirmation_required'
    assert table_counts(env, 'verification_runs') == (1,)


def test_cli_run_with_backup_deletes_and_prints_only_counts_and_codes(env, tmp_path, capsys):
    seed(env, [('old', 90), ('new', 1)])
    backup = tmp_path / 'b.db'
    code, out = run_cli(capsys, 'run', '--db', env, '--older-than-days', '30', '--yes', '--backup-to', str(backup))
    assert code == 0 and 'backup=created' in out and 'deleted_runs=1' in out and 'fk_violations=0' in out
    assert table_counts(env, 'verification_runs') == (1,) and table_counts(str(backup), 'verification_runs') == (2,)
    assert '주장 문구' not in out and 'First' not in out
    code, out = run_cli(capsys, 'run', '--db', env, '--older-than-days', '30', '--yes', '--backup-to', str(backup))
    assert code == 2 and 'backup_exists' in out and table_counts(env, 'verification_runs') == (1,)


def test_cli_rejects_missing_db_and_bad_setting(tmp_path, env, capsys):
    assert run_cli(capsys, 'plan', '--db', str(tmp_path / 'none.db'), '--older-than-days', '30')[1].strip() \
        == 'result=db_not_found'
    code, out = run_cli(capsys, 'plan', '--db', env, '--older-than-days', '0')
    assert code == 2 and 'invalid_setting' in out and 'older_than_days' in out


# ── B 리뷰 반영: WAL 포함 용량·잠금/일반 오류 구분·배타적 백업 ──────────────────────────────
import threading  # noqa: E402
from contextlib import contextmanager  # noqa: E402


@contextmanager
def short_lock_timeout():
    original = sqlite3.connect
    sqlite3.connect = lambda path, **kw: original(path, **{**kw, 'timeout': 0.05})
    try:
        yield
    finally:
        sqlite3.connect = original


def test_reclaim_checks_logical_size_including_wal_and_does_not_vacuum(tmp_path):
    db = str(tmp_path / 'wal.db')
    holder = sqlite3.connect(db, isolation_level=None)          # 열어 두어야 WAL이 닫힐 때 합쳐지지 않는다
    try:
        holder.execute('PRAGMA journal_mode=WAL')
        holder.execute('PRAGMA wal_autocheckpoint=0')
        holder.execute('CREATE TABLE t (x TEXT)')
        holder.execute('BEGIN')
        holder.executemany('INSERT INTO t VALUES (?)', [('x' * 1000,)] * 2000)
        holder.execute('COMMIT')
        main_size, wal_size = os.path.getsize(db), os.path.getsize(db + '-wal')
        assert wal_size > 1_000_000 and main_size < wal_size       # 본체만 보면 작고 WAL이 크다
        result = vr.reclaim_space(db, free_bytes=lambda path: 10_000)
        assert result.outcome is SpaceOutcome.INSUFFICIENT_DISK
        assert result.before_bytes > 1_000_000 and result.before_bytes > 100 * main_size   # 본체가 아니라 논리 크기를 보고했다
        assert (os.path.getsize(db), os.path.getsize(db + '-wal')) == (main_size, wal_size)   # VACUUM·체크포인트 안 함
        assert holder.execute('SELECT COUNT(*) FROM t').fetchone()[0] == 2000
    finally:
        holder.close()


def test_lock_and_general_database_errors_are_told_apart(env, tmp_path):
    blocker = sqlite3.connect(env, isolation_level=None)
    blocker.execute('BEGIN EXCLUSIVE')
    try:
        with short_lock_timeout():
            assert vr.run_verification_cleanup(env, policy(30), NOW).outcome is CleanupOutcome.LOCKED
            assert vr.reclaim_space(env).outcome is SpaceOutcome.LOCKED
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()
    missing = str(tmp_path / 'no_tables.db')
    with closing(sqlite3.connect(missing)) as conn, conn:
        conn.execute('CREATE TABLE other (x)')
    assert vr.run_verification_cleanup(missing, policy(30), NOW).outcome is CleanupOutcome.FAILED   # 잠금이 아니다
    with pytest.raises(sqlite3.OperationalError) as caught:
        vr.plan_verification_cleanup(missing, policy(30), NOW)
    assert not vr.is_lock_error(caught.value)


def test_cli_locked_versus_failed_for_all_three_commands(env, tmp_path, capsys):
    missing = str(tmp_path / 'no_tables.db')
    with closing(sqlite3.connect(missing)) as conn, conn:
        conn.execute('CREATE TABLE other (x)')
    garbage = tmp_path / 'garbage.db'
    garbage.write_bytes(b'this is not a sqlite database' * 200)
    for target in (missing, str(garbage)):
        for args in (('plan', '--older-than-days', '30'), ('run', '--older-than-days', '30', '--yes')):
            code, out = run_cli(capsys, args[0], '--db', target, *args[1:])
            assert code == 1 and out.startswith('result=failed'), (args, out)
            assert str(tmp_path) not in out and 'no such table' not in out and 'not a database' not in out
    code, out = run_cli(capsys, 'reclaim', '--db', str(garbage), '--yes')
    assert (code, out.strip().split()[0]) == (1, 'result=failed')
    assert str(tmp_path) not in out and 'no such table' not in out

    blocker = sqlite3.connect(env, isolation_level=None)
    blocker.execute('BEGIN EXCLUSIVE')
    try:
        with short_lock_timeout():
            code, out = run_cli(capsys, 'plan', '--db', env, '--older-than-days', '30')
            assert (code, out.strip()) == (1, 'result=locked')
            code, out = run_cli(capsys, 'run', '--db', env, '--older-than-days', '30', '--yes')
            assert code == 1 and out.startswith('result=locked')
            code, out = run_cli(capsys, 'reclaim', '--db', env, '--yes')
            assert code == 1 and out.startswith('result=locked')
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()


def test_backup_destination_is_reserved_exclusively_even_if_existence_check_is_stale(env, tmp_path, monkeypatch):
    target = tmp_path / 'backup.db'
    target.write_bytes(b'FIRST-BACKUP')                              # 먼저 만든 사본
    monkeypatch.setattr(os.path, 'exists', lambda path: False)       # 확인과 연결 사이에 만들어진 상황을 흉내
    with pytest.raises(FileExistsError):
        vr.backup_database(env, str(target))
    assert target.read_bytes() == b'FIRST-BACKUP'                    # 덮어쓰지 않았다


def test_concurrent_backups_to_the_same_destination_only_one_wins(env, tmp_path):
    seed(env, [('old', 90)])
    for round_number in range(15):
        target = str(tmp_path / f'backup{round_number}.db')
        barrier, outcomes = threading.Barrier(2), []

        def attempt():
            barrier.wait()
            try:
                vr.backup_database(env, target)
                outcomes.append('created')
            except FileExistsError:
                outcomes.append('exists')

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(outcomes) == ['created', 'exists'], outcomes
        assert table_counts(target, 'verification_runs') == (1,)     # 먼저 만든 사본이 온전하다


def test_failed_backup_removes_only_its_own_reservation(env, tmp_path):
    missing_source = str(tmp_path / 'nope' / 'src.db')
    target = tmp_path / 'backup.db'
    with pytest.raises(sqlite3.Error):
        vr.backup_database(missing_source, str(target))
    assert not target.exists()                                        # 재시도할 수 있다
    vr.backup_database(env, str(target))
    assert target.exists()
