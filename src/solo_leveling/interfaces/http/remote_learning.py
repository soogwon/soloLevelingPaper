"""명시적 원격 학습 시작·이어하기와 고정된 게시 근거의 연결."""

from uuid import UUID, uuid4


class RemoteLearningConflict(ValueError):
    """오래된 revision으로 학습 상태를 덮어쓸 수 없다."""


class PostgresRemoteLearningStore:
    def __init__(self, pool):
        self._pool = pool

    @staticmethod
    def _scope(owner, context):
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError('remote_learning_scope_invalid')
        return str(UUID(context))

    @staticmethod
    def _lock(conn, owner, context):
        # 삭제·메모와 같은 부모 잠금을 사용해 뒤늦은 학습 생성을 차단한다.
        return conn.execute('''SELECT c.* FROM server_execution_contexts c
            JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
            FOR UPDATE OF p''', (owner, context)).fetchone()

    @staticmethod
    def _read(conn, owner, context):
        row = conn.execute('''SELECT r.* FROM server_remote_learning_records r
            JOIN server_papers p ON p.owner_user_id=r.owner_user_id AND p.paper_id=r.paper_id
            WHERE r.owner_user_id=%s AND r.context_id=%s AND p.deleted_at IS NULL''',
            (owner, context)).fetchone()
        if row is None:
            return None
        return {**{key: str(row[key]) for key in (
            'learning_record_id', 'context_id', 'paper_id', 'server_version_id',
            'publication_id', 'parse_revision_id')},
            'status': row['status'], 'revision': row['revision'], 'origin': 'remote',
            'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def get(self, owner, context):
        """조회는 학습 시작이나 기록 생성으로 해석하지 않는다."""
        context = self._scope(owner, context)
        with self._pool.connection() as conn:
            return self._read(conn, owner, context)

    def start(self, owner, context):
        """같은 context의 명시적 시작 재전송은 기존 기록·완료 상태를 보존한다."""
        context = self._scope(owner, context)
        with self._pool.connection() as conn:
            binding = self._lock(conn, owner, context)
            if binding is None:
                return None
            conn.execute('''INSERT INTO server_remote_learning_records
                (learning_record_id,owner_user_id,context_id,paper_id,server_version_id,
                 publication_id,parse_revision_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(owner_user_id,context_id) DO NOTHING''',
                (str(uuid4()), owner, context, binding['paper_id'], binding['server_version_id'],
                 binding['publication_id'], binding['parse_revision_id']))
            return self._read(conn, owner, context)

    def set_status(self, owner, context, *, status, expected_revision):
        """완료·재열기는 기록이나 메모를 지우지 않으며 현재 revision을 요구한다."""
        context = self._scope(owner, context)
        if status not in ('reading', 'completed') or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('remote_learning_status_invalid')
        with self._pool.connection() as conn:
            if self._lock(conn, owner, context) is None:
                return None
            current = self._read(conn, owner, context)
            if current is None:
                return None
            if current['revision'] != expected_revision:
                raise RemoteLearningConflict('remote_learning_revision_conflict')
            if current['status'] != status:
                conn.execute('''UPDATE server_remote_learning_records
                    SET status=%s,revision=revision+1,updated_at=CURRENT_TIMESTAMP
                    WHERE owner_user_id=%s AND context_id=%s AND revision=%s''',
                    (status, owner, context, expected_revision))
            return self._read(conn, owner, context)
