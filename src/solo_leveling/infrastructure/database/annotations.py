"""메모·하이라이트 저장소(M1-11, #56). 스키마는 schema.py의 annotations·annotation_segments.

- 생성·수정·삭제는 모두 짧은 쓰기 트랜잭션 하나(BEGIN IMMEDIATE)로 처리한다. 중간에 실패하면 전부 되돌린다.
- 결과는 예외 대신 AnnotationResult의 outcome으로 돌려준다(저장됨/삭제됨/충돌/없음/잘못된 참조). 형식 오류만 AnnotationValidationError.
- 수정·삭제는 expected_revision과 현재 revision이 같을 때만 반영한다. 다르면 아무것도 바꾸지 않고 CONFLICT.
- 위치(맥락·청크·구간)는 바꾸지 않는다. 본문과 학습 단계만 수정한다.
- 원문 구간은 저장 시점에 청크 원문의 해당 구간과 인용문 스냅샷이 같은지 확인한다.
  번역 구간은 번역 리비전이 맥락의 것과 같은지만 확인하고 인용문 대조는 하지 않는다(번역 저장 구조가 #30에서 바뀔 수 있다).
- 단건 조회·수정·삭제·구간 원문 조회는 호출자의 context_id를 받아 DB 조건에서 소속을 확인한다. 다른 맥락의 기록은
  없는 것(NOT_FOUND / None / ResourceNotFoundError)과 똑같이 처리하고, 수정·삭제는 이 확인을 revision 판정보다 먼저 한다.
- 목록은 한 번의 읽기 트랜잭션(스냅샷)에서 목록과 구간을 함께 읽어, 동시에 삭제돼도 응답 항목이 온전하다.
- 공개 목록은 list_annotation_page(키셋 페이지네이션): (created_at, annotation_id) 순, 기본 20건·최대 50건, LIMIT+1로
  다음 페이지 유무를 알고, 커서는 발급한 맥락·필터에 묶인 불투명 문자열이다. 위치 값으로 이어 읽으므로 기준 기록이
  지워져도 이어진다. 입력 제한(인용문 합계·중복 구간)은 create_annotation이 저장 전에 검사한다.
- 본문·인용문은 로그에 남기지 않는다. 이유는 고정 코드로만 알린다.
"""
from contextlib import closing
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.annotation import (
    CHUNK_NOT_IN_CONTEXT, CONTEXT_NOT_FOUND, OFFSET_OUT_OF_RANGE, QUOTE_MISMATCH, TARGET_ORIGINAL,
    TRANSLATION_REVISION_MISMATCH, Annotation, AnnotationOutcome, AnnotationResult, AnnotationSegment,
    AnnotationPage, AnnotationValidationError, SegmentInput, decode_cursor, encode_cursor,
    validate_annotation_fields, validate_page_limit, validate_segments)
from solo_leveling.domain.models import now_iso
from solo_leveling.infrastructure.database.schema import get_connection


def _invalid(code: str) -> AnnotationResult:
    return AnnotationResult(AnnotationOutcome.INVALID_REFERENCE, code=code)


def _check_revision(expected_revision) -> None:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
        raise AnnotationValidationError('expected_revision은 1 이상의 정수여야 함')


def _load(conn, annotation_id: str, context_id: Optional[str] = None) -> Optional[Annotation]:
    """context_id를 주면 그 맥락의 기록만 읽는다(다른 맥락의 기록은 없는 것과 같다)."""
    query, params = 'SELECT * FROM annotations WHERE annotation_id=?', [annotation_id]
    if context_id is not None:
        query, params = query + ' AND context_id=?', params + [context_id]
    row = conn.execute(query, params).fetchone()
    if row is None:
        return None
    segments = tuple(
        AnnotationSegment(r['position'], r['chunk_id'], r['parse_revision_id'], r['target'], r['start_offset'],
                          r['end_offset'], r['quote_snapshot'], r['translation_revision_id'])
        for r in conn.execute('SELECT * FROM annotation_segments WHERE annotation_id=? ORDER BY position',
                              (annotation_id,)))
    return Annotation(row['annotation_id'], row['context_id'], row['kind'], row['body'], row['step'],
                      row['revision'], row['created_at'], row['updated_at'], segments)


def _check_segment(conn, context, segment: SegmentInput):
    """구간이 맥락에 맞는지 확인한다. 맞으면 (chunk 행, None), 아니면 (None, 이유 코드)."""
    chunk = conn.execute('SELECT chunk_id, parse_revision_id, original_text FROM chunks WHERE chunk_id=?',
                         (segment.chunk_id,)).fetchone()
    if chunk is None or chunk['parse_revision_id'] != context['parse_revision_id']:
        return None, CHUNK_NOT_IN_CONTEXT
    if segment.target == TARGET_ORIGINAL:
        text = chunk['original_text']
        if segment.end_offset > len(text):
            return None, OFFSET_OUT_OF_RANGE
        if text[segment.start_offset:segment.end_offset] != segment.quote_snapshot:
            return None, QUOTE_MISMATCH
    elif segment.translation_revision_id != context['translation_revision_id']:
        return None, TRANSLATION_REVISION_MISMATCH
    return chunk, None


