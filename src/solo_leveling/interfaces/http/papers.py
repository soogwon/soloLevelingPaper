"""사용자 소유 논문 메타데이터의 멱등 PostgreSQL 등록."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    reading_status: str
    source_updated_at: datetime


@dataclass(frozen=True)
class LearningRecordRegistration:
    learning_record_id: str
    revision: int
    sync_status: str
    server_updated_at: datetime


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


class IdempotencyConflict(Exception):
    pass


class LearningRecordConflict(Exception):
    pass


class PaperNotFound(Exception):
    pass


class PaperStoreUnavailable(Exception):
    pass


class PaperStore(Protocol):
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration: ...

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int) -> PaperPage: ...

    def upsert_learning_record(self, owner_user_id: str, paper_id: str,
                               metadata: LearningRecordMetadata) -> LearningRecordRegistration: ...


class UnavailablePaperStore:
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration:
        raise PaperStoreUnavailable

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int) -> PaperPage:
        raise PaperStoreUnavailable

    def upsert_learning_record(self, owner_user_id: str, paper_id: str,
                               metadata: LearningRecordMetadata) -> LearningRecordRegistration:
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
                   ON CONFLICT (owner_user_id, source_instance_id, local_paper_id) DO NOTHING
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
                    reading_status: str | None, page: int, limit: int) -> PaperPage:
        """웹 목록용 offset 페이지를 소유권 범위에서 안정적인 순서로 조회한다."""
        filters = ["owner_user_id = %s"]
        params: list[Any] = [owner_user_id]
        if query is not None:
            filters.append("title ILIKE %s ESCAPE '\\'")
            params.append(f"%{_escape_like(query)}%")
        if reading_status is not None:
            filters.append("COALESCE(r.reading_status, 'unknown') = %s")
            params.append(reading_status)
        where = " AND ".join(filters)
        latest = """LEFT JOIN LATERAL (
            SELECT learning_record_id, selected_version_id, file_name, last_position,
                   last_studied_at, reading_status
            FROM server_learning_records
            WHERE owner_user_id=p.owner_user_id AND paper_id=p.paper_id
            ORDER BY last_studied_at DESC NULLS LAST, learning_record_id ASC LIMIT 1
        ) r ON TRUE"""
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
                           r.last_position, r.last_studied_at, r.reading_status
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
                   SELECT %s, %s, p.paper_id, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
                   FROM server_papers p
                   WHERE p.paper_id=%s AND p.owner_user_id=%s AND p.source_instance_id=%s
                   ON CONFLICT(owner_user_id, source_instance_id, local_learning_record_id)
                   DO UPDATE SET selected_version_id=excluded.selected_version_id,
                     file_name=excluded.file_name, last_position=excluded.last_position,
                     last_studied_at=excluded.last_studied_at, reading_status=excluded.reading_status,
                     source_updated_at=excluded.source_updated_at, revision=server_learning_records.revision+1,
                     updated_at=CURRENT_TIMESTAMP
                   WHERE excluded.paper_id=server_learning_records.paper_id
                     AND excluded.source_updated_at>server_learning_records.source_updated_at
                   RETURNING learning_record_id, revision, updated_at""",
                (values[0], values[1], *values[3:], paper_id, owner_user_id, metadata.source_instance_id),
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
                if row is not None and row["source_updated_at"] == metadata.source_updated_at:
                    fields = ("selected_version_id", "file_name", "last_position", "last_studied_at", "reading_status")
                    if any(row[field] != getattr(metadata, field) for field in fields):
                        raise LearningRecordConflict
            if row is None:
                raise PaperNotFound
        return LearningRecordRegistration(
            str(row["learning_record_id"]), int(row["revision"]), "stored", row["updated_at"]
        )


def _payload_hash(metadata: PaperMetadata) -> bytes:
    payload = asdict(metadata)
    payload["authors"] = list(metadata.authors) if metadata.authors is not None else None
    payload["original_created_at"] = metadata.original_created_at.astimezone(timezone.utc).isoformat()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).digest()


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
