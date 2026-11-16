"""원격 논문 삭제 요청을 tombstone과 작업 취소로 원자 접수한다."""

from dataclasses import dataclass
from uuid import uuid4

from solo_leveling.domain.deletion_policy import DeletionPolicy


@dataclass(frozen=True)
class DeletionRequest:
    request_id: str
    paper_id: str
    status: str


@dataclass(frozen=True, repr=False)
class DeletionCleanup:
    request_id: str
    owner_user_id: str
    paper_id: str
    attempt_count: int
    lease_token: str


class PostgresRemoteDeletionStore:
    def __init__(self, pool):
        self._pool = pool

    def request(self, owner_user_id: str, paper_id: str, request_key: str) -> DeletionRequest | None:
        if not all(isinstance(v, str) and v.strip() for v in (owner_user_id, paper_id, request_key)):
            raise ValueError("remote_deletion_input_invalid")
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (owner_user_id,))
            old = conn.execute(
                """SELECT request_id,paper_id,status FROM server_deletion_requests
                   WHERE owner_user_id=%s AND (request_key=%s OR paper_id=%s)
                   ORDER BY (request_key=%s) DESC LIMIT 1 FOR UPDATE""",
                (owner_user_id, request_key, paper_id, request_key),
            ).fetchone()
            if old:
                if str(old["paper_id"]) != paper_id:
                    raise ValueError("remote_deletion_request_conflict")
                return _view(old)
            paper = conn.execute(
                """SELECT paper_id FROM server_papers
                   WHERE owner_user_id=%s AND paper_id=%s FOR UPDATE""",
                (owner_user_id, paper_id),
            ).fetchone()
            if paper is None:
                return None
            conn.execute(
                """UPDATE server_processing_jobs SET status='cancelled',retryable=false,error_code='paper_tombstoned',
                          lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
                          finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND paper_id=%s AND status IN ('queued','running')""",
                (owner_user_id, paper_id),
            )
            conn.execute(
                """UPDATE server_papers SET deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND paper_id=%s""",
                (owner_user_id, paper_id),
            )
            row = conn.execute(
                """INSERT INTO server_deletion_requests(request_id,owner_user_id,paper_id,request_key,status)
                   VALUES (%s,%s,%s,%s,'blocking') RETURNING request_id,paper_id,status""",
                (str(uuid4()), owner_user_id, paper_id, request_key),
            ).fetchone()
        return _view(row)

    def get_existing(self, owner_user_id: str, paper_id: str, request_key: str) -> DeletionRequest | None:
        """정확히 일치하는 기존 요청만 읽는다. 접수·재시도·잠금 갱신은 하지 않는다."""
        if not all(isinstance(v, str) and v.strip() for v in (owner_user_id, paper_id, request_key)):
            raise ValueError("remote_deletion_input_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT request_id,paper_id,status FROM server_deletion_requests
                   WHERE owner_user_id=%s AND paper_id=%s AND request_key=%s""",
                (owner_user_id, paper_id, request_key),
            ).fetchone()
        return None if row is None else _view(row)

    def claim_cleanup(self, worker_id: str, *, lease_seconds: int) -> DeletionCleanup | None:
        _lease_input(worker_id, lease_seconds)
        token = str(uuid4())
        with self._pool.connection() as conn:
            row = conn.execute(
                """WITH candidate AS (
                       SELECT request_id FROM server_deletion_requests
                       WHERE status IN ('blocking','failed') OR
                         (status='cleaning' AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp()))
                       ORDER BY updated_at,request_id LIMIT 1 FOR UPDATE SKIP LOCKED
                   )
                   UPDATE server_deletion_requests d
                   SET status='cleaning',attempt_count=attempt_count+1,last_error_code=NULL,
                       lease_owner=%s,lease_token=%s,
                       lease_expires_at=clock_timestamp()+%s*interval '1 second',
                       heartbeat_at=clock_timestamp(),updated_at=clock_timestamp()
                   FROM candidate c WHERE d.request_id=c.request_id
                   RETURNING d.request_id,d.owner_user_id,d.paper_id,d.attempt_count,d.lease_token""",
                (worker_id, token, lease_seconds),
            ).fetchone()
        if row is None:
            return None
        return DeletionCleanup(str(row["request_id"]), row["owner_user_id"],
                               str(row["paper_id"]), int(row["attempt_count"]), str(row["lease_token"]))

    def heartbeat_cleanup(self, cleanup: DeletionCleanup, *, lease_seconds: int) -> None:
        _lease_input('heartbeat', lease_seconds)
        with self._pool.connection() as conn:
            self._lock_cleanup(conn, cleanup)
            changed = conn.execute("""UPDATE server_deletion_requests SET
                lease_expires_at=clock_timestamp()+%s*interval '1 second',heartbeat_at=clock_timestamp()
                WHERE request_id=%s AND lease_expires_at>clock_timestamp() RETURNING request_id""",
                (lease_seconds, cleanup.request_id)).fetchone()
            _require_changed(changed)

    @staticmethod
    def _lock_cleanup(conn, cleanup):
        if not isinstance(cleanup, DeletionCleanup):
            raise ValueError('remote_deletion_cleanup_stale')
        # 잠금을 기다리는 동안 만료될 수 있어 잠금 획득 뒤 실시간으로 다시 검사한다.
        conn.execute('SELECT request_id FROM server_deletion_requests WHERE request_id=%s FOR UPDATE',
                     (cleanup.request_id,)).fetchone()
        row = conn.execute("""SELECT derived_deleted_at,source_deleted_at FROM server_deletion_requests
            WHERE request_id=%s AND owner_user_id=%s AND paper_id=%s AND status='cleaning'
              AND attempt_count=%s AND lease_token=%s AND lease_expires_at>clock_timestamp()""",
            (cleanup.request_id, cleanup.owner_user_id, cleanup.paper_id,
             cleanup.attempt_count, cleanup.lease_token)).fetchone()
        if row is None:
            raise ValueError('remote_deletion_cleanup_stale')
        return row

    def run_cleanup_phase(self, cleanup: DeletionCleanup, phase: str, operation) -> None:
        columns = {'derived': 'derived_deleted_at', 'source': 'source_deleted_at'}
        if phase not in columns or not callable(operation):
            raise ValueError('remote_deletion_phase_invalid')
        column = columns[phase]
        with self._pool.connection() as conn:
            row = self._lock_cleanup(conn, cleanup)
            if row[column] is not None:
                return
            if phase == 'source' and row['derived_deleted_at'] is None:
                raise ValueError('remote_deletion_phase_invalid')
            # 동기 backend 호출과 완료 표식까지 잠금을 유지해 만료 재접수와 겹치지 않게 한다.
            operation()
            self._lock_cleanup(conn, cleanup)
            changed = conn.execute(f'''UPDATE server_deletion_requests SET {column}=clock_timestamp()
                WHERE request_id=%s AND lease_expires_at>clock_timestamp() RETURNING request_id''',
                (cleanup.request_id,)).fetchone()
            _require_changed(changed)

    def mark_cleanup_failed(self, cleanup: DeletionCleanup, error_code: str) -> None:
        if error_code != "physical_cleanup_failed":
            raise ValueError("remote_deletion_error_invalid")
        with self._pool.connection() as conn:
            self._lock_cleanup(conn, cleanup)
            changed = conn.execute("""UPDATE server_deletion_requests
                SET status='failed',last_error_code=%s,updated_at=clock_timestamp(),
                    lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL
                WHERE request_id=%s AND lease_expires_at>clock_timestamp() RETURNING request_id""",
                (error_code, cleanup.request_id)).fetchone()
            _require_changed(changed)

    def mark_cleanup_complete(self, cleanup: DeletionCleanup) -> None:
        with self._pool.connection() as conn:
            row = self._lock_cleanup(conn, cleanup)
            if row['derived_deleted_at'] is None or row['source_deleted_at'] is None:
                raise ValueError('remote_deletion_phase_incomplete')
            changed = conn.execute("""UPDATE server_deletion_requests
                SET status='complete',last_error_code=NULL,completed_at=clock_timestamp(),
                    updated_at=clock_timestamp(),lease_owner=NULL,lease_token=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL WHERE request_id=%s
                AND lease_expires_at>clock_timestamp() RETURNING request_id""", (cleanup.request_id,)).fetchone()
            _require_changed(changed)


class RemoteDeletionCleanupService:
    """tombstone 이후 저장소별 물리 정리를 재시도 가능한 한 실행으로 묶는다."""

    def __init__(self, store, backend, policy: DeletionPolicy, *, worker_id: str, lease_seconds: int):
        _lease_input(worker_id, lease_seconds)
        self.store, self.backend, self.policy = store, backend, policy
        self.worker_id, self.lease_seconds = worker_id, lease_seconds

    def run_once(self) -> dict[str, object]:
        self.policy.validate_for_physical_deletion()
        cleanup = self.store.claim_cleanup(self.worker_id, lease_seconds=self.lease_seconds)
        if cleanup is None:
            return {"status": "idle"}
        try:
            for phase, operation in [('derived', self.backend.delete_derived), ('source', self.backend.delete_source)]:
                self.store.heartbeat_cleanup(cleanup, lease_seconds=self.lease_seconds)
                self.store.run_cleanup_phase(cleanup, phase,
                    lambda: operation(cleanup.owner_user_id, cleanup.paper_id))
        except Exception:
            code = "physical_cleanup_failed"
            self.store.mark_cleanup_failed(cleanup, code)
            return {"status": "failed", "attempt_count": cleanup.attempt_count,
                    "error_code": code}
        self.store.mark_cleanup_complete(cleanup)
        return {"status": "complete", "attempt_count": cleanup.attempt_count}


def _view(row):
    return DeletionRequest(str(row["request_id"]), str(row["paper_id"]), row["status"])


def _lease_input(worker_id, lease_seconds):
    if not isinstance(worker_id, str) or not worker_id.strip() or type(lease_seconds) is not int or lease_seconds < 1:
        raise ValueError('remote_deletion_lease_invalid')


def _require_changed(row):
    if row is None:
        raise ValueError('remote_deletion_cleanup_stale')