def create_annotation(db_path: str, context_id: str, kind: str, body: str,
                      segments: Sequence[SegmentInput] = (), step: Optional[str] = None) -> AnnotationResult:
    """기록과 구간을 한 트랜잭션으로 저장한다. 맥락·청크·위치·인용문이 맞지 않으면 아무것도 저장하지 않는다."""
    segments = tuple(segments)
    validate_annotation_fields(kind, body, step, len(segments))
    if not all(isinstance(s, SegmentInput) for s in segments):
        raise AnnotationValidationError('구간은 SegmentInput이어야 함')
    validate_segments(segments)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        context = conn.execute('SELECT parse_revision_id, translation_revision_id FROM learning_contexts '
                               'WHERE context_id=?', (context_id,)).fetchone()
        if context is None:
            return _invalid(CONTEXT_NOT_FOUND)
        checked = []
        for segment in segments:
            chunk, code = _check_segment(conn, context, segment)
            if code:
                return _invalid(code)
            checked.append(chunk)
        annotation_id, timestamp = str(uuid4()), now_iso()
        conn.execute('INSERT INTO annotations (annotation_id, context_id, kind, body, step, revision, '
                     'created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)',
                     (annotation_id, context_id, kind, body, step, timestamp, timestamp))
        for position, (segment, chunk) in enumerate(zip(segments, checked)):
            conn.execute('INSERT INTO annotation_segments (annotation_id, position, chunk_id, parse_revision_id, '
                         'target, translation_revision_id, start_offset, end_offset, quote_snapshot) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                         (annotation_id, position, segment.chunk_id, chunk['parse_revision_id'], segment.target,
                          segment.translation_revision_id, segment.start_offset, segment.end_offset,
                          segment.quote_snapshot))
        return AnnotationResult(AnnotationOutcome.SAVED, _load(conn, annotation_id))


def update_annotation(db_path: str, context_id: str, annotation_id: str, expected_revision: int, body: str,
                      step: Optional[str] = None) -> AnnotationResult:
    """본문과 학습 단계를 고친다(둘 다 새 값으로 바꾼다. step=None이면 단계 연결을 지운다).

    다른 맥락의 기록이면 revision을 보기 전에 NOT_FOUND(아무것도 바꾸지 않음). revision이 expected_revision과
    다르면 아무것도 바꾸지 않고 CONFLICT. 맥락·구간은 바꿀 수 없다.
    """
    _check_revision(expected_revision)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = _load(conn, annotation_id, context_id)
        if current is None:
            return AnnotationResult(AnnotationOutcome.NOT_FOUND)
        if current.revision != expected_revision:
            return AnnotationResult(AnnotationOutcome.CONFLICT)
        validate_annotation_fields(current.kind, body, step, len(current.segments))
        changed = conn.execute(
            'UPDATE annotations SET body=?, step=?, revision=revision+1, updated_at=? '
            'WHERE annotation_id=? AND context_id=? AND revision=?',
            (body, step, now_iso(), annotation_id, context_id, expected_revision)).rowcount
        if changed != 1:  # BEGIN IMMEDIATE라 일어나지 않지만, 어긋나면 조용히 덮지 않는다.
            return AnnotationResult(AnnotationOutcome.CONFLICT)
        return AnnotationResult(AnnotationOutcome.SAVED, _load(conn, annotation_id, context_id))


def delete_annotation(db_path: str, context_id: str, annotation_id: str, expected_revision: int) -> AnnotationResult:
    """기록과 소속 구간만 한 트랜잭션으로 지운다. 논문·맥락·청크·근거는 그대로 둔다. 다른 맥락의 기록은 NOT_FOUND."""
    _check_revision(expected_revision)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = _load(conn, annotation_id, context_id)
        if current is None:
            return AnnotationResult(AnnotationOutcome.NOT_FOUND)
        if current.revision != expected_revision:
            return AnnotationResult(AnnotationOutcome.CONFLICT)
        conn.execute('DELETE FROM annotation_segments WHERE annotation_id=?', (annotation_id,))
        conn.execute('DELETE FROM annotations WHERE annotation_id=? AND context_id=? AND revision=?',
                     (annotation_id, context_id, expected_revision))
        return AnnotationResult(AnnotationOutcome.DELETED)


def get_annotation(db_path: str, context_id: str, annotation_id: str) -> Optional[Annotation]:
    """그 맥락의 기록 하나. 없거나 다른 맥락의 기록이면 None."""
    with closing(get_connection(db_path)) as conn:
        return _load(conn, annotation_id, context_id)


