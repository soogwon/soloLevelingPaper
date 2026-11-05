"""ask 실행 기록 저장소(#119). 스키마는 schema.py의 ask_executions. 계약 값은 domain/ask_execution.py.

- 모든 쓰기는 짧은 트랜잭션 하나(BEGIN IMMEDIATE)다. 시작·갱신·결과 저장이 서로 끼어들 수 없다.
- 단일 실행권: 같은 키의 begin_execution은 처음 한 번만 STARTED와 소유자 토큰을 준다. 이후 호출은 토큰 없이 현재 상태를 알려 준다.
  임대가 지난 실행도 새 소유자에게 넘기지 않는다(자동 재시작 금지). 새 실행은 새 키로만 시작한다.
- 같은 키라도 저장된 context나 입력 해시가 다르면 RequestConflictError. 상태·본문을 읽기 전에 먼저 확인한다.
- 임대 만료 판단 시각은 쓰기 잠금을 얻은 직후에 정한다(잠금 대기 중에 임대가 지났으면 LEASE_LOST). 시각을 주입한 경우는 그 값을 쓴다.
- 만료된 실행의 하트비트·결과·실패 기록은 LEASE_LOST로 거부하고 아무것도 바꾸지 않는다. 임대 판정은 now < lease_expires_at이다.
- lookup_execution은 읽기 전용이다. 상태는 저장된 시각과 now로 계산하며 조회가 행을 바꾸지 않는다.
- 결과 보존이 지나면 EXPIRED로 보이고 본문은 돌려주지 않는다. 실제 정리(purge)는 plan으로 건수를 본 뒤 명시적으로 호출한다.
  유효한 임대의 실행은 정리하지 않는다.
- 시각은 호출자가 now를 주입할 수 있다(테스트·정리 기준). 없으면 현재 UTC.
- 키·질문·결과는 로그에 남기지 않는다.
"""
import hmac
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from solo_leveling.domain.ask_execution import (
    KEY_RETENTION_DAYS, LEASE_EXPIRED, LEASE_SECONDS, MAX_RESULT_BYTES, RESULT_RETENTION_DAYS, ExecutionOutcome,
    ExecutionResult, ExecutionState, ExecutionValidationError, ExecutionView, PurgePlan, PurgeResult,
    check_failure_code, check_text)
from solo_leveling.domain.models import RequestConflictError
from solo_leveling.infrastructure.database.repository import insert_evidences
from solo_leveling.infrastructure.database.verification_history import ensure_verification_history
from solo_leveling.domain.verification_history import validate_record
from solo_leveling.infrastructure.database.schema import get_connection


_TOKEN = re.compile(r'[0-9a-f]{32}')   # 서버가 발급하는 소유자 토큰의 형식(uuid4().hex)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: Optional[datetime]) -> str:
    """고정 폭 UTC 문자열. 문자열 비교가 시각 비교와 같다."""
    moment = _utcnow() if value is None else value
    if moment.tzinfo is None:
        raise ExecutionValidationError('now')
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _view(row, now: str) -> ExecutionView:
    """저장된 행과 now로 상태를 계산한다. 행을 바꾸지 않는다."""
    base = dict(context_id=row['context_id'], created_at=row['created_at'], finished_at=row['finished_at'],
                result_expires_at=row['result_expires_at'])
    if now >= row['key_expires_at']:
        return ExecutionView(ExecutionState.EXPIRED, **base)
    if row['status'] == 'completed':
        if row['result_expires_at'] is None or now >= row['result_expires_at'] or row['result_json'] is None:
            return ExecutionView(ExecutionState.EXPIRED, **base)
        return ExecutionView(ExecutionState.COMPLETED, result_json=row['result_json'], **base)
    if row['status'] == 'failed':
        return ExecutionView(ExecutionState.FAILED, failure_code=row['failure_code'], **base)
    if now >= row['lease_expires_at']:
        return ExecutionView(ExecutionState.FAILED, failure_code=LEASE_EXPIRED, **base)
    return ExecutionView(ExecutionState.RUNNING, lease_expires_at=row['lease_expires_at'], **base)


