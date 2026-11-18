"""인증된 원격 context의 메모 생성 원장과 원자적 PostgreSQL 저장."""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Sequence
from uuid import uuid4

from solo_leveling.domain.annotation import (
    AnnotationValidationError,
    SegmentInput,
    validate_annotation_fields,
    validate_segments,
)


FINGERPRINT_VERSION = 1
_SERVICE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")


class RemoteAnnotationConflict(ValueError):
    """같은 범위의 request_key가 다른 생성 입력에 사용됐다."""


class RemoteAnnotationRecoveryAmbiguous(ValueError):
    """같은 입력의 미확정 요청이 여러 개여서 자동 선택할 수 없다."""


class RemoteAnnotationDeleted(ValueError):
    """해당 생성 요청으로 만든 기록이 이미 삭제됐다."""


class RemoteAnnotationReferenceError(ValueError):
    """context 또는 선택 구간이 현재 인증 범위와 일치하지 않는다."""


@dataclass(frozen=True)
class RemoteAnnotationInput:
    kind: str
    body: str
    segments: tuple[SegmentInput, ...] = ()
    step: str | None = None
    creation_origin: str = "unknown"

    def __post_init__(self):
        if self.creation_origin not in ("user", "ai", "unknown"):
            raise AnnotationValidationError("지원하지 않는 생성 주체")
        if not isinstance(self.segments, tuple):
            object.__setattr__(self, "segments", tuple(self.segments))
        validate_annotation_fields(self.kind, self.body, self.step, len(self.segments))
        validate_segments(self.segments)


@dataclass(frozen=True)
class StoredRemoteAnnotation:
    annotation_id: str
    context_id: str
    kind: str
    body: str
    step: str | None
    segments: tuple[dict, ...]
    creation_origin: str
    revision: int
    created_at: object
    updated_at: object


@dataclass(frozen=True)
class RemoteAnnotationCreateResult:
    request_key: str
    annotation: StoredRemoteAnnotation
    reused: bool


@dataclass(frozen=True)
class RemoteAnnotationRecoveryResult:
    request_key: str
    annotation: StoredRemoteAnnotation
    state: str
    recovery: str