def list_annotations(db_path: str, context_id: str, step: Optional[str] = None,
                     kind: Optional[str] = None) -> List[Annotation]:
    """한 맥락의 기록을 만든 순서로 돌려준다. 다른 맥락의 기록은 섞이지 않는다. 맥락이 없으면 ResourceNotFoundError."""
    query, params = 'SELECT annotation_id FROM annotations WHERE context_id=?', [context_id]
    if step is not None:
        query, params = query + ' AND step=?', params + [step]
    if kind is not None:
        query, params = query + ' AND kind=?', params + [kind]
    with closing(get_connection(db_path)) as conn:
        conn.execute('BEGIN')  # 읽기 스냅샷: 목록과 구간을 같은 시점에서 읽는다.
        try:
            if not conn.execute('SELECT 1 FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone():
                raise ResourceNotFoundError('context not found')
            ids = [r['annotation_id'] for r in conn.execute(query + ' ORDER BY created_at, rowid', params)]
            loaded = [_load(conn, annotation_id, context_id) for annotation_id in ids]
        finally:
            conn.rollback()
        return [a for a in loaded if a is not None]  # 스냅샷 안이라 None은 생기지 않지만 방어한다.


def annotation_page_query(context_id: str, step: Optional[str], kind: Optional[str], size: int,
                          after: Optional[Tuple[str, str]] = None) -> Tuple[str, list]:
    """한 페이지의 SQL과 매개변수를 만든다. 커서가 있으면 인덱스 탐색이 커서 위치에서 시작한다(#56).

    `created_at >= 기준시각`이 인덱스(context_id, created_at, annotation_id)의 범위 조건이 되어 앞쪽 행을 훑지 않는다.
    같은 시각의 행은 뒤따르는 `(created_at > ? OR annotation_id > ?)`로 걸러 기준 기록 뒤의 것만 남긴다.
    OR 한 덩어리만 쓰면 범위로 쓰이지 못해 뒤쪽 페이지일수록 비용이 늘어난다.
    """
    query, params = 'SELECT annotation_id, created_at FROM annotations WHERE context_id=?', [context_id]
    if step is not None:
        query, params = query + ' AND step=?', params + [step]
    if kind is not None:
        query, params = query + ' AND kind=?', params + [kind]
    if after is not None:
        query, params = (query + ' AND created_at >= ? AND (created_at > ? OR annotation_id > ?)',
                         params + [after[0], after[0], after[1]])
    return query + ' ORDER BY created_at, annotation_id LIMIT ?', params + [size + 1]


def list_annotation_page(db_path: str, context_id: str, step: Optional[str] = None, kind: Optional[str] = None,
                         limit: Optional[int] = None, cursor: Optional[str] = None) -> AnnotationPage:
    """한 맥락의 기록을 (created_at, annotation_id) 순으로 한 페이지 돌려준다.

    limit은 기본 20·최대 50(범위 밖이면 INVALID_LIMIT). cursor는 이전 페이지의 next_cursor이며, 형식 오류이거나
    다른 맥락·필터에서 발급된 것이면 INVALID_CURSOR(AnnotationValidationError.code). 맥락이 없으면 ResourceNotFoundError.
    한 페이지의 기록과 구간은 같은 읽기 스냅샷에서 읽는다. 이미 지나간 위치에 새로 생긴 기록은 다음 새로고침에서 보인다.
    """
    size = validate_page_limit(limit)
    after = decode_cursor(cursor, context_id, step, kind) if cursor is not None else None
    query, params = annotation_page_query(context_id, step, kind, size, after)
    with closing(get_connection(db_path)) as conn:
        conn.execute('BEGIN')  # 읽기 스냅샷
        try:
            if not conn.execute('SELECT 1 FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone():
                raise ResourceNotFoundError('context not found')
            rows = conn.execute(query, params).fetchall()
            more = len(rows) > size
            rows = rows[:size]
            loaded = [_load(conn, r['annotation_id'], context_id) for r in rows]
        finally:
            conn.rollback()
    items = tuple(a for a in loaded if a is not None)
    next_cursor = None
    if more and items:
        last = items[-1]
        next_cursor = encode_cursor(context_id, step, kind, last.created_at, last.annotation_id)
    return AnnotationPage(items, next_cursor)


def get_segment_texts(db_path: str, context_id: str, annotation_id: str) -> Dict[int, Optional[str]]:
    """구간별 '지금' 청크 텍스트(구간 위치 → 문자열). 서비스가 저장된 위치·인용문과 비교해 위치 확인 불가를 판정한다.

    번역 구간은 청크의 현재 번역이 같은 번역 리비전일 때만 텍스트를 주고, 아니면 None이다.
    다른 맥락의 기록이면 없는 기록과 똑같이 ResourceNotFoundError를 내고 본문·구간을 돌려주지 않는다.
    """
    with closing(get_connection(db_path)) as conn:
        annotation = _load(conn, annotation_id, context_id)
        if annotation is None:
            raise ResourceNotFoundError('annotation not found')
        texts: Dict[int, Optional[str]] = {}
        for seg in annotation.segments:
            row = conn.execute('SELECT original_text, text, translation_revision_id FROM chunks WHERE chunk_id=?',
                               (seg.chunk_id,)).fetchone()
            if row is None:
                texts[seg.position] = None
            elif seg.target == TARGET_ORIGINAL:
                texts[seg.position] = row['original_text']
            else:
                texts[seg.position] = (row['text'] if row['translation_revision_id'] == seg.translation_revision_id
                                       else None)
        return texts
