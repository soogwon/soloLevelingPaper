"""원격 worker의 현재 입력·실행 세대와 영속 전송 원장을 연결한다."""

from solo_leveling.application.provider_dispatch import AttemptRequest, DispatchIntent
from solo_leveling.domain.remote_processing import ProcessingSource
from solo_leveling.interfaces.http.dispatch_ledger import PostgresDispatchLedger
from solo_leveling.workers.remote_processing import (
    PostgresRemoteProcessingInputStore, RemoteProcessingInputRejected, _SOURCE_SQL,
)


_IDENTITY = """ AND j.paper_id=%s AND j.server_version_id=%s
    AND u.sha256_hex=%s AND u.file_size=%s"""
_AUTH_SOURCE = _SOURCE_SQL.replace(
    "SELECT u.object_key,u.file_size,u.content_type,u.sha256_hex",
    "SELECT a.input_hash,a.storage_consent_id,a.ai_consent_id,a.notice_version",
)
_USAGE_BLOCKED = """SELECT 1 FROM server_processing_provider_attempts
    WHERE owner_user_id=%s AND execution_id=%s AND (
        status IN ('reserved','unknown') OR
        actual_input_tokens>reserved_input_tokens OR
        actual_output_tokens>reserved_output_tokens) LIMIT 1"""


def _params(source):
    return (source.owner_user_id, source.job_id, source.lease_token, source.paper_id,
            source.version_id, source.pdf_sha256, source.pdf_size)


def _valid(source):
    return isinstance(source, ProcessingSource) and bool(source.lease_token)


class PostgresProcessingAuthorization(PostgresRemoteProcessingInputStore):
    """사용량 미확정은 새 단계와 전송을 막고, 조회 실패는 허용으로 바꾸지 않는다."""

    def allowed(self, source, phase):
        if not _valid(source) or phase not in ('parse', 'translation', 'embedding', 'result'):
            return False
        with self._pool.connection() as conn:
            row = conn.execute(
                _AUTH_SOURCE + _IDENTITY + " AND (%s<>'translation' OR ctl.dispatch_enabled=true)",
                (*_params(source), phase),
            ).fetchone()
            if row is None:
                return False
            return conn.execute(_USAGE_BLOCKED, (source.owner_user_id, source.job_id)).fetchone() is None

    def dispatch_intent(self, source):
        if not _valid(source):
            raise RemoteProcessingInputRejected('remote_processing_input_rejected')
        with self._pool.connection() as conn:
            row = conn.execute(_AUTH_SOURCE + _IDENTITY, _params(source)).fetchone()
        if row is None:
            raise RemoteProcessingInputRejected('remote_processing_input_rejected')
        return DispatchIntent(source.owner_user_id, source.job_id, source.lease_token,
                              row['input_hash'], row['storage_consent_id'], row['ai_consent_id'],
                              row['notice_version'])

    def ledger(self, source, check_allowed, *, policy_version, window_key, validate_limits=None):
        """check_allowed는 worker가 공급한 해당 실행의 heartbeat 포함 callback이다."""
        if not _valid(source) or not callable(check_allowed):
            raise ValueError('processing_authorization_invalid')
        expected = self.dispatch_intent(source)

        def validate(conn, intent):
            if intent != expected:
                return False
            # 논문을 먼저 잠가 삭제와 같은 잠금 순서를 사용한다.
            paper = conn.execute(
                """SELECT paper_id FROM server_papers
                   WHERE owner_user_id=%s AND paper_id=%s AND deleted_at IS NULL FOR SHARE""",
                (source.owner_user_id, source.paper_id),
            ).fetchone()
            if paper is None:
                return False
            row = conn.execute(
                _AUTH_SOURCE + _IDENTITY + """ AND a.input_hash=%s AND a.storage_consent_id=%s
                    AND a.ai_consent_id=%s AND a.notice_version=%s
                    FOR SHARE OF j,a,i,u,sb,ab,sc,ac""",
                (*_params(source), intent.input_hash, intent.storage_consent_id,
                 intent.ai_consent_id, intent.notice_version),
            ).fetchone()
            return row is not None

        class BoundLedger(PostgresDispatchLedger):
            _attempt_table = "server_processing_provider_attempts"

            def authorize(self, request):
                # DB callback은 연결을 빌리기 전에 실행해 중첩 pool 대기를 피한다.
                if (not isinstance(request, AttemptRequest) or request.stage != 'translation'
                        or request.intent != expected):
                    return None
                if check_allowed(source, 'translation') is not True:
                    return None
                return super().authorize(request)

        return BoundLedger(self._pool, policy_version, window_key, validate, validate_limits=validate_limits)
