"""논문 등록 작업을 변경 없이 조회하고 안정적인 다음 페이지를 제공한다."""

import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError


def list_paper_jobs(db_path: str, *, query: str | None = None,
                    limit: int = 20, cursor: str | None = None) -> dict:
    """작업 단위 목록이며 게시 결과 검증은 기존 상태 조회 도구가 담당한다."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidArgumentError('limit은 1~100의 정수여야 합니다.')
    if query is not None:
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise InvalidArgumentError('query는 1~200자의 검색어여야 합니다.')
        query = query.strip()
    query_hash = hashlib.sha256((query or '').encode('utf-8')).hexdigest()
    boundary = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError()
            decoded = base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True)
            value = json.loads(decoded)
            if (not isinstance(value, dict)
                    or set(value) != {'v', 'created_at', 'job_id', 'query_hash'}
                    or type(value['v']) is not int or value['v'] != 1
                    or value['query_hash'] != query_hash
                    or not isinstance(value['created_at'], str)
                    or not 1 <= len(value['created_at']) <= 64
                    or not isinstance(value['job_id'], str)
                    or not 1 <= len(value['job_id']) <= 200):
                raise ValueError()
            boundary = (value['created_at'], value['job_id'])
        except (ValueError, TypeError, UnicodeError):
            raise InvalidArgumentError('cursor가 올바르지 않거나 검색어가 변경되었습니다.') from None

    predicates, parameters = [], []
    if query is not None:
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        predicates.append("(p.title LIKE ? ESCAPE '\\' OR v.original_filename LIKE ? ESCAPE '\\')")
        parameters.extend([f'%{escaped}%', f'%{escaped}%'])
    if boundary is not None:
        predicates.append('(j.created_at < ? OR (j.created_at = ? AND j.job_id < ?))')
        parameters.extend([boundary[0], boundary[0], boundary[1]])
    where = ' WHERE ' + ' AND '.join(predicates) if predicates else ''
    sql = '''SELECT p.paper_id, p.title, v.version_id, v.original_filename,
                    j.job_id, j.status, j.stage, j.created_at, j.updated_at
             FROM processing_jobs j
             JOIN paper_versions v ON v.version_id = j.version_id
             JOIN papers p ON p.paper_id = v.paper_id''' + where + '''
             ORDER BY j.created_at DESC, j.job_id DESC LIMIT ?'''
    parameters.append(limit + 1)
    # 읽기 전용 연결이므로 잘못된 DB 경로에 빈 파일을 생성하지 않는다.
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, parameters).fetchall()
    selected = rows[:limit]
    items = []
    for row in selected:
        item = dict(row)
        item['file_display_name'] = PureWindowsPath(PurePosixPath(
            item.pop('original_filename')).name).name
        items.append(item)
    next_cursor = None
    if len(rows) > limit:
        last = selected[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps({
            'v': 1, 'created_at': last['created_at'], 'job_id': last['job_id'],
            'query_hash': query_hash,
        }, separators=(',', ':')).encode('utf-8')).decode('ascii')
    return {'jobs': items, 'next_cursor': next_cursor}
