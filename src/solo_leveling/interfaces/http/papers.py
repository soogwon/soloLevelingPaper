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
class PaperListItem:
    paper_id: str
    title: str | None
    authors: tuple[str, ...] | None
    publication_year: int | None
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


class PaperStoreUnavailable(Exception):
    pass


class PaperStore(Protocol):
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration: ...

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int) -> PaperPage: ...


class UnavailablePaperStore:
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration:
        raise PaperStoreUnavailable

    def list_papers(self, owner_user_id: str, *, query: str | None,
                    reading_status: str | None, page: int, limit: int) -> PaperPage:
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
        # 학습 기록 동기화 전에는 상태를 추정하지 않는다. 명시 상태 필터에는 미확인 논문이 포함되지 않는다.
        if reading_status not in (None, "unknown"):
            filters.append("FALSE")
        where = " AND ".join(filters)
        with _connection(self._pool) as conn:
            # 등록과 목록 조회가 겹쳐도 전체 건수와 항목을 같은 시점에서 읽는다.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            total = int(conn.execute(
                f"SELECT count(*) AS total_count FROM server_papers WHERE {where}", tuple(params)
            ).fetchone()["total_count"])
            total_pages = (total + limit - 1) // limit
            if page > total_pages:
                return PaperPage((), page, limit, total, total_pages)
            rows = conn.execute(
                f"""SELECT paper_id, title, authors, publication_year
                    FROM server_papers WHERE {where}
                    ORDER BY paper_id ASC LIMIT %s OFFSET %s""",
                (*params, limit, (page - 1) * limit),
            ).fetchall()
        items = tuple(PaperListItem(
            paper_id=str(row["paper_id"]), title=row["title"],
            authors=tuple(row["authors"]) if row["authors"] is not None else None,
            publication_year=row["publication_year"],
        ) for row in rows)
        return PaperPage(items, page, limit, total, total_pages)


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