_OUTCOME = {ExecutionState.RUNNING: ExecutionOutcome.IN_PROGRESS, ExecutionState.COMPLETED: ExecutionOutcome.COMPLETED,
            ExecutionState.FAILED: ExecutionOutcome.FAILED, ExecutionState.EXPIRED: ExecutionOutcome.EXPIRED}


def _at_lock(fixed: Optional[str]) -> tuple[str, datetime]:
    """쓰기 잠금을 얻은 직후의 시각. 시각을 주입했으면 그 값을 쓴다(테스트·정리 기준).

    잠금을 기다리는 동안 시간이 흐르므로, 임대 검사에 잠금 전에 얻은 시각을 쓰면 만료된 실행이 저장될 수 있다.
    """
    stamp = fixed if fixed is not None else _ts(None)
    return stamp, datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)


def begin_execution(db_path: str, request_key: str, input_hash: str, context_id: str,
                    now: Optional[datetime] = None) -> ExecutionResult:
    """새 키면 실행을 시작하고 소유자 토큰을 돌려준다. 기존 키면 상태만 알려 주고 실행하지 않는다."""
    check_text(request_key, 'request_key')
    check_text(input_hash, 'input_hash')
    check_text(context_id, 'context_id')
    fixed = _ts(now) if now is not None else None   # 주입한 시각은 형식을 먼저 검사한다
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        stamp, moment = _at_lock(fixed)
        row = conn.execute('SELECT * FROM ask_executions WHERE request_key=?', (request_key,)).fetchone()
        if row is not None:
            # 기존 행은 저장된 context와 입력 해시가 모두 같을 때만 재사용한다. 상태·본문은 그 뒤에 읽는다.
            if row['input_hash'] != input_hash or row['context_id'] != context_id:
                raise RequestConflictError('같은 request_key가 다른 입력에 사용되었습니다.')
            view = _view(row, stamp)
            return ExecutionResult(_OUTCOME[view.state], view=view)
        if conn.execute('SELECT 1 FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone() is None:
            return ExecutionResult(ExecutionOutcome.CONTEXT_NOT_FOUND)
        token = uuid4().hex
        lease = _ts(moment + timedelta(seconds=LEASE_SECONDS))
        key_expiry = _ts(moment + timedelta(days=KEY_RETENTION_DAYS))
        conn.execute('INSERT INTO ask_executions (request_key, input_hash, context_id, owner_token, status, '
                     'created_at, heartbeat_at, lease_expires_at, key_expires_at) '
                     "VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                     (request_key, input_hash, context_id, token, stamp, stamp, lease, key_expiry))
        row = conn.execute('SELECT * FROM ask_executions WHERE request_key=?', (request_key,)).fetchone()
        return ExecutionResult(ExecutionOutcome.STARTED, token, _view(row, stamp))


def _is_owner(stored: str, given) -> bool:
    """형식이 서버가 발급한 토큰(소문자 16진수 32자)인 입력만 상수 시간 비교한다. 그 밖의 값은 소유자가 아니다.

    hmac.compare_digest는 ASCII가 아닌 문자열에서 TypeError를 내므로 비교 전에 형식을 확인한다.
    """
    if not isinstance(given, str) or not _TOKEN.fullmatch(given) or not _TOKEN.fullmatch(stored):
        return False
    return hmac.compare_digest(stored.encode('ascii'), given.encode('ascii'))


def _owned_running(conn, request_key: str, owner_token: str, stamp: str):
    """소유자 토큰이 맞고, 실행 중이며, 임대가 남아 있는 행. 아니면 (None, 결과 코드)."""
    row = conn.execute('SELECT * FROM ask_executions WHERE request_key=?', (request_key,)).fetchone()
    if row is None:
        return None, ExecutionOutcome.NOT_FOUND
    if (not _is_owner(row['owner_token'], owner_token) or row['status'] != 'running'
            or stamp >= row['lease_expires_at'] or stamp >= row['key_expires_at']):
        return None, ExecutionOutcome.LEASE_LOST
    return row, None


def heartbeat_execution(db_path: str, request_key: str, owner_token: str,
                        now: Optional[datetime] = None) -> ExecutionResult:
    """임대를 갱신한다. 만료됐거나 토큰이 다르면 LEASE_LOST이고 아무것도 바꾸지 않는다."""
    check_text(request_key, 'request_key')
    check_text(owner_token, 'owner_token')
    fixed = _ts(now) if now is not None else None
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        stamp, moment = _at_lock(fixed)
        row, code = _owned_running(conn, request_key, owner_token, stamp)
        if row is None:
            return ExecutionResult(code)
        lease = _ts(moment + timedelta(seconds=LEASE_SECONDS))
        conn.execute('UPDATE ask_executions SET heartbeat_at=?, lease_expires_at=? WHERE request_key=?',
                     (stamp, lease, request_key))
        return ExecutionResult(ExecutionOutcome.RENEWED, view=_view(
            conn.execute('SELECT * FROM ask_executions WHERE request_key=?', (request_key,)).fetchone(), stamp))


def complete_execution(db_path: str, request_key: str, owner_token: str, result_json: str,
                       now: Optional[datetime] = None, *, evidences=(), history=None) -> ExecutionResult:
    """결과를 저장하고 실행을 끝낸다. 임대가 만료됐거나 토큰이 다르면 LEASE_LOST로 거부한다(늦은 저장 차단)."""
    check_text(request_key, 'request_key')
    check_text(owner_token, 'owner_token')
    if not isinstance(result_json, str) or not result_json or len(result_json.encode('utf-8')) > MAX_RESULT_BYTES:
        raise ExecutionValidationError('result_json')
    try:
        json.loads(result_json)
    except ValueError:
        raise ExecutionValidationError('result_json') from None
    fixed = _ts(now) if now is not None else None
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        stamp, moment = _at_lock(fixed)
        row, code = _owned_running(conn, request_key, owner_token, stamp)
        if row is None:
            return ExecutionResult(code)
        if evidences:
            insert_evidences(conn, row['context_id'], evidences)
        if history is not None:
            if history.context_id != row['context_id']:
                raise ExecutionValidationError('history context')
            conn.execute('SAVEPOINT ask_history')
            try:
                validate_record(history)
                saved = ensure_verification_history(conn, history)
            except (sqlite3.Error, ValueError):
                conn.execute('ROLLBACK TO ask_history')
                saved = False
            conn.execute('RELEASE ask_history')
            payload = json.loads(result_json)
            payload['verification_history'] = {'status': 'saved' if saved else 'failed'}
            result_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
            if len(result_json.encode('utf-8')) > MAX_RESULT_BYTES:
                raise ExecutionValidationError('result_json')
        conn.execute("UPDATE ask_executions SET status='completed', result_json=?, finished_at=?, "
                     'result_expires_at=? WHERE request_key=?',
                     (result_json, stamp, _ts(moment + timedelta(days=RESULT_RETENTION_DAYS)), request_key))
        return ExecutionResult(ExecutionOutcome.SAVED, view=_view(
            conn.execute('SELECT * FROM ask_executions WHERE request_key=?', (request_key,)).fetchone(), stamp))


def fail_execution(db_path: str, request_key: str, owner_token: str, failure_code: str,
                   now: Optional[datetime] = None) -> ExecutionResult:
    """실행을 실패로 기록한다. 고정 코드(대문자·숫자·밑줄)만 받는다. 만료·토큰 불일치는 LEASE_LOST."""
    check_text(request_key, 'request_key')
    check_text(owner_token, 'owner_token')
    check_failure_code(failure_code)
    fixed = _ts(now) if now is not None else None
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        stamp, _ = _at_lock(fixed)
        row, code = _owned_running(conn, request_key, owner_token, stamp)
        if row is None:
            return ExecutionResult(code)
        conn.execute("UPDATE ask_executions SET status='failed', failure_code=?, finished_at=? WHERE request_key=?",
                     (failure_code, stamp, request_key))
        return ExecutionResult(ExecutionOutcome.SAVED, view=_view(
            conn.execute('SELECT * FROM ask_executions WHERE request_key=?', (request_key,)).fetchone(), stamp))


def lookup_execution(db_path: str, request_key: str, now: Optional[datetime] = None) -> ExecutionView:
    """키로 상태를 읽는다. 읽기 전용이며 실행을 시작·재시작하지 않는다. 없으면 NOT_FOUND."""
    check_text(request_key, 'request_key')
    stamp = _ts(now)
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('SELECT * FROM ask_executions WHERE request_key=?', (request_key,)).fetchone()
    return ExecutionView(ExecutionState.NOT_FOUND) if row is None else _view(row, stamp)


def plan_execution_purge(db_path: str, now: Optional[datetime] = None) -> PurgePlan:
    """dry-run. 데이터를 바꾸지 않고 정리 대상 건수만 센다."""
    stamp = _ts(now)
    with closing(get_connection(db_path)) as conn:
        conn.execute('BEGIN')
        deletable = conn.execute('SELECT COUNT(*) FROM ask_executions WHERE key_expires_at <= ? AND NOT '
                                 "(status='running' AND lease_expires_at > ?)", (stamp, stamp)).fetchone()[0]
        clearable = conn.execute("SELECT COUNT(*) FROM ask_executions WHERE status='completed' AND "
                                 'result_json IS NOT NULL AND result_expires_at <= ? AND key_expires_at > ?',
                                 (stamp, stamp)).fetchone()[0]
        conn.rollback()
    return PurgePlan(clearable, deletable)


def purge_expired_executions(db_path: str, now: Optional[datetime] = None, batch_size: int = 200,
                             max_batches: Optional[int] = None) -> PurgeResult:
    """결과 보존이 지난 본문을 비우고, 키 보존이 지난 행을 지운다. 명시적으로 호출할 때만 실행한다.

    배치마다 짧은 트랜잭션이라 중단돼도 끝난 배치는 온전하고 다시 실행하면 이어서 한다.
    유효한 임대의 실행은 지우지 않는다. 키 기록을 지운 뒤에는 그 키가 NOT_FOUND가 된다.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
        raise ExecutionValidationError('batch_size')
    stamp = _ts(now)
    cleared = deleted = batches = 0
    while max_batches is None or batches < max_batches:
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            keys = [r[0] for r in conn.execute(
                'SELECT request_key FROM ask_executions WHERE key_expires_at <= ? AND NOT '
                "(status='running' AND lease_expires_at > ?) ORDER BY key_expires_at, request_key LIMIT ?",
                (stamp, stamp, batch_size))]
            if keys:
                conn.execute(f"DELETE FROM ask_executions WHERE request_key IN ({','.join('?' * len(keys))})", keys)
                deleted += len(keys)
            else:
                keys = [r[0] for r in conn.execute(
                    "SELECT request_key FROM ask_executions WHERE status='completed' AND result_json IS NOT NULL "
                    'AND result_expires_at <= ? ORDER BY result_expires_at, request_key LIMIT ?', (stamp, batch_size))]
                if not keys:
                    return PurgeResult(cleared, deleted, batches, True)
                conn.execute('UPDATE ask_executions SET result_json=NULL WHERE request_key IN '
                             f"({','.join('?' * len(keys))})", keys)
                cleared += len(keys)
        batches += 1
    return PurgeResult(cleared, deleted, batches, False)
