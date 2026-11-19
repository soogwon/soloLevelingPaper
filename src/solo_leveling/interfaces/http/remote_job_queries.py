"""처리 작업의 안전한 조회 전용 투영. 실행·재시도·취소 포트는 없다."""
from uuid import UUID


_STATUSES = {'queued': 'queued', 'running': 'processing', 'succeeded': 'completed',
             'failed': 'failed', 'cancelled': 'cancelled'}
_SAFE_ERRORS = frozenset({'max_attempts_exhausted', 'lease_expired_not_retryable'})
_SELECT = '''SELECT j.job_id,j.paper_id,j.server_version_id,j.job_kind,j.status,
    j.attempt,j.revision,j.error_code,j.created_at,j.updated_at,j.finished_at,p.title,u.file_name,
    pub.publication_id,pub.embedding_set_id,
    NULL::uuid AS context_id,NULL::uuid AS request_key,NULL::uuid AS selected_context_id
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


_RECOVERY_SELECT = """SELECT r.job_id,c.paper_id,c.server_version_id,r.operation AS job_kind,r.state AS status,
    NULL::integer AS attempt,NULL::integer AS revision,NULL::text AS error_code,
    r.created_at,NULL::timestamptz AS updated_at,NULL::timestamptz AS finished_at,
    p.title,NULL::text AS file_name,pub.publication_id,pub.embedding_set_id,
    r.context_id,r.request_key,r.selected_context_id
    FROM server_reprocessing_reviews r
    JOIN server_execution_contexts c ON c.owner_user_id=r.owner_user_id AND c.context_id=r.context_id
    JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
    LEFT JOIN server_processing_publications pub ON pub.owner_user_id=c.owner_user_id
      AND pub.paper_id=c.paper_id AND pub.server_version_id=c.server_version_id
      AND pub.publication_id=r.publication_id AND r.state='published'
    WHERE r.owner_user_id=%s AND p.deleted_at IS NULL AND r.job_id IS NOT NULL AND r.state<>'review'"""
_RECOVERY_STATUS = dict(_STATUSES, running='processing', staged='publication_pending', published='published')


def _recovery_view(row):
    result = {key: str(row[key]) if row[key] is not None else None for key in (
        'job_id', 'paper_id', 'server_version_id', 'job_kind', 'title', 'publication_id',
        'embedding_set_id', 'selected_context_id')}
    result.update(
        status=_RECOVERY_STATUS[row['status']], attempt=None, revision=None,
        file_name=None, created_at=row['created_at'].isoformat(), updated_at=None, finished_at=None,
        error_code='processing_failed' if row['status']=='failed' else None,
        cancellation_available=False,
        cancellation_notice='취소 가능 여부는 웹 검토 화면에서 확인하세요.',
        learning_notice='후보 게시 후 웹 검토 화면에서 새 색인을 선택할 수 있습니다.',
        web_path=f"/#/reprocessing/{row['context_id']}/{row['job_kind']}?key={row['request_key']}",
    )
    return result


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
    def __init__(self, pool, owner, cancellation_enabled=False, *, reprocessing_enabled=False):
        self.pool, self.owner = pool, owner
        self.cancellation_enabled = cancellation_enabled
        self.reprocessing_enabled = reprocessing_enabled

    def _source(self, owner):
        if not self.reprocessing_enabled:
            return '(' + _SELECT + ') q', (owner,)
        normal = _SELECT
        # 게시 때 만들어지는 내부 embed 행 대신 동일 ID의 복구 원장을 보여준다.
        normal += """ AND NOT EXISTS (SELECT 1 FROM server_reprocessing_reviews r
                    WHERE r.owner_user_id=j.owner_user_id AND r.job_id=j.job_id)"""
        return '(' + normal + ' UNION ALL ' + _RECOVERY_SELECT + ') q', (owner, owner)

    def _view(self, row, cancellation_enabled=False):
        if row.get('context_id') is not None:
            return _recovery_view(row)
        return _view(row, cancellation_enabled)

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
                source, params = self._source(owner)
                rows = conn.execute('SELECT * FROM ' + source + ' WHERE (%s::uuid IS NULL OR q.job_id>%s::uuid)'
                    + ' ORDER BY q.job_id LIMIT %s', (*params, after, after, limit + 1)).fetchall()
            return {'status': 'ok', 'jobs': [self._view(r, self.cancellation_enabled) for r in rows[:limit]],
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
                source, params = self._source(owner)
                rows = conn.execute('SELECT * FROM ' + source + ' WHERE q.paper_id=%s AND q.server_version_id=%s'
                    + ' AND (%s::uuid IS NULL OR q.job_id>%s::uuid) ORDER BY q.job_id LIMIT %s',
                    (*params, paper_id, version_id, after, after, limit + 1)).fetchall()
            return {'status': 'ok', 'jobs': [self._view(r) for r in rows[:limit]],
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
                source, params = self._source(owner)
                row = conn.execute('SELECT * FROM ' + source + ' WHERE q.job_id=%s', (*params, job_id)).fetchone()
            return {'status': 'ok', 'result': self._view(row, self.cancellation_enabled)} if row else {'status': 'not_found'}
        except Exception:
            return {'status': 'unavailable'}