def annotation_input_fingerprint(value: RemoteAnnotationInput) -> str:
    """v1: 값은 그대로 두고 객체 키만 정렬한다. 배열 순서와 Unicode/공백은 보존한다."""
    payload = {
        "body": value.body,
        "creation_origin": value.creation_origin,
        "kind": value.kind,
        "segments": [
            {
                "chunk_id": item.chunk_id,
                "end_offset": item.end_offset,
                "quote_snapshot": item.quote_snapshot,
                "start_offset": item.start_offset,
                "target": item.target,
                "translation_revision_id": item.translation_revision_id,
            }
            for item in value.segments
        ],
        "step": value.step,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PostgresRemoteAnnotationStore:
    def __init__(self, pool):
        self._pool = pool

    def create(self, owner_user_id: str, service_id: str, context_id: str, request_key: str,
               value: RemoteAnnotationInput, *, require_learning: bool = False, require_storage_consent: bool = False) -> RemoteAnnotationCreateResult:
        if not all(isinstance(item, str) and item for item in (owner_user_id, context_id)):
            raise ValueError("remote_annotation_scope_invalid")
        if (not isinstance(service_id, str) or not isinstance(request_key, str)
                or not _SERVICE_ID.fullmatch(service_id) or not _REQUEST_KEY.fullmatch(request_key)):
            raise ValueError("remote_annotation_scope_invalid")
        if not isinstance(value, RemoteAnnotationInput):
            raise ValueError("remote_annotation_input_invalid")

        digest = annotation_input_fingerprint(value)
        annotation_id = str(uuid4())
        with self._pool.connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{owner_user_id}\x1f{service_id}\x1f{context_id}\x1f{request_key}",),
            )
            # 물리 삭제·메모 삭제와 같은 순서로 부모를 먼저 잠근다.
            # 원장→메모와 메모→원장의 교차 잠금을 부모 행 아래 직렬화한다.
            parent = conn.execute(
                """SELECT p.paper_id FROM server_papers p
                   JOIN server_execution_contexts c
                     ON c.owner_user_id=p.owner_user_id AND c.paper_id=p.paper_id
                   WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
                   FOR UPDATE OF p""",
                (owner_user_id, context_id),
            ).fetchone()
            if parent is None:
                raise RemoteAnnotationReferenceError("annotation_create_not_found")
            old = conn.execute(
                """SELECT r.*,a.* FROM server_annotation_create_requests r
                   JOIN server_context_annotations a
                     ON a.owner_user_id=r.owner_user_id AND a.annotation_id=r.annotation_id
                   JOIN server_execution_contexts c
                     ON c.owner_user_id=r.owner_user_id AND c.context_id=r.context_id
                   JOIN server_papers p
                     ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                   WHERE r.owner_user_id=%s AND r.service_id=%s AND r.context_id=%s
                     AND r.request_key=%s AND p.deleted_at IS NULL FOR UPDATE OF r,a,p""",
                (owner_user_id, service_id, context_id, request_key),
            ).fetchone()
            if old is not None:
                if old["state"] == "deleted" or old["deleted_at"] is not None:
                    raise RemoteAnnotationDeleted("annotation_deleted")
                if old["fingerprint_version"] != FINGERPRINT_VERSION or old["input_sha256"] != digest:
                    raise RemoteAnnotationConflict("annotation_request_conflict")
                return RemoteAnnotationCreateResult(request_key, _stored(old), True)

            scope = conn.execute(
                """SELECT c.parse_revision_id,p.translation_revision_id
                   FROM server_execution_contexts c
                   JOIN server_processing_publications p
                     ON p.owner_user_id=c.owner_user_id AND p.publication_id=c.publication_id
                   JOIN server_papers paper
                     ON paper.owner_user_id=c.owner_user_id AND paper.paper_id=c.paper_id
                   WHERE c.owner_user_id=%s AND c.context_id=%s AND paper.deleted_at IS NULL
                   FOR SHARE OF c,p,paper""",
                (owner_user_id, context_id),
            ).fetchone()
            if scope is None:
                raise RemoteAnnotationReferenceError("annotation_create_not_found")
            if require_learning and conn.execute(
                "SELECT 1 FROM server_remote_learning_records WHERE owner_user_id=%s AND context_id=%s",
                (owner_user_id, context_id),
            ).fetchone() is None:
                raise RemoteAnnotationReferenceError("annotation_learning_required")
            if require_storage_consent:
                from .remote_annotation_consents import require_annotation_consent
                require_annotation_consent(conn, owner_user_id, context_id)
            segments = self._validated_segments(conn, owner_user_id, scope, value.segments)
            row = conn.execute(
                """INSERT INTO server_context_annotations
                       (annotation_id,owner_user_id,context_id,kind,body,step,segments,creation_origin)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING *""",
                (annotation_id, owner_user_id, context_id, value.kind, value.body, value.step,
                 json.dumps(segments, ensure_ascii=False, separators=(",", ":")), value.creation_origin),
            ).fetchone()
            conn.execute(
                """INSERT INTO server_annotation_create_requests
                       (owner_user_id,service_id,context_id,request_key,fingerprint_version,
                        input_sha256,annotation_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (owner_user_id, service_id, context_id, request_key, FINGERPRINT_VERSION,
                 digest, annotation_id),
            )
        return RemoteAnnotationCreateResult(request_key, _stored(row), False)

    @staticmethod
    def _recovery_scope(owner, service, context, request_key=None):
        if (not all(isinstance(item, str) and item for item in (owner, context))
                or not isinstance(service, str) or not _SERVICE_ID.fullmatch(service)
                or request_key is not None and (not isinstance(request_key, str)
                    or not _REQUEST_KEY.fullmatch(request_key))):
            raise ValueError("remote_annotation_scope_invalid")

    @staticmethod
    def _lock_recovery_parent(conn, owner, context):
        # 생성·삭제와 같은 부모 우선 순서로 현재 상태와 수신 확인을 직렬화한다.
        return conn.execute(
            """SELECT p.paper_id FROM server_papers p
               JOIN server_execution_contexts c
                 ON c.owner_user_id=p.owner_user_id AND c.paper_id=p.paper_id
               WHERE c.owner_user_id=%s AND c.context_id=%s AND p.deleted_at IS NULL
               FOR UPDATE OF p""", (owner, context),
        ).fetchone() is not None

    @staticmethod
    def _recovery_rows(conn, owner, service, context, *, request_key=None, digest=None):
        if request_key is not None:
            condition = " AND r.request_key=%s LIMIT 1"
            params = (request_key,)
        else:
            condition = " AND r.fingerprint_version=%s AND r.input_sha256=%s AND r.client_confirmed_at IS NULL LIMIT 2"
            params = (FINGERPRINT_VERSION, digest)
        return conn.execute(
            """SELECT r.request_key,r.state,r.input_sha256,r.fingerprint_version,
                      r.client_confirmed_at,a.*
               FROM server_annotation_create_requests r
               JOIN server_context_annotations a
                 ON a.owner_user_id=r.owner_user_id AND a.annotation_id=r.annotation_id
                   AND a.context_id=r.context_id
               WHERE r.owner_user_id=%s AND r.service_id=%s AND r.context_id=%s""" + condition,
            (owner, service, context, *params),
        ).fetchall()

    def lookup_request(self, owner, service, context, request_key):
        """인증 adapter가 확정한 범위의 정확한 키만 조회하며 새 메모를 만들지 않는다."""
        self._recovery_scope(owner, service, context, request_key)
        if request_key is None:
            raise ValueError('remote_annotation_scope_invalid')
        with self._pool.connection() as conn:
            if not self._lock_recovery_parent(conn, owner, context):
                return None
            rows = self._recovery_rows(conn, owner, service, context, request_key=request_key)
            row = rows[0] if rows else None
            return self._recovery_result(row, 'exact') if row else None

    def recover_request(self, owner, service, context, value):
        """전체 최초 입력 지문이 같은 미확정 요청 한 개만 반환한다. 부분 본문 검색은 금지한다."""
        self._recovery_scope(owner, service, context)
        if not isinstance(value, RemoteAnnotationInput):
            raise ValueError('remote_annotation_input_invalid')
        digest = annotation_input_fingerprint(value)
        with self._pool.connection() as conn:
            if not self._lock_recovery_parent(conn, owner, context):
                return None
            rows = self._recovery_rows(conn, owner, service, context, digest=digest)
            if len(rows) > 1:
                raise RemoteAnnotationRecoveryAmbiguous('annotation_recovery_ambiguous')
            return self._recovery_result(rows[0], 'unique_unconfirmed') if rows else None

    @staticmethod
    def _recovery_result(row, recovery):
        state = 'deleted' if row['state'] == 'deleted' or row['deleted_at'] is not None else 'committed'
        annotation = _stored(row)
        if state == 'deleted':
            annotation = replace(annotation, body='', segments=())
        return RemoteAnnotationRecoveryResult(row['request_key'], annotation, state, recovery)

    def confirm_request(self, owner, service, context, request_key, annotation_id, revision):
        """검증한 응답의 ID·현재 revision이 일치할 때만 멱등 확인한다."""
        self._recovery_scope(owner, service, context, request_key)
        if (request_key is None or not isinstance(annotation_id, str) or not annotation_id
                or type(revision) is not int or revision < 1):
            raise ValueError('remote_annotation_confirmation_invalid')
        with self._pool.connection() as conn:
            if not self._lock_recovery_parent(conn, owner, context):
                return False
            row = conn.execute(
                """UPDATE server_annotation_create_requests r
                   SET client_confirmed_at=COALESCE(r.client_confirmed_at,CURRENT_TIMESTAMP)
                   FROM server_context_annotations a
                   WHERE r.owner_user_id=%s AND r.service_id=%s AND r.context_id=%s
                     AND r.request_key=%s AND r.annotation_id=%s
                     AND a.owner_user_id=r.owner_user_id AND a.annotation_id=r.annotation_id
                     AND a.context_id=r.context_id AND a.revision=%s
                   RETURNING r.request_key""",
                (owner, service, context, request_key, annotation_id, revision),
            ).fetchone()
            return row is not None

    @staticmethod
    def _validated_segments(conn, owner_user_id: str, scope, segments: Sequence[SegmentInput]) -> list[dict]:
        stored = []
        for position, segment in enumerate(segments):
            if segment.target == "original":
                row = conn.execute(
                    """SELECT original_text AS source_text FROM server_processing_chunks
                       WHERE owner_user_id=%s AND parse_revision_id=%s AND chunk_id=%s""",
                    (owner_user_id, scope["parse_revision_id"], segment.chunk_id),
                ).fetchone()
            elif str(scope["translation_revision_id"]) == segment.translation_revision_id:
                row = conn.execute(
                    """SELECT translated_text AS source_text FROM server_translation_chunks
                       WHERE owner_user_id=%s AND parse_revision_id=%s
                         AND translation_revision_id=%s AND chunk_id=%s""",
                    (owner_user_id, scope["parse_revision_id"], scope["translation_revision_id"],
                     segment.chunk_id),
                ).fetchone()
            else:
                row = None
            if row is None:
                raise RemoteAnnotationReferenceError("annotation_segment_not_found")
            source = row["source_text"]
            if segment.end_offset > len(source) or source[segment.start_offset:segment.end_offset] != segment.quote_snapshot:
                raise RemoteAnnotationReferenceError("annotation_quote_mismatch")
            stored.append({
                "position": position,
                "chunk_id": segment.chunk_id,
                "parse_revision_id": str(scope["parse_revision_id"]),
                "target": segment.target,
                "translation_revision_id": segment.translation_revision_id,
                "start_offset": segment.start_offset,
                "end_offset": segment.end_offset,
                "quote_snapshot": segment.quote_snapshot,
            })
        return stored

    def get_annotation(self, owner, context, annotation_id):
        """같은 owner/context의 살아 있는 기록만 조회한다."""
        self._recovery_scope(owner, 'lookup', context)
        with self._pool.connection() as conn:
            if not self._lock_recovery_parent(conn, owner, context):
                return None
            row = conn.execute("""SELECT * FROM server_context_annotations
                WHERE owner_user_id=%s AND context_id=%s AND annotation_id=%s
                  AND deleted_at IS NULL""", (owner, context, annotation_id)).fetchone()
            return None if row is None else _stored(row)

    def list_annotations(self, owner, context, *, after=None, limit=20):
        """불변 ID를 커서로 사용하며 삭제된 기록은 목록에서 제외한다."""
        from uuid import UUID
        self._recovery_scope(owner, 'lookup', context)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('annotation_limit_invalid')
        after = str(UUID(after)) if after is not None else None
        with self._pool.connection() as conn:
            if not self._lock_recovery_parent(conn, owner, context):
                return None
            rows = conn.execute("""SELECT * FROM server_context_annotations
                WHERE owner_user_id=%s AND context_id=%s AND deleted_at IS NULL
                  AND (%s::uuid IS NULL OR annotation_id>%s::uuid)
                ORDER BY annotation_id LIMIT %s""", (owner, context, after, after, limit+1)).fetchall()
            return {'items': [_stored(row) for row in rows[:limit]],
                    'next_cursor': str(rows[limit-1]['annotation_id']) if len(rows)>limit else None}

    def update_annotation(self, owner, context, annotation_id, *, body, step, expected_revision, require_storage_consent=False):
        """위치와 출처는 고정하고 본문·단계만 현재 revision에서 수정한다."""
        return self._change_annotation(owner, context, annotation_id, expected_revision, body, step, False, require_storage_consent)

    def delete_annotation(self, owner, context, annotation_id, *, expected_revision):
        """현재 revision의 기록을 지우고 생성 키 이력은 보존한다."""
        return self._change_annotation(owner, context, annotation_id, expected_revision, None, None, True)

    def _change_annotation(self, owner, context, annotation_id, expected_revision, body, step, delete, require_storage_consent=False):
        self._recovery_scope(owner, 'change', context)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('annotation_revision_invalid')
        with self._pool.connection() as conn:
            if not self._lock_recovery_parent(conn, owner, context):
                return None
            current = conn.execute("""SELECT * FROM server_context_annotations
                WHERE owner_user_id=%s AND context_id=%s AND annotation_id=%s
                  AND deleted_at IS NULL FOR UPDATE""", (owner, context, annotation_id)).fetchone()
            if current is None:
                return None
            if current['revision'] != expected_revision:
                raise RemoteAnnotationConflict('annotation_revision_conflict')
            if delete:
                row = conn.execute("""UPDATE server_context_annotations SET body='',segments='[]'::jsonb,
                    deleted_at=CURRENT_TIMESTAMP,revision=revision+1,updated_at=CURRENT_TIMESTAMP
                    WHERE owner_user_id=%s AND annotation_id=%s RETURNING *""", (owner, annotation_id)).fetchone()
                conn.execute("""UPDATE server_annotation_create_requests SET state='deleted'
                    WHERE owner_user_id=%s AND annotation_id=%s""", (owner, annotation_id))
            else:
                if require_storage_consent:
                    from .remote_annotation_consents import require_annotation_consent
                    require_annotation_consent(conn, owner, context)
                validate_annotation_fields(current['kind'], body, step, len(current['segments']))
                row = conn.execute("""UPDATE server_context_annotations SET body=%s,step=%s,
                    revision=revision+1,updated_at=CURRENT_TIMESTAMP
                    WHERE owner_user_id=%s AND annotation_id=%s RETURNING *""",
                    (body, step, owner, annotation_id)).fetchone()
            return _stored(row)

    def mark_deleted(self, owner_user_id: str, annotation_id: str) -> bool:
        """본문을 제거하고 생성 키 tombstone을 남긴다. 후속 삭제 route가 호출할 경계다."""
        if not all(isinstance(item, str) and item for item in (owner_user_id, annotation_id)):
            raise ValueError("remote_annotation_scope_invalid")
        with self._pool.connection() as conn:
            parent = conn.execute(
                """SELECT p.paper_id FROM server_papers p
                   JOIN server_execution_contexts c
                     ON c.owner_user_id=p.owner_user_id AND c.paper_id=p.paper_id
                   JOIN server_context_annotations a
                     ON a.owner_user_id=c.owner_user_id AND a.context_id=c.context_id
                   WHERE a.owner_user_id=%s AND a.annotation_id=%s
                   FOR UPDATE OF p""",
                (owner_user_id, annotation_id),
            ).fetchone()
            if parent is None:
                return False
            row = conn.execute(
                """UPDATE server_context_annotations
                   SET body='',segments='[]'::jsonb,deleted_at=CURRENT_TIMESTAMP,
                       revision=revision+1,updated_at=CURRENT_TIMESTAMP
                   WHERE owner_user_id=%s AND annotation_id=%s AND deleted_at IS NULL
                   RETURNING annotation_id""",
                (owner_user_id, annotation_id),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """UPDATE server_annotation_create_requests SET state='deleted'
                       WHERE owner_user_id=%s AND annotation_id=%s""",
                    (owner_user_id, annotation_id),
                )
        return row is not None


def _stored(row) -> StoredRemoteAnnotation:
    segments = row["segments"]
    if isinstance(segments, str):
        segments = json.loads(segments)
    return StoredRemoteAnnotation(
        annotation_id=str(row["annotation_id"]), context_id=str(row["context_id"]),
        kind=row["kind"], body=row["body"], step=row["step"], segments=tuple(segments),
        creation_origin=row["creation_origin"], revision=int(row["revision"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
