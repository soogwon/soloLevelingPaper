"""검증 이력 보존·정리(#82). 스키마는 schema.py의 verification_runs·verification_claims·verification_claim_refs.

- 지우는 것은 보존 기간보다 오래된 '검증 실행'과 그 주장·근거 연결뿐이다. 맥락·답변 근거·청크·논문 파일은 건드리지 않는다.
- 이력 저장(save_verification_history)은 실행·주장·참조를 한 트랜잭션으로 넣으므로, 읽는 쪽에는 완성된 실행만 보인다.
  그래서 '진행 중 기록'은 따로 표시하지 않고, 정리는 보존 기한(created_at)보다 새로운 실행을 절대 지우지 않는다.
- 정리는 배치마다 BEGIN IMMEDIATE 짧은 트랜잭션이다. 배치는 참조 → 주장 → 실행 순서로 지우고, 같은 트랜잭션에서
  기한을 다시 확인한다. 중단돼도 끝난 배치는 온전하고, 다시 실행하면 이어서 지운다. 쓰기와 겹치면 LOCKED로 멈춘다.
- 행 삭제는 파일 크기를 줄이지 않는다. 공간 회수(VACUUM)는 reclaim_space로 따로, 명시적으로만 한다.
- 계획·결과에는 건수·기간·코드만 담는다.
"""
import os
import shutil
import sqlite3
import time
from pathlib import Path
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from solo_leveling.domain.verification_retention import (
    CleanupOutcome, CleanupPlan, CleanupResult, RetentionPolicy, SpaceOutcome, SpaceReport, SpaceResult,
    TargetCheck, validate_policy)
from solo_leveling.infrastructure.database.schema import get_connection


_LOCK_CODES = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def is_lock_error(error: sqlite3.Error) -> bool:
    """실제 잠금(SQLITE_BUSY·SQLITE_LOCKED 계열)만 True다. 테이블 누락 같은 일반 DB 오류는 재시도할 상황이 아니다(#82)."""
    code = getattr(error, 'sqlite_errorcode', None)
    return isinstance(code, int) and (code & 0xFF) in _LOCK_CODES


REQUIRED_TABLES = ('verification_runs', 'verification_claims', 'verification_claim_refs')
DEFAULT_BACKUP_LOCK_TIMEOUT = 30.0


class BackupLockTimeout(Exception):
    """원본이 다른 쓰기로 잠겨 있어 정해진 시간 안에 백업하지 못했다. 만든 사본은 지운다."""


def check_target(db_path: str) -> TargetCheck:
    """변경 전에 대상이 이 프로젝트의 검증 이력을 가진 SQLite 파일인지 읽기 전용으로 확인한다. 파일을 만들거나 바꾸지 않는다."""
    uri = Path(db_path).resolve().as_uri() + '?mode=ro'
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as error:
        if is_lock_error(error):
            return TargetCheck.LOCKED
        return TargetCheck.NOT_A_DATABASE
    return TargetCheck.OK if all(name in names for name in REQUIRED_TABLES) else TargetCheck.MISSING_TABLES


def _cutoff(policy: RetentionPolicy, now: Optional[datetime]) -> str:
    # created_at은 now_iso()가 쓰는 UTC isoformat 문자열이라 같은 형식으로 비교한다.
    base = datetime.now(timezone.utc) if now is None else now
    return (base - timedelta(days=policy.older_than_days)).isoformat()


def plan_verification_cleanup(db_path: str, policy: RetentionPolicy, now: Optional[datetime] = None) -> CleanupPlan:
    """dry-run. 데이터를 바꾸지 않고 정리될 대상의 건수·기간만 한 스냅샷에서 센다."""
    validate_policy(policy)
    cutoff = _cutoff(policy, now)
    with closing(get_connection(db_path)) as conn:
        conn.execute('BEGIN')
        total = conn.execute('SELECT COUNT(*) FROM verification_runs').fetchone()[0]
        run = conn.execute('SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM verification_runs '
                           'WHERE created_at < ?', (cutoff,)).fetchone()
        claims = conn.execute('SELECT COUNT(*) FROM verification_claims WHERE run_id IN '
                              '(SELECT run_id FROM verification_runs WHERE created_at < ?)', (cutoff,)).fetchone()[0]
        refs = conn.execute('SELECT COUNT(*) FROM verification_claim_refs WHERE run_id IN '
                            '(SELECT run_id FROM verification_runs WHERE created_at < ?)', (cutoff,)).fetchone()[0]
        conn.rollback()
    return CleanupPlan(cutoff, total, run[0], claims, refs, run[1], run[2])


