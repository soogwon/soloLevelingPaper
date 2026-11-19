"""처리 작업의 안전한 조회 전용 투영. 실행·재시도·취소 포트는 없다."""
from uuid import UUID


_STATUSES = {'queued': 'queued', 'running': 'processing', 'succeeded': 'completed',
             'failed': 'failed', 'cancelled': 'cancelled'}
_SAFE_ERRORS = frozenset({'max_attempts_exhausted', 'lease_expired_not_retryable'})
_SELECT = '''SELECT j.job_id,j.paper_id,j.server_version_id,j.job_kind,j.status,
    j.attempt,j.revision,j.error_code,j.created_at,j.updated_at,j.finished_at,p.title,u.file_name,
    pub.publication_id,pub.embedding_set_id
    FROM server_processing_jobs j
    JOIN server_papers p ON p.owner_user_id=j.owner_user_id AND p.paper_id=j.paper_id
    LEFT JOIN server_processing_job_authorizations a
      ON a.owner_user_id=j.owner_user_id AND a.job_id=j.job_id
    LEFT JOIN server_remote_upload_intents u
      ON u.owner_user_id=a.owner_user_id AND u.intent_id=a.upload_intent_id
      AND u.paper_id=j.paper_id AND u.server_version_id=j.server_version_id
    LEFT JOIN LATERAL (
      SELECT publication_id,embedding_set_id FROM server_processing_publications
      WHERE owner_user_id=j.owner_user_id AND paper_id=j.paper_id
        AND server_version_id=j.server_version_id AND source_job_id=j.job_id
      ORDER BY published_at DESC,publication_id DESC LIMIT 1
    ) pub ON j.status='succeeded'
    WHERE j.owner_user_id=%s AND p.deleted_at IS NULL'''


def _view(row, cancellation_enabled=False):
    result = {k: str(row[k]) if row[k] is not None else None for k in (
        'job_id', 'paper_id', 'server_version_id', 'job_kind', 'title', 'file_name',
        'publication_id', 'embedding_set_id')}
    result.update(status=_STATUSES[row['status']], attempt=row['attempt'], revision=row['revision'],
                  error_code=(row['error_code'] if row['error_code'] in _SAFE_ERRORS
                              else 'processing_failed' if row['error_code'] else None),
                  cancellation_available=cancellation_enabled and row['status'] in ('queued', 'running'),
                  cancellation_notice='취소 가능 여부와 사용자 확인은 별도 취소 준비 도구에서 다시 검사합니다. 이미 발생한 외부 호출·비용은 되돌릴 수 없습니다.',
                  learning_notice='처리 완료는 학습 완료가 아닙니다.')
    result.update({k: row[k].isoformat() if row[k] else None
                   for k in ('created_at', 'updated_at', 'finished_at')})
    result['web_path'] = f"/#/papers/{result['paper_id']}" if result['publication_id'] else None
    return result


class RemoteJobQueries:
    def __init__(self, pool, owner, cancellation_enabled=False):
        self.pool, self.owner = pool, owner
        self.cancellation_enabled = cancellation_enabled

    def list(self, after=None, limit=20):
        try:
            if type(limit) is not int or not 1 <= limit <= 50:
                return {'status': 'invalid_argument'}
            after = str(UUID(after)) if after is not None else None
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        try:
            owner = self.owner()
            if owner is None:
                return {'status': 'not_found'}
            with self.pool.connection() as conn:
                # UUID 기준 커서는 상태가 바뀌어도 중복·누락을 만들지 않는다.
                rows = conn.execute(_SELECT + ' AND (%s::uuid IS NULL OR j.job_id>%s::uuid)'
                    + ' ORDER BY j.job_id LIMIT %s', (owner, after, after, limit + 1)).fetchall()
            return {'status': 'ok', 'jobs': [_view(r, self.cancellation_enabled) for r in rows[:limit]],
                    'next_after': str(rows[limit - 1]['job_id']) if len(rows) > limit else None}
        except Exception:
            return {'status': 'unavailable'}

    def for_paper(self, paper_id, version_id, after=None, limit=20):
        try:
            paper_id = str(UUID(paper_id))
            after = str(UUID(after)) if after is not None else None
            if (not isinstance(version_id, str) or not version_id.strip()
                    or len(version_id) > 128 or "\x00" in version_id
                    or type(limit) is not int or not 1 <= limit <= 50):
                return {'status': 'invalid_argument'}
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        try:
            owner = self.owner()
            if owner is None:
                return {'status': 'not_found'}
            with self.pool.connection() as conn:
                exists = conn.execute("""SELECT 1 FROM server_processing_versions v
                    JOIN server_papers p ON p.owner_user_id=v.owner_user_id AND p.paper_id=v.paper_id
                    WHERE v.owner_user_id=%s AND v.paper_id=%s AND v.server_version_id=%s
                      AND p.deleted_at IS NULL""", (owner, paper_id, version_id)).fetchone()
                if not exists:
                    return {'status': 'not_found'}
                rows = conn.execute(_SELECT + ' AND j.paper_id=%s AND j.server_version_id=%s'
                    + ' AND (%s::uuid IS NULL OR j.job_id>%s::uuid) ORDER BY j.job_id LIMIT %s',
                    (owner, paper_id, version_id, after, after, limit + 1)).fetchall()
            return {'status': 'ok', 'jobs': [_view(r) for r in rows[:limit]],
                    'next_after': str(rows[limit - 1]['job_id']) if len(rows) > limit else None}
        except Exception:
            return {'status': 'unavailable'}

    def get(self, job_id):
        try:
            job_id = str(UUID(job_id))
        except (ValueError, TypeError, AttributeError):
            return {'status': 'invalid_argument'}
        try:
            owner = self.owner()
            if owner is None:
                return {'status': 'not_found'}
            with self.pool.connection() as conn:
                row = conn.execute(_SELECT + ' AND j.job_id=%s', (owner, job_id)).fetchone()
            return {'status': 'ok', 'result': _view(row, self.cancellation_enabled)} if row else {'status': 'not_found'}
        except Exception:
            return {'status': 'unavailable'}
