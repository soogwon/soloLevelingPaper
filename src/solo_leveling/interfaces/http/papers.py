"""사용자 소유 논문 메타데이터의 멱등 PostgreSQL 등록."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from typing import Any, Protocol
from uuid import uuid4

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
    learning_record_id: str
    selected_version_id: str | None
    kind: str
    body: str
    step: str | None
    segments: tuple[dict[str, Any], ...]
    created_at: datetime
    updated_at: datetime


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


class PaperStoreUnavailable(Exception):
    pass


class PaperStore(Protocol):
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration: ...

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int,
                    collection_id: str | None = None, unassigned: bool = False) -> PaperPage: ...

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
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration:
        raise PaperStoreUnavailable

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int,
                    collection_id: str | None = None, unassigned: bool = False) -> PaperPage:
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

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int,
                    collection_id: str | None = None, unassigned: bool = False) -> PaperPage:
        """웹 목록용 offset 페이지를 소유권 범위에서 안정적인 순서로 조회한다."""
        filters = ["p.owner_user_id = %s"]
        params: list[Any] = [owner_user_id]
        if query is not None:
            filters.append("p.title ILIKE %s ESCAPE '\\'")
            params.append(f"%{_escape_like(query)}%")
        if reading_status is not None:
            filters.append("COALESCE(r.reading_status, 'unknown') = %s")
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
        ) r ON TRUE LEFT JOIN server_collection_papers cp
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
                           r.learning_record_id, r.selected_version_id, r.file_name,
                           r.last_position, r.last_studied_at, r.reading_status, cp.collection_id
                    FROM server_papers p {latest} WHERE {where}
                    ORDER BY r.last_studied_at DESC NULLS LAST, p.paper_id ASC LIMIT %s OFFSET %s""",
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
                    original_created_at, source_updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                   ON CONFLICT(owner_user_id, source_instance_id, local_annotation_id)
                   DO UPDATE SET kind=excluded.kind, body=excluded.body, step=excluded.step,
                     segments=excluded.segments, source_updated_at=excluded.source_updated_at,
                     revision=server_annotations.revision+1, updated_at=CURRENT_TIMESTAMP
                   WHERE excluded.paper_id=server_annotations.paper_id
                     AND excluded.learning_record_id=server_annotations.learning_record_id
                     AND excluded.selected_version_id IS NOT DISTINCT FROM server_annotations.selected_version_id
                     AND excluded.original_created_at=server_annotations.original_created_at
                     AND excluded.source_updated_at>server_annotations.source_updated_at
                   RETURNING annotation_id, revision, updated_at""",
                (str(uuid4()), owner_user_id, paper_id, record["learning_record_id"],
                 metadata.source_instance_id, metadata.local_annotation_id,
                 metadata.selected_version_id, metadata.kind, metadata.body, metadata.step,
                 json.dumps(metadata.segments, ensure_ascii=False), metadata.original_created_at,
                 metadata.source_updated_at),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT a.annotation_id, a.revision, a.updated_at, a.paper_id,
                              a.learning_record_id, a.selected_version_id, a.kind, a.body,
                              a.step, a.segments, a.original_created_at, a.source_updated_at,
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
        conditions = ["a.owner_user_id=%s"]
        params: list[Any] = [owner_user_id]
        if kind is not None:
            conditions.append("a.kind=%s")
            params.append(kind)
        if start_date is not None:
            conditions.append("a.original_created_at >= (%s::date::timestamp AT TIME ZONE 'Asia/Seoul')")
            params.append(start_date)
        if end_date is not None:
            conditions.append("a.original_created_at < ((%s::date + 1)::timestamp AT TIME ZONE 'Asia/Seoul')")
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
                """(a.body ILIKE %s ESCAPE '\\' OR p.title ILIKE %s ESCAPE '\\'
                    OR EXISTS (SELECT 1 FROM jsonb_array_elements(a.segments) segment
                               WHERE segment->>'quote_snapshot' ILIKE %s ESCAPE '\\'))"""
            )
            params.extend((pattern, pattern, pattern))
        where = " AND ".join(conditions)
        direction = "ASC" if sort == "oldest" else "DESC"
        base = "FROM server_annotations a JOIN server_papers p ON p.paper_id=a.paper_id"
        with _connection(self._pool) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            total = int(conn.execute(
                f"SELECT count(*) AS total_count {base} WHERE {where}", tuple(params),
            ).fetchone()["total_count"])
            total_pages = (total + limit - 1) // limit
            counts = conn.execute(
                f"""SELECT (a.original_created_at AT TIME ZONE 'Asia/Seoul')::date AS created_date,
                           count(*) AS item_count {base} WHERE {where}
                    GROUP BY created_date ORDER BY created_date {direction}""", tuple(params),
            ).fetchall()
            rows = [] if page > total_pages else conn.execute(
                f"""SELECT a.annotation_id, a.paper_id, p.title AS paper_title,
                           a.learning_record_id, a.selected_version_id, a.kind, a.body, a.step,
                           a.segments, a.original_created_at, a.source_updated_at AS updated_at
                    {base} WHERE {where}
                    ORDER BY a.original_created_at {direction}, a.annotation_id {direction}
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
                """SELECT a.annotation_id, a.paper_id, p.title AS paper_title,
                          a.learning_record_id, a.selected_version_id, a.kind, a.body, a.step,
                          a.segments, a.original_created_at, a.source_updated_at AS updated_at
                   FROM server_annotations a JOIN server_papers p ON p.paper_id=a.paper_id
                   WHERE a.owner_user_id=%s AND a.annotation_id=%s""",
                (owner_user_id, annotation_id),
            ).fetchone()
        if row is None:
            raise PaperNotFound
        return _stored_annotation(row)

    def get_paper(self, owner_user_id: str, paper_id: str, *, learning_record_id: str | None,
                  version_id: str | None) -> PaperDetail:
        with _connection(self._pool) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            paper = conn.execute(
                """SELECT paper_id, title, authors, publication_year FROM server_papers
                   WHERE paper_id=%s AND owner_user_id=%s""", (paper_id, owner_user_id),
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
        if version_id is not None and record is None:
            raise PaperNotFound
        if (record is not None and version_id is not None
                and record["selected_version_id"] != version_id):
            raise PaperSelectionConflict
        item = PaperListItem(
            paper_id=str(paper["paper_id"]), title=paper["title"],
            authors=tuple(paper["authors"]) if paper["authors"] is not None else None,
            publication_year=paper["publication_year"],
            file_name=record["file_name"] if record else None,
            selected_learning_record_id=str(record["learning_record_id"]) if record else None,
            selected_version_id=record["selected_version_id"] if record else None,
            last_position=record["last_position"] if record else None,
            last_studied_at=record["last_studied_at"] if record else None,
            reading_status=record["reading_status"] if record else "unknown",
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
        str(row["learning_record_id"]), row["selected_version_id"], row["kind"],
        row["body"], row["step"], tuple(row["segments"]), row["original_created_at"],
        row["updated_at"],
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