def run_verification_cleanup(db_path: str, policy: RetentionPolicy, now: Optional[datetime] = None,
                             max_batches: Optional[int] = None) -> CleanupResult:
    """보존 기간이 지난 검증 실행을 배치로 지운다. 호출자가 명시적으로 부를 때만 실행된다."""
    validate_policy(policy)
    cutoff = _cutoff(policy, now)
    runs = claims = refs = batches = 0
    while True:
        if max_batches is not None and batches >= max_batches:
            return CleanupResult(CleanupOutcome.STOPPED, runs, claims, refs, batches)
        try:
            with closing(get_connection(db_path)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                ids = [row[0] for row in conn.execute(
                    'SELECT run_id FROM verification_runs WHERE created_at < ? '
                    'ORDER BY created_at, run_id LIMIT ?', (cutoff, policy.batch_size))]
                if not ids:
                    return CleanupResult(CleanupOutcome.COMPLETED, runs, claims, refs, batches)
                marks = ','.join('?' * len(ids))
                batch_refs = conn.execute(
                    f'DELETE FROM verification_claim_refs WHERE run_id IN ({marks})', ids).rowcount
                batch_claims = conn.execute(
                    f'DELETE FROM verification_claims WHERE run_id IN ({marks})', ids).rowcount
                conn.execute(f'DELETE FROM verification_runs WHERE run_id IN ({marks})', ids)
        except sqlite3.Error as error:
            outcome = CleanupOutcome.LOCKED if is_lock_error(error) else CleanupOutcome.FAILED
            return CleanupResult(outcome, runs, claims, refs, batches)
        runs += len(ids)
        claims += batch_claims
        refs += batch_refs
        batches += 1


def count_foreign_key_violations(db_path: str) -> int:
    """정리 뒤 참조 무결성 점검. 0이어야 한다."""
    with closing(get_connection(db_path)) as conn:
        return len(conn.execute('PRAGMA foreign_key_check').fetchall())


def space_report(db_path: str) -> SpaceReport:
    with closing(get_connection(db_path)) as conn:
        page_size = conn.execute('PRAGMA page_size').fetchone()[0]
        free = conn.execute('PRAGMA freelist_count').fetchone()[0]
    return SpaceReport(os.path.getsize(db_path), page_size * free)


def backup_database(db_path: str, backup_path: str, lock_timeout: float = DEFAULT_BACKUP_LOCK_TIMEOUT) -> None:
    """SQLite 백업 API로 일관된 사본을 만든다. 이미 있는 파일은 덮어쓰지 않는다.

    목적지는 O_EXCL로 먼저 배타적으로 확보한다. 존재 확인과 연결 사이에 다른 실행이 같은 경로를 만들어도
    한쪽만 확보하고 나머지는 FileExistsError로 멈추며, 먼저 만든 사본은 그대로 남는다. 백업이 실패하면 이 호출이
    만든 파일만 지워 재시도할 수 있게 한다.

    원본이 잠겨 있으면 백업 API는 끝없이 다시 시도하므로, 잠김이 lock_timeout초 넘게 이어지면 BackupLockTimeout으로
    멈춘다(#178). 잠기지 않고 진행되는 시간은 제한하지 않는다.
    """
    if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)) or not 0 < lock_timeout <= 3600:
        raise ValueError('lock_timeout')
    fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    waiting = {'since': None}

    def progress(status, remaining, total):
        if status in _LOCK_CODES:
            now = time.monotonic()
            waiting['since'] = now if waiting['since'] is None else waiting['since']
            if now - waiting['since'] > lock_timeout:
                raise BackupLockTimeout()
        else:
            waiting['since'] = None

    try:
        with closing(sqlite3.connect(db_path, timeout=min(lock_timeout, 1.0))) as src, \
                closing(sqlite3.connect(backup_path)) as dst:
            src.backup(dst, progress=progress)
    except BaseException:
        try:
            os.unlink(backup_path)
        except OSError:
            pass
        raise


def logical_size(db_path: str) -> int:
    """WAL에 커밋된 페이지까지 포함한 데이터베이스 크기(page_count × page_size). 본체 파일 크기보다 클 수 있다."""
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute('PRAGMA page_count').fetchone()[0] * conn.execute('PRAGMA page_size').fetchone()[0]


def _wal_bytes(db_path: str) -> int:
    try:
        return os.path.getsize(db_path + '-wal')
    except OSError:
        return 0


def reclaim_space(db_path: str, free_bytes: Optional[Callable[[str], int]] = None) -> SpaceResult:
    """VACUUM으로 파일 공간을 회수한다. 전체 쓰기 잠금이 걸리고 임시 공간으로 DB 크기만큼 더 필요하다.

    여유 공간은 본체 파일이 아니라 WAL의 커밋된 페이지를 포함한 논리 크기의 2배로 판단한다(#82).
    WAL 모드에서는 VACUUM 결과도 WAL에 먼저 기록되므로, 끝난 뒤 한 번 더 체크포인트(TRUNCATE)하고 연결을 닫은 다음
    본체와 WAL의 실제 디스크 크기를 보고한다. WAL이 남아 있으면 WAL_NOT_TRUNCATED이며 성공으로 보고하지 않는다(#178).
    """
    try:
        before = logical_size(db_path)
    except sqlite3.Error as error:
        size = os.path.getsize(db_path) + _wal_bytes(db_path)
        return SpaceResult(SpaceOutcome.LOCKED if is_lock_error(error) else SpaceOutcome.FAILED, size, size)
    available = (free_bytes or (lambda path: shutil.disk_usage(path).free))(os.path.dirname(os.path.abspath(db_path)))
    if available < before * 2:
        return SpaceResult(SpaceOutcome.INSUFFICIENT_DISK, before, before)
    try:
        with closing(sqlite3.connect(db_path, isolation_level=None)) as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('VACUUM')
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except sqlite3.Error as error:
        outcome = SpaceOutcome.LOCKED if is_lock_error(error) else SpaceOutcome.FAILED
        return SpaceResult(outcome, before, os.path.getsize(db_path) + _wal_bytes(db_path), _wal_bytes(db_path))
    wal = _wal_bytes(db_path)   # 마지막 연결이 닫히면 WAL 파일은 사라진다. 남아 있으면 다른 연결이 붙들고 있다.
    outcome = SpaceOutcome.COMPLETED if wal == 0 else SpaceOutcome.WAL_NOT_TRUNCATED
    return SpaceResult(outcome, before, os.path.getsize(db_path) + wal, wal)
