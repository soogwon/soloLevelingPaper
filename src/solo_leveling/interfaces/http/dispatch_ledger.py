"""PostgreSQL 기반 제공자 전송 예약·사용량 원장."""

from collections.abc import Callable
from uuid import uuid4

from solo_leveling.application.provider_dispatch import AttemptPermit, AttemptRequest


class PostgresDispatchLedger:
    """정책 숫자를 만들지 않고 미리 설정된 limit 행을 원자적으로 소비한다.

    ``validate_scope``는 같은 connection에서 execution/lease/tombstone을 잠그고 현재
    전송 세대이면 True를 반환해야 한다. 저장소별 실행 모델을 이 adapter가 추측하지 않는다.
    """

    _attempt_table = "server_provider_attempts"
    _question_reservations = False

    def __init__(self, pool, policy_version: str, window_key: str, validate_scope: Callable, *, validate_limits=None):
        if not all(isinstance(value, str) and value.strip() for value in (policy_version, window_key)):
            raise ValueError("dispatch_policy_invalid")
        if not callable(validate_scope):
            raise ValueError("dispatch_scope_validator_invalid")
        if self._attempt_table not in {"server_provider_attempts", "server_processing_provider_attempts"}:
            raise ValueError("dispatch_ledger_invalid")
        if validate_limits is not None and not callable(validate_limits):
            raise ValueError('dispatch_limits_validator_invalid')
        self._validate_limits = validate_limits
        self._pool = pool
        self._policy_version = policy_version
        self._window_key = window_key
        self._validate_scope = validate_scope

    def authorize(self, request: AttemptRequest) -> AttemptPermit | None:
        if not isinstance(request, AttemptRequest):
            raise ValueError("dispatch_request_invalid")
        intent = request.intent
        attempt_id = str(uuid4())
        with self._pool.connection() as conn:
            if not self._validate_scope(conn, intent):
                return None
            control = conn.execute(
                """SELECT generation,dispatch_enabled FROM server_dispatch_controls
                   WHERE control_id='provider_dispatch' FOR UPDATE"""
            ).fetchone()
            if control is None or not control["dispatch_enabled"]:
                return None
            # 재시작·lease 교체·새 전송 키로 미정산 또는 초과 사용을 우회하지 못하게 한다.
            blocked = conn.execute(
                f"""SELECT 1 FROM {self._attempt_table}
                   WHERE owner_user_id=%s AND execution_id=%s AND (
                       status IN ('reserved','unknown') OR
                       actual_input_tokens>reserved_input_tokens OR
                       actual_output_tokens>reserved_output_tokens)
                   LIMIT 1""",
                (intent.owner_user_id, intent.execution_id),
            ).fetchone()
            if blocked is not None:
                return None
            consents = conn.execute(
                """SELECT consent_id,purpose,input_hash FROM server_remote_consents
                   WHERE owner_user_id=%s AND consent_id IN (%s,%s)
                     AND notice_version=%s AND revoked_at IS NULL
                   FOR SHARE""",
                (
                    intent.owner_user_id,
                    intent.storage_consent_id,
                    intent.ai_consent_id,
                    intent.notice_version,
                ),
            ).fetchall()
            records = {row["consent_id"]: row for row in consents}
            if (
                records.get(intent.storage_consent_id, {}).get("purpose") != "storage"
                or records.get(intent.ai_consent_id, {}).get("purpose") != "ai_provider"
                or records[intent.ai_consent_id]["input_hash"] != intent.input_hash
            ):
                return None
            reservation = None
            if self._question_reservations:
                reservation = conn.execute("SELECT * FROM server_question_budget_reservations WHERE owner_user_id=%s AND execution_id=%s FOR UPDATE",
                    (intent.owner_user_id,intent.execution_id)).fetchone()
                if reservation is not None and (
                        reservation['policy_version']!=self._policy_version or reservation['window_key']!=self._window_key
                        or reservation['remaining_input_tokens']<request.input_token_upper_bound
                        or reservation['remaining_output_tokens']<request.max_output_tokens):
                    return None
            limits = conn.execute(
                """SELECT scope_type,scope_id,max_input_tokens,max_output_tokens,
                          reserved_input_tokens,reserved_output_tokens,
                          consumed_input_tokens,consumed_output_tokens
                   FROM server_dispatch_budget_limits
                   WHERE policy_version=%s AND window_key=%s AND (
                       (scope_type='service' AND scope_id='service') OR
                       (scope_type='owner' AND scope_id=%s))
                   ORDER BY scope_type,scope_id FOR UPDATE""",
                (self._policy_version, self._window_key, intent.owner_user_id),
            ).fetchall()
            if {row["scope_type"] for row in limits} != {"service", "owner"}:
                return None
            if self._validate_limits is not None and self._validate_limits(limits) is not True:
                return None
            if any(
                row["reserved_input_tokens"] + row["consumed_input_tokens"] + (0 if reservation is not None else request.input_token_upper_bound)
                > row["max_input_tokens"]
                or row["reserved_output_tokens"] + row["consumed_output_tokens"] + (0 if reservation is not None else request.max_output_tokens)
                > row["max_output_tokens"]
                for row in limits
            ):
                return None
            row = conn.execute(
                f"""INSERT INTO {self._attempt_table}
                       (attempt_id,owner_user_id,execution_id,lease_token,input_hash,
                        storage_consent_id,ai_consent_id,notice_version,dispatch_key,stage,
                        policy_version,window_key,control_generation,reserved_input_tokens,
                        reserved_output_tokens,status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved')
                   ON CONFLICT (owner_user_id,execution_id,dispatch_key) DO NOTHING
                   RETURNING attempt_id""",
                (
                    attempt_id,
                    intent.owner_user_id,
                    intent.execution_id,
                    intent.lease_token,
                    intent.input_hash,
                    intent.storage_consent_id,
                    intent.ai_consent_id,
                    intent.notice_version,
                    request.dispatch_key,
                    request.stage,
                    self._policy_version,
                    self._window_key,
                    control["generation"],
                    request.input_token_upper_bound,
                    request.max_output_tokens,
                ),
            ).fetchone()
            if row is None:
                return None
            if reservation is not None:
                conn.execute("""UPDATE server_question_budget_reservations SET
                    remaining_input_tokens=remaining_input_tokens-%s,remaining_output_tokens=remaining_output_tokens-%s
                    WHERE owner_user_id=%s AND execution_id=%s""",
                    (request.input_token_upper_bound,request.max_output_tokens,intent.owner_user_id,intent.execution_id))
            else:
                conn.execute(
                    """UPDATE server_dispatch_budget_limits
                       SET reserved_input_tokens=reserved_input_tokens+%s,
                           reserved_output_tokens=reserved_output_tokens+%s,
                           updated_at=CURRENT_TIMESTAMP
                       WHERE policy_version=%s AND window_key=%s AND (
                           (scope_type='service' AND scope_id='service') OR
                           (scope_type='owner' AND scope_id=%s))""",
                    (
                        request.input_token_upper_bound,
                        request.max_output_tokens,
                        self._policy_version,
                        self._window_key,
                        intent.owner_user_id,
                    ),
                )
        return AttemptPermit(attempt_id, request)

    def observe(self, attempt_id: str, input_tokens: int, output_tokens: int) -> None:
        if not _usage_valid(attempt_id, input_tokens, output_tokens):
            raise ValueError("dispatch_usage_invalid")
        with self._pool.connection() as conn:
            attempt = _lock_attempt(conn, attempt_id, self._attempt_table)
            if attempt["status"] == "observed":
                if (attempt["actual_input_tokens"], attempt["actual_output_tokens"]) != (input_tokens, output_tokens):
                    raise ValueError("dispatch_usage_conflict")
                return
            conn.execute(
                f"""UPDATE {self._attempt_table}
                   SET status='observed',actual_input_tokens=%s,actual_output_tokens=%s,
                       settled_at=CURRENT_TIMESTAMP
                   WHERE attempt_id=%s AND status IN ('reserved','unknown')""",
                (input_tokens, output_tokens, attempt_id),
            )
            _settle_limits(conn, attempt, input_tokens, output_tokens)

    def unknown(self, attempt_id: str) -> None:
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("dispatch_attempt_invalid")
        with self._pool.connection() as conn:
            attempt = _lock_attempt(conn, attempt_id, self._attempt_table)
            if attempt["status"] in {"unknown", "observed"}:
                return
            conn.execute(
                f"""UPDATE {self._attempt_table} SET status='unknown',settled_at=CURRENT_TIMESTAMP
                   WHERE attempt_id=%s AND status='reserved'""",
                (attempt_id,),
            )
            # unknown은 예약을 반환하거나 소비로 추정하지 않는다.


