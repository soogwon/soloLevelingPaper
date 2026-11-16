"""논문 단위 읽기 상태의 소유권·CAS·명시적 완료 취소 계약."""
from dataclasses import dataclass
from datetime import datetime


class ReadingStateConflict(Exception):
    """오래된 리비전 또는 허용되지 않은 상태 전이."""


@dataclass(frozen=True)
class PaperReadingState:
    paper_id: str
    status: str
    basis_version_id: str | None
    revision: int
    source_revision: int
    updated_at: datetime | None


def get_state(pool, owner, paper_id, source_id):
    from .papers import _connection, PaperNotFound
    with _connection(pool) as conn:
        if conn.execute('SELECT 1 FROM server_papers WHERE paper_id=%s AND owner_user_id=%s '
                        'AND source_instance_id=%s AND deleted_at IS NULL',
                        (paper_id, owner, source_id)).fetchone() is None:
            raise PaperNotFound
        row = conn.execute('SELECT * FROM server_paper_reading_states WHERE paper_id=%s', (paper_id,)).fetchone()
        return _view(paper_id, row)


def put_state(pool, owner, paper_id, request):
    from .papers import _connection, PaperNotFound
    import hashlib
    import json
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with _connection(pool) as conn:
        # 최초 생성도 논문 행으로 직렬화한다. 다른 계정/출처는 상태 존재를 노출하지 않는다.
        paper = conn.execute('SELECT paper_id FROM server_papers WHERE paper_id=%s AND owner_user_id=%s '
            'AND source_instance_id=%s AND deleted_at IS NULL FOR UPDATE',
            (paper_id, owner, request['source_instance_id'])).fetchone()
        if paper is None:
            raise PaperNotFound
        row = conn.execute('SELECT * FROM server_paper_reading_states WHERE paper_id=%s', (paper_id,)).fetchone()
        if row and str(row['operation_id']) == request['operation_id']:
            if row['request_hash'] != digest:
                raise ReadingStateConflict
            return _view(paper_id, row)
        revision = row['revision'] if row else 0
        if request['expected_revision'] != revision or (row and request['source_revision'] < row['source_revision']):
            raise ReadingStateConflict
        if row and request['source_revision'] == row['source_revision'] and any(
            request[key] != row[key] for key in ('status', 'basis_version_id', 'action')
        ):
            raise ReadingStateConflict
        status, action = request['status'], request['action']
        if row and row['status'] == 'completed' and action == 'reopen' and request['basis_version_id'] != row['basis_version_id']:
            raise ReadingStateConflict
        if row and row['status'] == 'completed' and status != 'completed' and action != 'reopen':
            raise ReadingStateConflict
        # 같은 상태라도 새로 명시한 기준 버전은 보존한다.
        row = conn.execute('''INSERT INTO server_paper_reading_states
            (paper_id, status, basis_version_id, revision, source_revision, action, operation_id, request_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(paper_id) DO UPDATE SET status=excluded.status,
              basis_version_id=excluded.basis_version_id, revision=excluded.revision,
              source_revision=excluded.source_revision, action=excluded.action,
              operation_id=excluded.operation_id, request_hash=excluded.request_hash, updated_at=CURRENT_TIMESTAMP
            RETURNING *''', (paper_id, status, request['basis_version_id'], revision+1,
                request['source_revision'], action, request['operation_id'], digest)).fetchone()
        return _view(paper_id, row)


def _view(paper_id, row):
    if row is None:
        return PaperReadingState(paper_id, 'unknown', None, 0, 0, None)
    return PaperReadingState(paper_id, row['status'], row['basis_version_id'], row['revision'],
                             row['source_revision'], row['updated_at'])
