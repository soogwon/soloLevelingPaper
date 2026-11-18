"""사용자가 인증 웹에서 선택한 학습 맥락별 서버 저장 동의."""
from uuid import uuid4
from .remote_learning import PostgresRemoteLearningStore

NOTICE_VERSION = 'remote-annotation-storage-v1'
NOTICE = ('이 학습 맥락의 메모·하이라이트 본문과 원문·번역 인용 구간을 서버에 저장하고 '
          '같은 계정의 웹·다른 기기에서 조회합니다. 동의 후 작성·수정할 수 있으며 언제든 철회할 수 있습니다. '
          '철회하면 새 작성·수정은 중단되고 기존 기록 조회·삭제는 유지됩니다. '
          'PDF 보관이나 외부 AI 전송에 동의하는 것은 아닙니다.')


class AnnotationStorageConsentRequired(ValueError):
    """현재 고지의 저장 동의를 확인해야 한다."""


def require_annotation_consent(conn, owner, context):
    """호출자는 논문 부모 잠금을 보유해 철회와 저장을 직렬화한다."""
    row = conn.execute('''SELECT notice_version,revoked_at FROM server_annotation_consents
        WHERE owner_user_id=%s AND context_id=%s ORDER BY revision DESC LIMIT 1''',
        (owner, context)).fetchone()
    if row is None or row['notice_version'] != NOTICE_VERSION or row['revoked_at'] is not None:
        raise AnnotationStorageConsentRequired('annotation_storage_consent_required')


class PostgresAnnotationConsentStore(PostgresRemoteLearningStore):
    @staticmethod
    def _latest(conn, owner, context):
        return conn.execute('''SELECT * FROM server_annotation_consents
            WHERE owner_user_id=%s AND context_id=%s ORDER BY revision DESC LIMIT 1''',
            (owner, context)).fetchone()

    @staticmethod
    def _view(row):
        return {'notice_version': NOTICE_VERSION, 'notice': NOTICE,
                'consent_id': str(row['consent_id']) if row else None,
                'revision': row['revision'] if row else 0,
                'granted': bool(row and row['revoked_at'] is None and row['notice_version'] == NOTICE_VERSION),
                'granted_at': row['granted_at'] if row else None,
                'revoked_at': row['revoked_at'] if row else None}

    def view(self, owner, context):
        context = self._scope(owner, context)
        with self._pool.connection() as conn:
            if self._read(conn, owner, context) is None:
                return None
            return self._view(self._latest(conn, owner, context))

    def grant(self, owner, context, notice_version):
        if notice_version != NOTICE_VERSION:
            raise ValueError('annotation_notice_changed')
        context = self._scope(owner, context)
        with self._pool.connection() as conn:
            if self._lock(conn, owner, context) is None or self._read(conn, owner, context) is None:
                return None
            previous = self._latest(conn, owner, context)
            if not self._view(previous)['granted']:
                previous = conn.execute('''INSERT INTO server_annotation_consents
                    (consent_id,owner_user_id,context_id,revision,notice_version)
                    VALUES (%s,%s,%s,%s,%s) RETURNING *''',
                    (str(uuid4()), owner, context, previous['revision']+1 if previous else 1, NOTICE_VERSION)).fetchone()
            return self._view(previous)

    def revoke(self, owner, context, consent_id):
        context = self._scope(owner, context)
        with self._pool.connection() as conn:
            if self._lock(conn, owner, context) is None or self._read(conn, owner, context) is None:
                return None
            previous = self._latest(conn, owner, context)
            if previous is None or str(previous['consent_id']) != consent_id:
                raise ValueError('annotation_consent_conflict')
            conn.execute('''UPDATE server_annotation_consents
                SET revoked_at=COALESCE(revoked_at,CURRENT_TIMESTAMP)
                WHERE owner_user_id=%s AND context_id=%s AND consent_id=%s''', (owner, context, consent_id))
            return self._view(self._latest(conn, owner, context))
