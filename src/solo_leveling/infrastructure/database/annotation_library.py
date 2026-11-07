"""로컬 DB 전체의 메모·하이라이트를 조회하는 읽기 전용 저장소 계약(#206).

학습 맥락별 CRUD와 달리 모든 맥락을 한 목록으로 묶는다. 이 앱의 M1 저장소는 사용자별 서버 계정이
아니라 사용자 기기의 단일 로컬 DB이므로 DB 파일 자체가 프로필 경계다.
"""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Optional, Tuple

from solo_leveling.domain.annotation import (
    KINDS, Annotation, AnnotationLibraryItem, AnnotationLibraryPage, AnnotationSegment,
    AnnotationValidationError, decode_library_cursor, encode_library_cursor, validate_page_limit,
)
from solo_leveling.infrastructure.database.schema import get_connection

MAX_SEARCH_LENGTH = 200


def _validate_date(value, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AnnotationValidationError(f'{name} 형식이 올바르지 않음')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise AnnotationValidationError(f'{name} 형식이 올바르지 않음') from None
    if parsed.tzinfo is None:
        raise AnnotationValidationError(f'{name}에는 시간대가 필요함')
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_filters(kind, query, created_from, created_before):
    if kind is not None and kind not in KINDS:
        raise AnnotationValidationError('지원하지 않는 기록 종류')
    if query is not None:
        if not isinstance(query, str):
            raise AnnotationValidationError('검색어는 문자열이어야 함')
        query = query.strip()
        if not query:
            query = None
        elif len(query) > MAX_SEARCH_LENGTH:
            raise AnnotationValidationError('검색어가 너무 김')
    created_from = _validate_date(created_from, 'created_from')
    created_before = _validate_date(created_before, 'created_before')
    if created_from is not None and created_before is not None and created_from >= created_before:
        raise AnnotationValidationError('조회 기간이 올바르지 않음')
    return kind, query, created_from, created_before


def _scope(kind, query, created_from, created_before):
    return ('annotation-library-v1', kind, query, created_from, created_before)


def annotation_library_query(kind: Optional[str], query: Optional[str], created_from: Optional[str],
                             created_before: Optional[str], size: int,
                             before: Optional[Tuple[str, str]] = None):
    """최신순 키셋 조회 SQL. 본문·인용문·논문 제목 검색은 EXISTS로 중복을 막는다."""
    sql = """
        SELECT a.*, l.version_id, v.paper_id, v.original_filename, p.title AS paper_title
        FROM annotations a
        JOIN learning_contexts l ON l.context_id=a.context_id
        JOIN paper_versions v ON v.version_id=l.version_id
        JOIN papers p ON p.paper_id=v.paper_id
        WHERE 1=1
    """
    params = []
    if kind is not None:
        sql += ' AND a.kind=?'
        params.append(kind)
    if created_from is not None:
        sql += ' AND a.created_at>=?'
        params.append(created_from)
    if created_before is not None:
        sql += ' AND a.created_at<?'
        params.append(created_before)
    if query is not None:
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f'%{escaped}%'
        sql += """ AND (a.body LIKE ? ESCAPE '\\' COLLATE NOCASE
            OR p.title LIKE ? ESCAPE '\\' COLLATE NOCASE
            OR EXISTS (SELECT 1 FROM annotation_segments sx
                       WHERE sx.annotation_id=a.annotation_id
                         AND sx.quote_snapshot LIKE ? ESCAPE '\\' COLLATE NOCASE))"""
        params.extend([pattern] * 3)
    if before is not None:
        sql += ' AND (a.created_at, a.annotation_id) < (?, ?)'
        params.extend(before)
    sql += ' ORDER BY a.created_at DESC, a.annotation_id DESC LIMIT ?'
    params.append(size + 1)
    return sql, params


def _segments_for(conn, ids):
    if not ids:
        return {}
    marks = ','.join('?' for _ in ids)
    rows = conn.execute(f"""
        SELECT s.*, c.pdf_page, c.original_text, tr.translated_text, tr.failure_code
        FROM annotation_segments s
        LEFT JOIN chunks c ON c.chunk_id=s.chunk_id AND c.parse_revision_id=s.parse_revision_id
        LEFT JOIN translation_results tr
          ON tr.translation_revision_id=s.translation_revision_id AND tr.chunk_id=s.chunk_id
        WHERE s.annotation_id IN ({marks}) ORDER BY s.annotation_id, s.position
    """, ids).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['annotation_id']].append(row)
    return grouped


def list_annotation_library(db_path: str, *, kind: Optional[str] = None, query: Optional[str] = None,
                            created_from: Optional[str] = None, created_before: Optional[str] = None,
                            limit: Optional[int] = None, cursor: Optional[str] = None) -> AnnotationLibraryPage:
    """전체 기록을 최신순으로 조회한다.

    created_from은 포함, created_before는 제외한다. 검색은 본문·저장 인용문·논문 제목에 적용한다.
    저장 인용문은 변경하지 않고 돌려주며 segment_available은 현재 원문/번역과 여전히 일치하는지를 별도로 나타낸다.
    """
    size = validate_page_limit(limit)
    kind, query, created_from, created_before = _normalize_filters(kind, query, created_from, created_before)
    scope = _scope(kind, query, created_from, created_before)
    before = decode_library_cursor(cursor, scope) if cursor is not None else None
    sql, params = annotation_library_query(kind, query, created_from, created_before, size, before)
    with closing(get_connection(db_path)) as conn:
        conn.execute('BEGIN')
        try:
            rows = conn.execute(sql, params).fetchall()
            more = len(rows) > size
            rows = rows[:size]
            grouped = _segments_for(conn, [row['annotation_id'] for row in rows])
            items = []
            for row in rows:
                stored, available, pages = [], [], []
                for segment in grouped.get(row['annotation_id'], ()):
                    stored.append(AnnotationSegment(
                        segment['position'], segment['chunk_id'], segment['parse_revision_id'], segment['target'],
                        segment['start_offset'], segment['end_offset'], segment['quote_snapshot'],
                        segment['translation_revision_id']))
                    text = (segment['original_text'] if segment['target'] == 'original'
                            else segment['translated_text'] if segment['failure_code'] is None else None)
                    start, end = segment['start_offset'], segment['end_offset']
                    available.append(text is not None and 0 <= start < end <= len(text)
                                     and text[start:end] == segment['quote_snapshot'])
                    if segment['pdf_page'] is not None and segment['pdf_page'] not in pages:
                        pages.append(segment['pdf_page'])
                annotation = Annotation(row['annotation_id'], row['context_id'], row['kind'], row['body'], row['step'],
                                        row['revision'], row['created_at'], row['updated_at'], tuple(stored))
                items.append(AnnotationLibraryItem(
                    annotation, row['paper_id'], row['paper_title'], row['version_id'],
                    PureWindowsPath(PurePosixPath(row['original_filename']).name).name,
                    tuple(pages), tuple(available)))
        finally:
            conn.rollback()
    next_cursor = None
    if more and items:
        last = items[-1].annotation
        next_cursor = encode_library_cursor(scope, last.created_at, last.annotation_id)
    return AnnotationLibraryPage(tuple(items), next_cursor)