def _lock_attempt(conn, attempt_id, table):
    row = conn.execute(
        f"""SELECT status,owner_user_id,policy_version,window_key,reserved_input_tokens,reserved_output_tokens,
                  actual_input_tokens,actual_output_tokens
           FROM {table} WHERE attempt_id=%s FOR UPDATE""",
        (attempt_id,),
    ).fetchone()
    if row is None:
        raise ValueError("dispatch_attempt_not_found")
    return row


def _settle_limits(conn, attempt, input_tokens, output_tokens):
    conn.execute(
        """UPDATE server_dispatch_budget_limits
           SET reserved_input_tokens=reserved_input_tokens-%s,
               reserved_output_tokens=reserved_output_tokens-%s,
               consumed_input_tokens=consumed_input_tokens+%s,
               consumed_output_tokens=consumed_output_tokens+%s,
               updated_at=CURRENT_TIMESTAMP
           WHERE policy_version=%s AND window_key=%s AND (
               (scope_type='service' AND scope_id='service') OR
               (scope_type='owner' AND scope_id=%s))""",
        (
            attempt["reserved_input_tokens"],
            attempt["reserved_output_tokens"],
            input_tokens,
            output_tokens,
            attempt["policy_version"],
            attempt["window_key"],
            attempt["owner_user_id"],
        ),
    )


def _usage_valid(attempt_id, input_tokens, output_tokens):
    return (
        isinstance(attempt_id, str)
        and bool(attempt_id.strip())
        and type(input_tokens) is int
        and input_tokens >= 0
        and type(output_tokens) is int
        and output_tokens >= 0
    )


class PostgresQuestionDispatchLedger(PostgresDispatchLedger):
    """질문 제품 경로만 접수 시 예약한 토큰 할당을 사용한다."""
    _question_reservations = True
