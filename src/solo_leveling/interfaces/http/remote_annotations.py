"""인증된 원격 context의 메모 생성 원장과 원자적 PostgreSQL 저장."""

import hashlib
import json
import re
from dataclasses import dataclass
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
               value: RemoteAnnotationInput) -> RemoteAnnotationCreateResult:
        if not all(isinstance(item, str) and item for item in (owner_user_id, context_id)):
            raise ValueError("remote_annotation_scope_invalid")
        if not _SERVICE_ID.fullmatch(service_id) or not _REQUEST_KEY.fullmatch(request_key):
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

    def mark_deleted(self, owner_user_id: str, annotation_id: str) -> bool:
        """본문을 제거하고 생성 키 tombstone을 남긴다. 후속 삭제 route가 호출할 경계다."""
        if not all(isinstance(item, str) and item for item in (owner_user_id, annotation_id)):
            raise ValueError("remote_annotation_scope_invalid")
        with self._pool.connection() as conn:
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
