"""명시적 원격 학습 시작·이어하기와 고정된 게시 근거의 연결."""

from uuid import UUID, uuid4
import hashlib
import json


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
            'last_pdf_page': row.get('last_pdf_page'),
            'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def get(self, owner, context):
        """조회는 학습 시작이나 기록 생성으로 해석하지 않는다."""
        context = self._scope(owner, context)
        with self._pool.connection() as conn:
            return self._read(conn, owner, context)

    def list_for_paper(self, owner, paper, version, *, after=None, limit=20):
        """현재 판본의 학습 맥락을 최근 진행 저장순으로 읽는다.

        커서는 해당 범위에 속한 context ID다. 커서 행의 ``updated_at``을 다시
        읽어 복합 정렬을 이어가므로 같은 시각에도 누락·중복이 없다.
        """
        paper = self._scope(owner, paper)
        if after is not None:
            after = self._scope(owner, after)
        if not isinstance(version, str) or not version.strip() or len(version) > 128:
            raise ValueError('remote_learning_version_invalid')
        after = str(UUID(after)) if after is not None else None
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('remote_learning_limit_invalid')
        with self._pool.connection() as conn:
            cursor = None
            if after is not None:
                cursor = conn.execute('''SELECT r.context_id,r.updated_at
                    FROM server_remote_learning_records r
                    JOIN server_papers p ON p.paper_id=r.paper_id AND p.owner_user_id=r.owner_user_id
                    WHERE r.owner_user_id=%s AND r.paper_id=%s AND r.server_version_id=%s
                      AND r.context_id=%s AND p.deleted_at IS NULL''',
                    (owner, paper, version, after)).fetchone()
                if cursor is None:
                    return {'items': [], 'next_cursor': None}
            continuation = ''
            params = [owner, paper, version]
            if cursor is not None:
                continuation = ''' AND (r.updated_at<%s OR
                    (r.updated_at=%s AND r.context_id>%s))'''
                params.extend([cursor['updated_at'], cursor['updated_at'], after])
            params.append(limit + 1)
            rows = conn.execute('''SELECT r.context_id, r.learning_record_id,
                r.paper_id, r.server_version_id, r.publication_id, r.status, r.revision,
                r.last_pdf_page, r.updated_at
                FROM server_remote_learning_records r
                JOIN server_papers p ON p.paper_id=r.paper_id AND p.owner_user_id=r.owner_user_id
                WHERE r.owner_user_id=%s AND r.paper_id=%s AND r.server_version_id=%s
                  AND p.deleted_at IS NULL''' + continuation +
                ''' ORDER BY r.updated_at DESC,r.context_id LIMIT %s''', params).fetchall()
        items = [{**{key: str(row[key]) for key in ('context_id', 'learning_record_id',
            'paper_id', 'server_version_id', 'publication_id')},
            'status': row['status'], 'revision': row['revision'],
            'last_position': ({'pdf_page': row['last_pdf_page']}
                              if row['last_pdf_page'] is not None else None),
            'updated_at': row['updated_at']} for row in rows[:limit]]
        return {'items': items, 'next_cursor': items[-1]['context_id'] if len(rows) > limit else None}

    def dashboard(self, owner, *, after=None, limit=20, context=None):
        """고정된 원격 맥락만 조회한다. 처리 완료를 학습 완료로 변환하지 않는다."""
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError('remote_learning_scope_invalid')
        after = str(UUID(after)) if after is not None else None
        context = str(UUID(context)) if context is not None else None
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('remote_learning_limit_invalid')
        with self._pool.connection() as conn:
            rows = conn.execute("""SELECT c.context_id,c.paper_id,c.server_version_id,c.publication_id,
                p.title,r.status,r.revision,r.updated_at,r.last_pdf_page,
                (SELECT count(*) FROM server_context_annotations a
                  WHERE a.owner_user_id=c.owner_user_id AND a.context_id=c.context_id
                    AND a.deleted_at IS NULL AND a.kind='note') AS note_count,
                (SELECT count(*) FROM server_context_annotations a
                  WHERE a.owner_user_id=c.owner_user_id AND a.context_id=c.context_id
                    AND a.deleted_at IS NULL AND a.kind='highlight') AS highlight_count
                FROM server_execution_contexts c
                JOIN server_papers p ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                LEFT JOIN server_remote_learning_records r
                  ON r.owner_user_id=c.owner_user_id AND r.context_id=c.context_id
                WHERE c.owner_user_id=%s AND p.deleted_at IS NULL
                  AND (%s::uuid IS NULL OR c.context_id>%s::uuid)
                  AND (%s::uuid IS NULL OR c.context_id=%s::uuid)
                ORDER BY c.context_id LIMIT %s""",
                (owner, after, after, context, context, limit + 1)).fetchall()
        items = [{**{key: str(row[key]) for key in (
            'context_id', 'paper_id', 'server_version_id', 'publication_id')},
            'title': row['title'], 'processing_status': 'published',
            'reading_status': row['status'] or 'not_started', 'revision': row['revision'],
            'last_learning_updated_at': row['updated_at'],
            'last_position': {'pdf_page': row['last_pdf_page']} if row['last_pdf_page'] is not None else None,
            'note_count': row['note_count'], 'highlight_count': row['highlight_count'],
            'quiz_summary': None, 'reflection_summary': None,
            'web_path': '/#/remote-learning/' + str(row['context_id']) if row['status'] else None}
            for row in rows[:limit]]
        return {'items': items, 'next_cursor': items[-1]['context_id'] if len(rows) > limit else None,
                'limitations': ['remote_quiz_not_available',
                                'remote_reflection_not_available'],
                'notice': '게시 완료와 학습 완료는 별개입니다. 학습 변경 시각은 마지막 열람 시각이 아닙니다. 위치는 고정 PDF 판본 기준이며 퀴즈·성찰은 아직 지원하지 않습니다.'}

    def reading_state(self, owner, context):
        page = self.dashboard(owner, context=self._scope(owner, context))
        return page['items'][0] if page['items'] else None

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


    def set_progress(self, owner, context, *, request_key, status, pdf_page, expected_revision):
        """동일 키 재전송은 재적용하지 않고 현재 상태와 최초 적용 revision을 함께 반환한다."""
        context = self._scope(owner, context)
        request_key = str(UUID(request_key))
        if (status not in ('reading', 'completed')
                or type(expected_revision) is not int or expected_revision < 1
                or (pdf_page is not None and (type(pdf_page) is not int or pdf_page < 1))):
            raise ValueError('remote_learning_progress_invalid')
        digest = hashlib.sha256(json.dumps([status, pdf_page, expected_revision],
                                           separators=(',', ':')).encode()).hexdigest()
        with self._pool.connection() as conn:
            if self._lock(conn, owner, context) is None:
                return None
            current = self._read(conn, owner, context)
            if current is None:
                return None
            old = conn.execute("""SELECT payload_hash,applied_revision FROM server_remote_learning_requests
                WHERE owner_user_id=%s AND context_id=%s AND request_key=%s""",
                (owner, context, request_key)).fetchone()
            if old:
                if old['payload_hash'] != digest:
                    raise RemoteLearningConflict('remote_learning_request_conflict')
                return {'state': current, 'applied_revision': old['applied_revision'], 'replayed': True}
            if current['revision'] != expected_revision:
                raise RemoteLearningConflict('remote_learning_revision_conflict')
            if pdf_page is not None:
                row = conn.execute("""SELECT document_metadata FROM server_parse_revisions
                    WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s
                      AND parse_revision_id=%s""", (owner, current['paper_id'],
                        current['server_version_id'], current['parse_revision_id'])).fetchone()
                count = row['document_metadata'].get('page_count') if row else None
                if type(count) is not int or not 1 <= pdf_page <= count:
                    raise ValueError('remote_learning_page_invalid')
            if current['status'] != status or current['last_pdf_page'] != pdf_page:
                conn.execute("""UPDATE server_remote_learning_records
                    SET status=%s,last_pdf_page=%s,revision=revision+1,updated_at=CURRENT_TIMESTAMP
                    WHERE owner_user_id=%s AND context_id=%s AND revision=%s""",
                    (status, pdf_page, owner, context, expected_revision))
            state = self._read(conn, owner, context)
            conn.execute("""INSERT INTO server_remote_learning_requests
                (owner_user_id,context_id,request_key,payload_hash,applied_revision)
                VALUES (%s,%s,%s,%s,%s)""", (owner, context, request_key, digest, state['revision']))
            return {'state': state, 'applied_revision': state['revision'], 'replayed': False}
