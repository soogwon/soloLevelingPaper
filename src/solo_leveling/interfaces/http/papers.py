"""사용자 소유 논문 메타데이터의 멱등 PostgreSQL 등록."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from typing import Any, Protocol
from uuid import uuid4

from .paper_visibility import VISIBLE_PAPER

@dataclass(frozen=True)
class PaperMetadata:
    source_instance_id: str
    local_paper_id: str
    title: str | None
    authors: tuple[str, ...] | None
    publication_year: int | None
    original_created_at: datetime


@dataclass(frozen=True)
class PaperRegistration:
    paper_id: str
    revision: int
    sync_status: str
    server_updated_at: datetime


@dataclass(frozen=True)
class LearningRecordMetadata:
    source_instance_id: str
    local_learning_record_id: str
    selected_version_id: str | None
    file_name: str | None
    last_position: dict[str, Any] | None
    last_studied_at: datetime | None
    reading_status: str | None
    source_updated_at: datetime


@dataclass(frozen=True)
class LearningRecordRegistration:
    learning_record_id: str
    revision: int
    sync_status: str
    server_updated_at: datetime


@dataclass(frozen=True)
class SummaryMetadata:
    source_instance_id: str
    local_learning_record_id: str
    local_summary_id: str
    scope: str
    author_type: str
    items: tuple[dict[str, Any], ...]
    original_created_at: datetime
    source_updated_at: datetime
    selected_version_id: str | None


@dataclass(frozen=True)
class SummaryRegistration:
    summary_id: str
    revision: int
    sync_status: str
    server_updated_at: datetime


@dataclass(frozen=True)
class AnnotationMetadata:
    source_instance_id: str
    local_learning_record_id: str
    local_annotation_id: str
    selected_version_id: str | None
    kind: str
    body: str
    step: str | None
    segments: tuple[dict[str, Any], ...]
    original_created_at: datetime
    source_updated_at: datetime
    creation_origin: str | None = None


@dataclass(frozen=True)
class AnnotationRegistration:
    annotation_id: str
    revision: int
    sync_status: str
    server_updated_at: datetime


@dataclass(frozen=True)
class StoredAnnotation:
    annotation_id: str
    paper_id: str
    paper_title: str | None
    learning_record_id: str | None
    selected_version_id: str | None
    kind: str
    body: str
    step: str | None
    segments: tuple[dict[str, Any], ...]
    created_at: datetime
    updated_at: datetime
    creation_origin: str = 'unknown'
    context_id: str | None = None
    storage_origin: str = 'local_sync'


@dataclass(frozen=True)
class AnnotationDateCount:
    date: date
    count: int


@dataclass(frozen=True)
class AnnotationPage:
    items: tuple[StoredAnnotation, ...]
    date_counts: tuple[AnnotationDateCount, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int


@dataclass(frozen=True)
class StoredSummary:
    summary_id: str
    created_at: datetime
    author_type: str
    items: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PaperListItem:
    paper_id: str
    title: str | None
    authors: tuple[str, ...] | None
    publication_year: int | None
    file_name: str | None = None
    selected_learning_record_id: str | None = None
    selected_version_id: str | None = None
    last_position: dict[str, Any] | None = None
    last_studied_at: datetime | None = None
    reading_status: str = "unknown"
    collection_id: str | None = None
    reading_basis_version_id: str | None = None
    reading_state_revision: int = 0
    reading_status_source: str = "unknown"
    reading_context_id: str | None = None
    reading_status_updated_at: datetime | None = None


@dataclass(frozen=True)
class PaperPage:
    items: tuple[PaperListItem, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int


@dataclass(frozen=True)
class PaperDetail:
    paper: PaperListItem
    summary: StoredSummary | None = None


class IdempotencyConflict(Exception):
    pass


class LearningRecordConflict(Exception):
    pass


class SummaryConflict(Exception):
    pass


class AnnotationConflict(Exception):
    pass


class PaperSelectionConflict(Exception):
    pass


class PaperNotFound(Exception):
    pass


PAPER_SORT_DEFAULT_ORDER = {"recent": "desc", "title": "asc", "status": "asc", "updated": "desc"}
# 정렬 식은 고정 목록에서만 고르므로 SQL에 사용자 입력이 섞이지 않는다.
_PAPER_SORT_EXPRESSIONS = {
    "recent": "r.last_studied_at",
    "title": ("lower(COALESCE(p.title, CASE WHEN r.learning_record_id IS NULL "
              "THEN remote_file.file_name ELSE r.file_name END))"),
    # 학습 진행 순서(읽기 전 → 읽는 중 → 읽기 완료), 상태 미확인은 방향과 관계없이 맨 뒤.
    "status": ("CASE reading.status WHEN 'not_started' THEN 0 WHEN 'reading' THEN 1 "
               "WHEN 'completed' THEN 2 END"),
    "updated": "reading.status_updated_at",
}


def _paper_order_by(sort: str, order: str | None) -> str:
    if sort not in _PAPER_SORT_EXPRESSIONS:
        raise ValueError(f"unsupported paper sort: {sort}")
    direction = order or PAPER_SORT_DEFAULT_ORDER[sort]
    if direction not in ("asc", "desc"):
        raise ValueError(f"unsupported paper order: {order}")
    return f"{_PAPER_SORT_EXPRESSIONS[sort]} {direction.upper()} NULLS LAST"


class PaperStoreUnavailable(Exception):
    pass


class PaperStore(Protocol):
    def get_reading_state(self, owner_user_id: str, paper_id: str, source_instance_id: str): ...

    def put_reading_state(self, owner_user_id: str, paper_id: str, request: dict): ...

    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration: ...

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int,
                    collection_id: str | None = None, unassigned: bool = False,
                    sort: str = "recent", order: str | None = None) -> PaperPage: ...

    def upsert_learning_record(self, owner_user_id: str, paper_id: str,
                               metadata: LearningRecordMetadata) -> LearningRecordRegistration: ...

    def upsert_summary(self, owner_user_id: str, paper_id: str,
                       metadata: SummaryMetadata) -> SummaryRegistration: ...

    def upsert_annotation(self, owner_user_id: str, paper_id: str,
                          metadata: AnnotationMetadata) -> AnnotationRegistration: ...

    def list_annotations(self, owner_user_id: str, *, kind: str | None,
                         start_date: date | None, end_date: date | None, query: str | None,
                         sort: str, paper_id: str | None, version_id: str | None,
                         page: int, limit: int) -> AnnotationPage: ...

    def get_annotation(self, owner_user_id: str, annotation_id: str) -> StoredAnnotation: ...

    def get_paper(self, owner_user_id: str, paper_id: str, *, learning_record_id: str | None,
                  version_id: str | None) -> PaperDetail: ...


class UnavailablePaperStore:
    def get_reading_state(self, owner_user_id: str, paper_id: str, source_instance_id: str):
        raise PaperStoreUnavailable

    def put_reading_state(self, owner_user_id: str, paper_id: str, request: dict):
        raise PaperStoreUnavailable

    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration:
        raise PaperStoreUnavailable

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int,
                    collection_id: str | None = None, unassigned: bool = False,
                    sort: str = "recent", order: str | None = None) -> PaperPage:
        raise PaperStoreUnavailable

    def upsert_learning_record(self, owner_user_id: str, paper_id: str,
                               metadata: LearningRecordMetadata) -> LearningRecordRegistration:
        raise PaperStoreUnavailable

    def upsert_summary(self, owner_user_id: str, paper_id: str,
                       metadata: SummaryMetadata) -> SummaryRegistration:
        raise PaperStoreUnavailable

    def upsert_annotation(self, owner_user_id: str, paper_id: str,
                          metadata: AnnotationMetadata) -> AnnotationRegistration:
        raise PaperStoreUnavailable

    def list_annotations(self, owner_user_id: str, **filters) -> AnnotationPage:
        raise PaperStoreUnavailable

    def get_annotation(self, owner_user_id: str, annotation_id: str) -> StoredAnnotation:
        raise PaperStoreUnavailable

    def get_paper(self, owner_user_id: str, paper_id: str, *, learning_record_id: str | None,
                  version_id: str | None) -> PaperDetail:
        raise PaperStoreUnavailable


class PostgresPaperStore:
    OPERATION = "register_paper"

    def __init__(self, pool: Any):
        self._pool = pool

    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration:
        payload_hash = _payload_hash(metadata)
        with _connection(self._pool) as conn:
            conn.execute(
                """INSERT INTO server_idempotency_records
                   (owner_user_id, operation, idempotency_key, payload_hash)
                   VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (owner_user_id, self.OPERATION, key, payload_hash),
            )
            idem = conn.execute(
                """SELECT payload_hash, response_status, response_body
                   FROM server_idempotency_records
                   WHERE owner_user_id = %s AND operation = %s AND idempotency_key = %s
                   FOR UPDATE""",
                (owner_user_id, self.OPERATION, key),
            ).fetchone()
            if idem is None:
                raise PaperStoreUnavailable
            if bytes(idem["payload_hash"]) != payload_hash:
                raise IdempotencyConflict
            if idem["response_body"] is not None:
                return _registration(idem["response_body"])

            paper = conn.execute(
                """INSERT INTO server_papers
                   (paper_id, owner_user_id, source_instance_id, local_paper_id, title, authors,
                    publication_year, original_created_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                   ON CONFLICT (owner_user_id, source_instance_id, local_paper_id) DO UPDATE SET
                       title = COALESCE(server_papers.title, EXCLUDED.title),
                       revision = CASE
                           WHEN server_papers.title IS NULL AND EXCLUDED.title IS NOT NULL
                           THEN server_papers.revision + 1 ELSE server_papers.revision END,
                       updated_at = CASE
                           WHEN server_papers.title IS NULL AND EXCLUDED.title IS NOT NULL
                           THEN CURRENT_TIMESTAMP ELSE server_papers.updated_at END
                   RETURNING paper_id, revision, updated_at""",
                (
                    str(uuid4()), owner_user_id, metadata.source_instance_id, metadata.local_paper_id,
                    metadata.title, json.dumps(metadata.authors) if metadata.authors is not None else None,
                    metadata.publication_year, metadata.original_created_at,
                ),
            ).fetchone()
            if paper is None:
                paper = conn.execute(
                    """SELECT paper_id, revision, updated_at FROM server_papers
                       WHERE owner_user_id = %s AND source_instance_id = %s AND local_paper_id = %s""",
                    (owner_user_id, metadata.source_instance_id, metadata.local_paper_id),
                ).fetchone()
            if paper is None:
                raise PaperStoreUnavailable
            result = PaperRegistration(str(paper["paper_id"]), int(paper["revision"]), "stored", paper["updated_at"])
            body = _response_body(result)
            conn.execute(
                """UPDATE server_idempotency_records SET response_status = 201, response_body = %s::jsonb
                   WHERE owner_user_id = %s AND operation = %s AND idempotency_key = %s""",
                (json.dumps(body), owner_user_id, self.OPERATION, key),
            )
            return result

    def get_reading_state(self, owner_user_id, paper_id, source_instance_id):
        from .reading_states import get_state
        return get_state(self._pool, owner_user_id, paper_id, source_instance_id)

    def put_reading_state(self, owner_user_id, paper_id, request):
        from .reading_states import put_state
        return put_state(self._pool, owner_user_id, paper_id, request)

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int,
                    collection_id: str | None = None, unassigned: bool = False,
                    sort: str = "recent", order: str | None = None) -> PaperPage:
        """웹 목록용 offset 페이지를 소유권 범위에서 안정적인 순서로 조회한다."""
        order_by = _paper_order_by(sort, order)
        filters = ["p.owner_user_id = %s", VISIBLE_PAPER]
        params: list[Any] = [owner_user_id]
        if query is not None:
            filters.append("p.title ILIKE %s ESCAPE '\\'")
            params.append(f"%{_escape_like(query)}%")
        if reading_status is not None:
            filters.append("reading.status = %s")
            params.append(reading_status)
        if collection_id is not None:
            filters.append("cp.collection_id = %s")
            params.append(collection_id)
        elif unassigned:
            filters.append("cp.collection_id IS NULL")
        where = " AND ".join(filters)
        latest = """LEFT JOIN LATERAL (
            SELECT learning_record_id, selected_version_id, file_name, last_position,
                   last_studied_at, reading_status
            FROM server_learning_records
            WHERE owner_user_id=p.owner_user_id AND paper_id=p.paper_id
            ORDER BY last_studied_at DESC NULLS LAST, learning_record_id ASC LIMIT 1
        ) r ON TRUE LEFT JOIN LATERAL (
            SELECT u.server_version_id, u.file_name, u.updated_at
            FROM server_pdf_uploads u
            JOIN server_processing_versions v ON v.owner_user_id=u.owner_user_id
              AND v.paper_id=u.paper_id AND v.server_version_id=u.server_version_id
            WHERE p.origin='remote' AND u.source_kind='remote' AND u.status='ready'
              AND u.owner_user_id=p.owner_user_id AND u.paper_id=p.paper_id
            ORDER BY u.updated_at DESC, u.upload_id ASC LIMIT 1
        ) remote_file ON TRUE LEFT JOIN server_paper_reading_states rs ON rs.paper_id=p.paper_id
          LEFT JOIN LATERAL (
            SELECT rl.status, rl.context_id, rl.updated_at, rl.server_version_id
            FROM server_remote_learning_records rl
            WHERE rl.owner_user_id=p.owner_user_id AND rl.paper_id=p.paper_id
              AND rl.server_version_id=COALESCE(r.selected_version_id, remote_file.server_version_id)
            ORDER BY rl.updated_at DESC, rl.context_id LIMIT 1
          ) remote_learning ON TRUE
          LEFT JOIN LATERAL (
            SELECT
              CASE
                WHEN rs.basis_version_id=COALESCE(r.selected_version_id, remote_file.server_version_id)
                  THEN rs.status
                WHEN remote_learning.status IS NOT NULL THEN remote_learning.status
                WHEN remote_file.server_version_id IS NOT NULL THEN 'not_started'
                ELSE 'unknown'
              END AS status,
              CASE
                WHEN rs.basis_version_id=COALESCE(r.selected_version_id, remote_file.server_version_id)
                  THEN 'explicit'
                WHEN remote_learning.status IS NOT NULL THEN 'remote_learning'
                WHEN remote_file.server_version_id IS NOT NULL THEN 'remote_file'
                ELSE 'unknown'
              END AS source,
              CASE
                WHEN rs.basis_version_id=COALESCE(r.selected_version_id, remote_file.server_version_id)
                  THEN rs.basis_version_id
                WHEN remote_learning.status IS NOT NULL THEN remote_learning.server_version_id
                WHEN remote_file.server_version_id IS NOT NULL THEN remote_file.server_version_id
                ELSE NULL
              END AS basis_version_id,
              CASE WHEN remote_learning.status IS NOT NULL
                AND rs.basis_version_id IS DISTINCT FROM
                    COALESCE(r.selected_version_id, remote_file.server_version_id)
                THEN remote_learning.context_id ELSE NULL END AS context_id,
              CASE
                WHEN rs.basis_version_id=COALESCE(r.selected_version_id, remote_file.server_version_id)
                  THEN rs.updated_at
                WHEN remote_learning.status IS NOT NULL THEN remote_learning.updated_at
                WHEN remote_file.server_version_id IS NOT NULL THEN remote_file.updated_at
                ELSE NULL
              END AS status_updated_at,
              CASE WHEN rs.basis_version_id=COALESCE(r.selected_version_id, remote_file.server_version_id)
                THEN rs.revision ELSE 0 END AS revision
          ) reading ON TRUE
          LEFT JOIN server_collection_papers cp
          ON cp.owner_user_id=p.owner_user_id AND cp.paper_id=p.paper_id"""
        with _connection(self._pool) as conn:
            # 등록과 목록 조회가 겹쳐도 전체 건수와 항목을 같은 시점에서 읽는다.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            total = int(conn.execute(
                f"SELECT count(*) AS total_count FROM server_papers p {latest} WHERE {where}", tuple(params)
            ).fetchone()["total_count"])
            total_pages = (total + limit - 1) // limit
            if page > total_pages:
                return PaperPage((), page, limit, total, total_pages)
            rows = conn.execute(
                f"""SELECT p.paper_id, p.title, p.authors, p.publication_year,
                           r.learning_record_id,
                           CASE WHEN r.learning_record_id IS NULL THEN remote_file.server_version_id
                                ELSE r.selected_version_id END AS selected_version_id,
                           CASE WHEN r.learning_record_id IS NULL THEN remote_file.file_name
                                ELSE r.file_name END AS file_name,
                           r.last_position, r.last_studied_at, reading.status AS reading_status,
                           reading.basis_version_id AS reading_basis_version_id,
                           reading.revision AS reading_state_revision,
                           reading.source AS reading_status_source,
                           reading.context_id AS reading_context_id,
                           reading.status_updated_at AS reading_status_updated_at,
                           cp.collection_id
                    FROM server_papers p {latest} WHERE {where}
                    ORDER BY {order_by}, p.paper_id ASC LIMIT %s OFFSET %s""",
                (*params, limit, (page - 1) * limit),
            ).fetchall()
        items = tuple(PaperListItem(
            paper_id=str(row["paper_id"]), title=row["title"],
            authors=tuple(row["authors"]) if row["authors"] is not None else None,
            publication_year=row["publication_year"],
            file_name=row.get("file_name"),
            selected_learning_record_id=(str(row.get("learning_record_id"))
                                         if row.get("learning_record_id") is not None else None),
            selected_version_id=row.get("selected_version_id"), last_position=row.get("last_position"),
            last_studied_at=row.get("last_studied_at"), reading_status=row.get("reading_status") or "unknown",
            collection_id=(str(row.get("collection_id")) if row.get("collection_id") else None),
            reading_basis_version_id=row.get("reading_basis_version_id"),
            reading_state_revision=row.get("reading_state_revision") or 0,
            reading_status_source=row.get("reading_status_source") or "unknown",
            reading_context_id=(str(row.get("reading_context_id"))
                                if row.get("reading_context_id") is not None else None),
            reading_status_updated_at=row.get("reading_status_updated_at"),
        ) for row in rows)
        return PaperPage(items, page, limit, total, total_pages)

    def upsert_learning_record(self, owner_user_id: str, paper_id: str,
                               metadata: LearningRecordMetadata) -> LearningRecordRegistration:
        values = (
            str(uuid4()), owner_user_id, paper_id, metadata.source_instance_id,
            metadata.local_learning_record_id, metadata.selected_version_id, metadata.file_name,
            json.dumps(metadata.last_position) if metadata.last_position is not None else None,
            metadata.last_studied_at, metadata.reading_status, metadata.source_updated_at,
        )
        with _connection(self._pool) as conn:
            row = conn.execute(
                """INSERT INTO server_learning_records
                   (learning_record_id, owner_user_id, paper_id, source_instance_id,
                    local_learning_record_id, selected_version_id, file_name, last_position,
                    last_studied_at, reading_status, source_updated_at)
                   SELECT %s, %s, p.paper_id, %s, %s, %s, %s, %s::jsonb, %s, COALESCE(%s, 'unknown'), %s
                   FROM server_papers p
                   WHERE p.paper_id=%s AND p.owner_user_id=%s AND p.source_instance_id=%s
                   ON CONFLICT(owner_user_id, source_instance_id, local_learning_record_id)
                   DO UPDATE SET selected_version_id=excluded.selected_version_id,
                     file_name=excluded.file_name, last_position=excluded.last_position,
                     last_studied_at=excluded.last_studied_at,
                     reading_status=CASE WHEN %s THEN excluded.reading_status ELSE server_learning_records.reading_status END,
                     source_updated_at=excluded.source_updated_at, revision=server_learning_records.revision+1,
                     updated_at=CURRENT_TIMESTAMP
                   WHERE excluded.paper_id=server_learning_records.paper_id
                     AND excluded.source_updated_at>server_learning_records.source_updated_at
                   RETURNING learning_record_id, revision, updated_at""",
                (values[0], values[1], *values[3:], paper_id, owner_user_id, metadata.source_instance_id,
                 metadata.reading_status is not None),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT learning_record_id, revision, updated_at, source_updated_at,
                              selected_version_id, file_name, last_position, last_studied_at, reading_status
                       FROM server_learning_records
                       WHERE owner_user_id=%s AND source_instance_id=%s AND local_learning_record_id=%s
                         AND paper_id=%s""",
                    (owner_user_id, metadata.source_instance_id,
                     metadata.local_learning_record_id, paper_id),
                ).fetchone()
                if row is not None:
                    if row["source_updated_at"] != metadata.source_updated_at:
                        raise LearningRecordConflict
                    fields = ("selected_version_id", "file_name", "last_position", "last_studied_at", "reading_status")
                    if any(row[field] != getattr(metadata, field) for field in fields
                           if field != 'reading_status' or metadata.reading_status is not None):
                        raise LearningRecordConflict
            if row is None:
                raise PaperNotFound
        return LearningRecordRegistration(
            str(row["learning_record_id"]), int(row["revision"]), "stored", row["updated_at"]
        )

    def upsert_summary(self, owner_user_id: str, paper_id: str,
                       metadata: SummaryMetadata) -> SummaryRegistration:
        with _connection(self._pool) as conn:
            record = conn.execute(
                """SELECT learning_record_id, selected_version_id FROM server_learning_records
                   WHERE paper_id=%s AND owner_user_id=%s AND source_instance_id=%s
                     AND local_learning_record_id=%s FOR SHARE""",
                (paper_id, owner_user_id, metadata.source_instance_id, metadata.local_learning_record_id),
            ).fetchone()
            if record is None:
                raise PaperNotFound
            if record["selected_version_id"] != metadata.selected_version_id:
                raise SummaryConflict
            row = conn.execute(
                """INSERT INTO server_summaries
                   (summary_id, owner_user_id, paper_id, learning_record_id, source_instance_id,
                    local_summary_id, scope, author_type, items, original_created_at, source_updated_at,
                    selected_version_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                   ON CONFLICT(owner_user_id, source_instance_id, local_summary_id)
                   DO UPDATE SET scope=excluded.scope, author_type=excluded.author_type,
                     items=excluded.items, original_created_at=excluded.original_created_at,
                     source_updated_at=excluded.source_updated_at,
                     revision=server_summaries.revision+1, updated_at=CURRENT_TIMESTAMP
                   WHERE excluded.paper_id=server_summaries.paper_id
                     AND excluded.selected_version_id IS NOT DISTINCT FROM server_summaries.selected_version_id
                     AND excluded.learning_record_id=server_summaries.learning_record_id
                     AND excluded.source_updated_at>server_summaries.source_updated_at
                   RETURNING summary_id, revision, updated_at""",
                (str(uuid4()), owner_user_id, paper_id, record["learning_record_id"], metadata.source_instance_id,
                 metadata.local_summary_id,
                 metadata.scope, metadata.author_type, json.dumps(metadata.items, ensure_ascii=False),
                 metadata.original_created_at, metadata.source_updated_at, metadata.selected_version_id),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT s.summary_id, s.revision, s.updated_at, s.paper_id, s.learning_record_id,
                              s.selected_version_id, s.scope, s.author_type, s.items, s.original_created_at, s.source_updated_at,
                              r.local_learning_record_id
                       FROM server_summaries s JOIN server_learning_records r
                         ON r.learning_record_id=s.learning_record_id
                       WHERE s.owner_user_id=%s AND s.source_instance_id=%s AND s.local_summary_id=%s""",
                    (owner_user_id, metadata.source_instance_id, metadata.local_summary_id),
                ).fetchone()
                if row is not None and (str(row["paper_id"]) != paper_id
                                        or row["selected_version_id"] != metadata.selected_version_id
                                        or row["local_learning_record_id"] != metadata.local_learning_record_id):
                    raise SummaryConflict
                if row is not None:
                    if row["source_updated_at"] != metadata.source_updated_at:
                        raise AnnotationConflict
                    expected = (metadata.scope, metadata.author_type, list(metadata.items),
                                metadata.original_created_at)
                    actual = (row["scope"], row["author_type"], row["items"], row["original_created_at"])
                    if actual != expected:
                        raise SummaryConflict
            if row is None:
                raise PaperNotFound
        return SummaryRegistration(str(row["summary_id"]), int(row["revision"]), "stored", row["updated_at"])

    def upsert_annotation(self, owner_user_id: str, paper_id: str,
                          metadata: AnnotationMetadata) -> AnnotationRegistration:
        with _connection(self._pool) as conn:
            record = conn.execute(
                """SELECT learning_record_id, selected_version_id FROM server_learning_records
                   WHERE paper_id=%s AND owner_user_id=%s AND source_instance_id=%s
                     AND local_learning_record_id=%s FOR SHARE""",
                (paper_id, owner_user_id, metadata.source_instance_id,
                 metadata.local_learning_record_id),
            ).fetchone()
            if record is None:
                raise PaperNotFound
            if record["selected_version_id"] != metadata.selected_version_id:
                raise AnnotationConflict
            row = conn.execute(
                """INSERT INTO server_annotations
                   (annotation_id, owner_user_id, paper_id, learning_record_id, source_instance_id,
                    local_annotation_id, selected_version_id, kind, body, step, segments,
                    original_created_at, source_updated_at, creation_origin)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, COALESCE(%s, 'unknown'))
                   ON CONFLICT(owner_user_id, source_instance_id, local_annotation_id)
                   DO UPDATE SET kind=excluded.kind, body=excluded.body, step=excluded.step,
                     segments=excluded.segments, source_updated_at=excluded.source_updated_at,
                     revision=server_annotations.revision+1, updated_at=CURRENT_TIMESTAMP
                   WHERE excluded.paper_id=server_annotations.paper_id
                     AND excluded.learning_record_id=server_annotations.learning_record_id
                     AND excluded.selected_version_id IS NOT DISTINCT FROM server_annotations.selected_version_id
                     AND excluded.original_created_at=server_annotations.original_created_at
                     AND (%s OR excluded.creation_origin=server_annotations.creation_origin)
                     AND excluded.source_updated_at>server_annotations.source_updated_at
                   RETURNING annotation_id, revision, updated_at""",
                (str(uuid4()), owner_user_id, paper_id, record["learning_record_id"],
                 metadata.source_instance_id, metadata.local_annotation_id,
                 metadata.selected_version_id, metadata.kind, metadata.body, metadata.step,
                 json.dumps(metadata.segments, ensure_ascii=False), metadata.original_created_at,
                 metadata.source_updated_at, metadata.creation_origin, metadata.creation_origin is None),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT a.annotation_id, a.revision, a.updated_at, a.paper_id,
                              a.learning_record_id, a.selected_version_id, a.kind, a.body,
                              a.step, a.segments, a.original_created_at, a.source_updated_at, a.creation_origin,
                              r.local_learning_record_id
                       FROM server_annotations a JOIN server_learning_records r
                         ON r.learning_record_id=a.learning_record_id
                       WHERE a.owner_user_id=%s AND a.source_instance_id=%s
                         AND a.local_annotation_id=%s""",
                    (owner_user_id, metadata.source_instance_id, metadata.local_annotation_id),
                ).fetchone()
                if row is not None and (str(row["paper_id"]) != paper_id
                                        or row["selected_version_id"] != metadata.selected_version_id
                                        or row["local_learning_record_id"] != metadata.local_learning_record_id):
                    raise AnnotationConflict
                if row is not None and metadata.creation_origin is not None and row['creation_origin'] != metadata.creation_origin:
                    raise AnnotationConflict
                if row is not None:
                    if row["source_updated_at"] != metadata.source_updated_at:
                        raise AnnotationConflict
                    expected = (metadata.kind, metadata.body, metadata.step,
                                list(metadata.segments), metadata.original_created_at)
                    actual = (row["kind"], row["body"], row["step"], row["segments"],
                              row["original_created_at"])
                    if actual != expected:
                        raise AnnotationConflict
            if row is None:
                raise PaperNotFound
        return AnnotationRegistration(str(row["annotation_id"]), int(row["revision"]),
                                      "stored", row["updated_at"])

    def list_annotations(self, owner_user_id: str, *, kind: str | None,
                         start_date: date | None, end_date: date | None, query: str | None,
                         sort: str, paper_id: str | None, version_id: str | None,
                         page: int, limit: int) -> AnnotationPage:
        if sort not in ("newest", "oldest"):
            raise ValueError("지원하지 않는 메모 정렬입니다.")
        conditions = ["TRUE"]
        params: list[Any] = [owner_user_id, owner_user_id]
        if kind is not None:
            conditions.append("a.kind=%s")
            params.append(kind)
        if start_date is not None:
            conditions.append("a.created_at >= (%s::date::timestamp AT TIME ZONE 'Asia/Seoul')")
            params.append(start_date)
        if end_date is not None:
            conditions.append("a.created_at < ((%s::date + 1)::timestamp AT TIME ZONE 'Asia/Seoul')")
            params.append(end_date)
        if paper_id is not None:
            conditions.append("a.paper_id=%s")
            params.append(paper_id)
        if version_id is not None:
            conditions.append("a.selected_version_id=%s")
            params.append(version_id)
        if query is not None:
            pattern = f"%{_escape_like(query)}%"
            conditions.append(
                """(a.body ILIKE %s ESCAPE '\\' OR a.paper_title ILIKE %s ESCAPE '\\'
                    OR EXISTS (SELECT 1 FROM jsonb_array_elements(a.segments) segment
                               WHERE segment->>'quote_snapshot' ILIKE %s ESCAPE '\\'))"""
            )
            params.extend((pattern, pattern, pattern))
        where = " AND ".join(conditions)
        direction = "ASC" if sort == "oldest" else "DESC"
        # 두 저장소는 공통 원본 식별자를 공유하지 않는다. 본문·시각이 같다는 이유로
        # 서로 다른 사용자 기록을 합치지 않고 저장 출처와 불변 annotation_id를 보존한다.
        unified = """WITH unified AS (
            SELECT a.annotation_id,a.paper_id,p.title AS paper_title,a.learning_record_id,
                   a.selected_version_id,a.kind,a.body,a.step,a.segments,
                   a.original_created_at AS created_at,a.source_updated_at AS updated_at,
                   a.creation_origin,NULL::uuid AS context_id,'local_sync'::text AS storage_origin
            FROM server_annotations a
            JOIN server_papers p ON p.owner_user_id=a.owner_user_id AND p.paper_id=a.paper_id
            WHERE a.owner_user_id=%s AND p.deleted_at IS NULL
            UNION ALL
            SELECT a.annotation_id,c.paper_id,p.title AS paper_title,r.learning_record_id,
                   c.server_version_id AS selected_version_id,a.kind,a.body,a.step,a.segments,
                   a.created_at,a.updated_at,a.creation_origin,a.context_id,
                   'remote'::text AS storage_origin
            FROM server_context_annotations a
            JOIN server_execution_contexts c
              ON c.owner_user_id=a.owner_user_id AND c.context_id=a.context_id
            JOIN server_papers p
              ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
            LEFT JOIN server_remote_learning_records r
              ON r.owner_user_id=a.owner_user_id AND r.context_id=a.context_id
            WHERE a.owner_user_id=%s AND a.deleted_at IS NULL AND p.deleted_at IS NULL
        ) """
        base = "FROM unified a"
        with _connection(self._pool) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            total = int(conn.execute(
                unified + f"SELECT count(*) AS total_count {base} WHERE {where}", tuple(params),
            ).fetchone()["total_count"])
            total_pages = (total + limit - 1) // limit
            counts = conn.execute(
                unified + f"""SELECT (a.created_at AT TIME ZONE 'Asia/Seoul')::date AS created_date,
                           count(*) AS item_count {base} WHERE {where}
                    GROUP BY created_date ORDER BY created_date {direction}""", tuple(params),
            ).fetchall()
            rows = [] if page > total_pages else conn.execute(
                unified + f"""SELECT a.annotation_id, a.paper_id, a.paper_title,
                           a.learning_record_id, a.selected_version_id, a.kind, a.body, a.step,
                            a.segments, a.created_at, a.updated_at, a.creation_origin,
                            a.context_id, a.storage_origin
                    {base} WHERE {where}
                     ORDER BY a.created_at {direction}, a.storage_origin {direction},
                              a.annotation_id {direction}
                    LIMIT %s OFFSET %s""", (*params, limit, (page - 1) * limit),
            ).fetchall()
        return AnnotationPage(
            tuple(_stored_annotation(row) for row in rows),
            tuple(AnnotationDateCount(row["created_date"], int(row["item_count"])) for row in counts),
            page, limit, total, total_pages,
        )

    def get_annotation(self, owner_user_id: str, annotation_id: str) -> StoredAnnotation:
        with _connection(self._pool) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            row = conn.execute(
                """SELECT * FROM (
                     SELECT a.annotation_id,a.paper_id,p.title AS paper_title,a.learning_record_id,
                            a.selected_version_id,a.kind,a.body,a.step,a.segments,
                            a.original_created_at AS created_at,a.source_updated_at AS updated_at,
                            a.creation_origin,NULL::uuid AS context_id,'local_sync'::text AS storage_origin
                     FROM server_annotations a
                     JOIN server_papers p ON p.owner_user_id=a.owner_user_id AND p.paper_id=a.paper_id
                     WHERE a.owner_user_id=%s AND a.annotation_id=%s AND p.deleted_at IS NULL
                     UNION ALL
                     SELECT a.annotation_id,c.paper_id,p.title AS paper_title,r.learning_record_id,
                            c.server_version_id,a.kind,a.body,a.step,a.segments,a.created_at,a.updated_at,
                            a.creation_origin,a.context_id,'remote'::text AS storage_origin
                     FROM server_context_annotations a
                     JOIN server_execution_contexts c
                       ON c.owner_user_id=a.owner_user_id AND c.context_id=a.context_id
                     JOIN server_papers p
                       ON p.owner_user_id=c.owner_user_id AND p.paper_id=c.paper_id
                     LEFT JOIN server_remote_learning_records r
                       ON r.owner_user_id=a.owner_user_id AND r.context_id=a.context_id
                     WHERE a.owner_user_id=%s AND a.annotation_id=%s
                       AND a.deleted_at IS NULL AND p.deleted_at IS NULL
                   ) found ORDER BY storage_origin LIMIT 1""",
                (owner_user_id, annotation_id, owner_user_id, annotation_id),
            ).fetchone()
        if row is None:
            raise PaperNotFound
        return _stored_annotation(row)

    def get_paper(self, owner_user_id: str, paper_id: str, *, learning_record_id: str | None,
                  version_id: str | None) -> PaperDetail:
        with _connection(self._pool) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            paper = conn.execute(
                f"""SELECT p.paper_id, p.title, p.authors, p.publication_year, p.origin,
                          s.status AS reading_status, s.basis_version_id AS reading_basis_version_id,
                          s.revision AS reading_state_revision, s.updated_at AS reading_status_updated_at
                   FROM server_papers p LEFT JOIN server_paper_reading_states s ON s.paper_id=p.paper_id
                   WHERE p.paper_id=%s AND p.owner_user_id=%s AND {VISIBLE_PAPER}""", (paper_id, owner_user_id),
            ).fetchone()
            if paper is None:
                raise PaperNotFound
            params: list[Any] = [owner_user_id, paper_id]
            condition = ""
            if learning_record_id is not None:
                condition = " AND learning_record_id=%s"
                params.append(learning_record_id)
            elif version_id is not None:
                condition = " AND selected_version_id=%s"
                params.append(version_id)
            record = conn.execute(
                f"""SELECT learning_record_id, selected_version_id, file_name, last_position,
                            last_studied_at, reading_status
                     FROM server_learning_records
                     WHERE owner_user_id=%s AND paper_id=%s{condition}
                     ORDER BY last_studied_at DESC NULLS LAST, learning_record_id ASC LIMIT 1""",
                tuple(params),
            ).fetchone()
            remote_file = None
            if record is None and learning_record_id is None and paper.get("origin") == "remote":
                # 학습 이력을 만들지 않고 소유자의 검증된 원격 판본만 선택한다.
                remote_file = conn.execute(
                    """SELECT u.server_version_id, u.file_name, u.updated_at
                       FROM server_pdf_uploads u JOIN server_processing_versions v
                         ON v.owner_user_id=u.owner_user_id AND v.paper_id=u.paper_id
                        AND v.server_version_id=u.server_version_id
                       WHERE u.owner_user_id=%s AND u.paper_id=%s
                         AND u.source_kind='remote' AND u.status='ready'
                         AND (%s::text IS NULL OR u.server_version_id=%s)
                       ORDER BY u.updated_at DESC,u.upload_id ASC LIMIT 1""",
                    (owner_user_id, paper_id, version_id, version_id),
                ).fetchone()
            selected_version_id = (record["selected_version_id"] if record else
                                   remote_file["server_version_id"] if remote_file else None)
            reading = ({"status": paper.get("reading_status"),
                        "basis_version_id": paper.get("reading_basis_version_id"),
                        "revision": paper.get("reading_state_revision") or 0,
                        "updated_at": paper.get("reading_status_updated_at")}
                       if selected_version_id is not None
                       and paper.get("reading_basis_version_id") == selected_version_id else None)
            reading_source = "explicit" if reading is not None else "unknown"
            reading_context_id = None
            if (reading is None and selected_version_id is not None
                    and paper.get("origin") == "remote"):
                reading = conn.execute(
                    """SELECT status, server_version_id AS basis_version_id, 0 AS revision,
                              updated_at, context_id
                       FROM server_remote_learning_records
                       WHERE owner_user_id=%s AND paper_id=%s AND server_version_id=%s
                       ORDER BY updated_at DESC, context_id LIMIT 1""",
                    (owner_user_id, paper_id, selected_version_id),
                ).fetchone()
                if reading is not None:
                    reading_source = "remote_learning"
                    reading_context_id = str(reading["context_id"])
            if reading is None and remote_file is not None:
                reading = {"status": "not_started", "basis_version_id": selected_version_id,
                           "revision": 0, "updated_at": remote_file.get("updated_at")}
                reading_source = "remote_file"
            summary = None
            if record is not None:
                summary = conn.execute(
                    """SELECT summary_id, author_type, items, original_created_at
                       FROM server_summaries
                       WHERE owner_user_id=%s AND paper_id=%s AND learning_record_id=%s AND scope='paper'
                         AND selected_version_id IS NOT DISTINCT FROM %s
                       ORDER BY source_updated_at DESC, summary_id ASC LIMIT 1""",
                    (owner_user_id, paper_id, record["learning_record_id"], record["selected_version_id"]),
                ).fetchone()
        if learning_record_id is not None and record is None:
            raise PaperNotFound
        if version_id is not None and record is None and remote_file is None:
            raise PaperNotFound
        if (record is not None and version_id is not None
                and record["selected_version_id"] != version_id):
            raise PaperSelectionConflict
        item = PaperListItem(
            paper_id=str(paper["paper_id"]), title=paper["title"],
            authors=tuple(paper["authors"]) if paper["authors"] is not None else None,
            publication_year=paper["publication_year"],
            file_name=record["file_name"] if record else remote_file["file_name"] if remote_file else None,
            selected_learning_record_id=str(record["learning_record_id"]) if record else None,
            selected_version_id=selected_version_id,
            last_position=record["last_position"] if record else None,
            last_studied_at=record["last_studied_at"] if record else None,
            reading_status=reading["status"] if reading is not None else "unknown",
            reading_basis_version_id=(reading.get("basis_version_id") if reading is not None else None),
            reading_state_revision=(reading.get("revision") or 0 if reading is not None else 0),
            reading_status_source=reading_source,
            reading_context_id=reading_context_id,
            reading_status_updated_at=(reading.get("updated_at") if reading is not None else None),
        )
        stored_summary = (StoredSummary(
            str(summary["summary_id"]), summary["original_created_at"], summary["author_type"],
            tuple(summary["items"]),
        ) if summary is not None else None)
        return PaperDetail(item, stored_summary)


def _payload_hash(metadata: PaperMetadata) -> bytes:
    payload = asdict(metadata)
    payload["authors"] = list(metadata.authors) if metadata.authors is not None else None
    payload["original_created_at"] = metadata.original_created_at.astimezone(timezone.utc).isoformat()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).digest()


def _stored_annotation(row) -> StoredAnnotation:
    return StoredAnnotation(
        str(row["annotation_id"]), str(row["paper_id"]), row["paper_title"],
        str(row["learning_record_id"]) if row.get("learning_record_id") is not None else None,
        row["selected_version_id"], row["kind"], row["body"], row["step"],
        tuple(row["segments"]), row.get("created_at", row.get("original_created_at")),
        row["updated_at"], row.get('creation_origin', 'unknown'),
        str(row["context_id"]) if row.get("context_id") is not None else None,
        row.get("storage_origin", "local_sync"),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _response_body(result: PaperRegistration) -> dict[str, Any]:
    return {
        "paper_id": result.paper_id,
        "revision": result.revision,
        "sync_status": result.sync_status,
        "server_updated_at": result.server_updated_at.isoformat(),
    }


def _registration(body: dict[str, Any]) -> PaperRegistration:
    return PaperRegistration(
        str(body["paper_id"]), int(body["revision"]), str(body["sync_status"]),
        datetime.fromisoformat(str(body["server_updated_at"])),
    )


@contextmanager
def _connection(pool: Any):
    try:
        with pool.connection() as conn:
            yield conn
    except Exception as exc:
        from psycopg import Error
        from psycopg_pool import PoolTimeout

        if isinstance(exc, (Error, PoolTimeout)):
            raise PaperStoreUnavailable from None
        raise
