"""소유자·revision·요청 키로 취소를 직렬화한다. 외부 실행과 예산 반환은 하지 않는다."""
from uuid import UUID

from solo_leveling.interfaces.http.processing_jobs import ProcessingJobNotFound, ProcessingJobConflict


class PostgresProcessingCancellationStore:
    def __init__(self, pool):
        self._pool = pool

    def cancel(self, owner_user_id, job_id, request_key, expected_revision):
        try:
            job_id, request_key = str(UUID(job_id)), str(UUID(request_key))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('cancellation_argument_invalid') from None
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**63:
            raise ValueError('cancellation_argument_invalid')
        with self._pool.connection() as conn:
            # 서로 다른 작업에 같은 키가 동시에 사용되는 경우까지 직렬화한다.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                         (f'processing-cancel:{owner_user_id}:{request_key}',))
            paper = conn.execute('''SELECT p.paper_id FROM server_papers p
                JOIN server_processing_jobs j ON j.owner_user_id=p.owner_user_id AND j.paper_id=p.paper_id
                WHERE j.owner_user_id=%s AND j.job_id=%s AND p.deleted_at IS NULL
                FOR SHARE OF p''', (owner_user_id, job_id)).fetchone()
            if paper is None:
                raise ProcessingJobNotFound('processing_job_not_found')
            job = conn.execute('''SELECT status,revision FROM server_processing_jobs
                WHERE owner_user_id=%s AND job_id=%s FOR UPDATE''', (owner_user_id, job_id)).fetchone()
            prior = conn.execute('''SELECT job_id,expected_revision,applied_revision
                FROM server_processing_cancel_requests WHERE owner_user_id=%s AND request_key=%s''',
                (owner_user_id, request_key)).fetchone()
            if prior is not None:
                if str(prior['job_id']) != job_id or prior['expected_revision'] != expected_revision:
                    raise ProcessingJobConflict('cancellation_request_key_conflict')
                return self._result(job_id, job, prior['applied_revision'])
            if job['revision'] != expected_revision:
                raise ProcessingJobConflict('processing_job_revision_conflict')
            # 이미 종료된 작업을 취소 성공으로 기록하거나 기존 게시물을 삭제하지 않는다.
            if job['status'] not in ('queued', 'running'):
                raise ProcessingJobConflict('processing_job_not_cancellable')
            published = conn.execute('''SELECT 1 FROM server_processing_publications
                WHERE owner_user_id=%s AND source_job_id=%s LIMIT 1''', (owner_user_id, job_id)).fetchone()
            if published:
                raise ProcessingJobConflict('processing_job_already_published')
            changed = conn.execute('''UPDATE server_processing_jobs
                SET status='cancelled',retryable=false,lease_owner=NULL,lease_token=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,finished_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE owner_user_id=%s AND job_id=%s RETURNING status,revision''',
                (owner_user_id, job_id)).fetchone()
            conn.execute('''INSERT INTO server_processing_cancel_requests
                (owner_user_id,request_key,job_id,expected_revision,applied_revision)
                VALUES (%s,%s,%s,%s,%s)''',
                (owner_user_id, request_key, job_id, expected_revision, changed['revision']))
            return self._result(job_id, changed, changed['revision'])

    @staticmethod
    def _result(job_id, current, applied_revision):
        return {'job_id': job_id, 'status': current['status'], 'revision': current['revision'],
                'applied_revision': applied_revision,
                'notice': '새 실행과 게시를 차단했습니다. 이미 전송된 외부 호출·발생 비용은 되돌리지 않으며 비용 확정은 별도 원장을 따릅니다.'}
