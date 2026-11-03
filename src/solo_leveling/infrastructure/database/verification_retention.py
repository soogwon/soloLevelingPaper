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
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from solo_leveling.domain.verification_retention import (
    CleanupOutcome, CleanupPlan, CleanupResult, RetentionPolicy, SpaceOutcome, SpaceReport, SpaceResult,
    validate_policy)
from solo_leveling.infrastructure.database.schema import get_connection


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
        except sqlite3.OperationalError:
            return CleanupResult(CleanupOutcome.LOCKED, runs, claims, refs, batches)
        except sqlite3.Error:
            return CleanupResult(CleanupOutcome.FAILED, runs, claims, refs, batches)
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


def backup_database(db_path: str, backup_path: str) -> None:
    """SQLite 백업 API로 일관된 사본을 만든다. 이미 있는 파일은 덮어쓰지 않는다."""
    if os.path.exists(backup_path):
        raise FileExistsError('backup_path')
    with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(backup_path)) as dst:
        src.backup(dst)


def reclaim_space(db_path: str, free_bytes: Optional[Callable[[str], int]] = None) -> SpaceResult:
    """VACUUM으로 파일 공간을 회수한다. 전체 쓰기 잠금이 걸리고 임시 공간으로 DB 크기만큼 더 필요하다."""
    before = os.path.getsize(db_path)
    available = (free_bytes or (lambda path: shutil.disk_usage(path).free))(os.path.dirname(os.path.abspath(db_path)))
    if available < before * 2:
        return SpaceResult(SpaceOutcome.INSUFFICIENT_DISK, before, before)
    try:
        with closing(sqlite3.connect(db_path, isolation_level=None)) as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('VACUUM')
    except sqlite3.OperationalError:
        return SpaceResult(SpaceOutcome.LOCKED, before, os.path.getsize(db_path))
    except sqlite3.Error:
        return SpaceResult(SpaceOutcome.FAILED, before, os.path.getsize(db_path))
    return SpaceResult(SpaceOutcome.COMPLETED, before, os.path.getsize(db_path))
