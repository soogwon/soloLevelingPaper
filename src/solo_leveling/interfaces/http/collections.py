"""사용자 소유 컬렉션의 PostgreSQL 저장 계약."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class CollectionItem:
    collection_id: str
    name: str
    paper_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CollectionPage:
    items: tuple[CollectionItem, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int


@dataclass(frozen=True)
class CollectionPaperItem:
    paper_id: str
    title: str | None
    file_name: str | None
    authors: tuple[str, ...] | None
    publication_year: int | None
    added_at: datetime


@dataclass(frozen=True)
class CollectionPaperPage:
    items: tuple[CollectionPaperItem, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int


@dataclass(frozen=True)
class CollectionMembership:
    collection_id: str
    paper_id: str
    paper_count: int


class CollectionNotFound(Exception):
    pass


class CollectionPaperNotFound(Exception):
    pass


class CollectionMembershipConflict(Exception):
    pass


class CollectionNameConflict(Exception):
    pass


class CollectionIdempotencyConflict(Exception):
    pass


class CollectionStoreUnavailable(Exception):
    pass


class CollectionStore(Protocol):
    def list_collections(self, owner_user_id: str, *, page: int, limit: int) -> CollectionPage: ...
    def create_collection(self, owner_user_id: str, key: str, name: str,
                          normalized_name: str) -> CollectionItem: ...
    def get_collection(self, owner_user_id: str, collection_id: str) -> CollectionItem: ...
    def rename_collection(self, owner_user_id: str, collection_id: str, name: str,
                          normalized_name: str) -> CollectionItem: ...
    def delete_collection(self, owner_user_id: str, collection_id: str) -> None: ...
    def list_papers(self, owner_user_id: str, collection_id: str, *, page: int,
                    limit: int) -> CollectionPaperPage: ...
    def add_paper(self, owner_user_id: str, collection_id: str,
                  paper_id: str) -> CollectionMembership: ...
    def remove_paper(self, owner_user_id: str, collection_id: str,
                     paper_id: str) -> CollectionMembership: ...


class UnavailableCollectionStore:
    def __getattr__(self, _name):
        raise CollectionStoreUnavailable


class PostgresCollectionStore:
    OPERATION = "create_collection"

    def __init__(self, pool: Any):
        self._pool = pool

    def list_collections(self, owner_user_id: str, *, page: int, limit: int) -> CollectionPage:
        with _connection(self._pool) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            total = int(conn.execute(
                "SELECT count(*) AS total_count FROM server_collections WHERE owner_user_id=%s",
                (owner_user_id,),
            ).fetchone()["total_count"])
            pages = (total + limit - 1) // limit
            rows = [] if page > pages else conn.execute(
                """SELECT c.collection_id, c.name, c.created_at, c.updated_at,
                          count(cp.paper_id) AS paper_count
                   FROM server_collections c LEFT JOIN server_collection_papers cp
                     ON cp.collection_id=c.collection_id AND cp.owner_user_id=c.owner_user_id
                   WHERE c.owner_user_id=%s
                   GROUP BY c.collection_id
                   ORDER BY c.created_at DESC, c.collection_id DESC LIMIT %s OFFSET %s""",
                (owner_user_id, limit, (page - 1) * limit),
            ).fetchall()
        return CollectionPage(tuple(_collection(row) for row in rows), page, limit, total, pages)

    def create_collection(self, owner_user_id: str, key: str, name: str,
                          normalized_name: str) -> CollectionItem:
        digest = _create_hash(name)
        with _connection(self._pool) as conn:
            conn.execute(
                """INSERT INTO server_idempotency_records
                   (owner_user_id, operation, idempotency_key, payload_hash)
                   VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (owner_user_id, self.OPERATION, key, digest),
            )
            idem = conn.execute(
                """SELECT payload_hash, response_body FROM server_idempotency_records
                   WHERE owner_user_id=%s AND operation=%s AND idempotency_key=%s FOR UPDATE""",
                (owner_user_id, self.OPERATION, key),
            ).fetchone()
            if idem is None:
                raise CollectionStoreUnavailable
            if bytes(idem["payload_hash"]) != digest:
                raise CollectionIdempotencyConflict
            if idem["response_body"] is not None:
                return _collection(idem["response_body"])
            row = conn.execute(
                """INSERT INTO server_collections
                   (collection_id, owner_user_id, name, normalized_name)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT(owner_user_id, normalized_name) DO NOTHING
                   RETURNING collection_id, name, 0 AS paper_count, created_at, updated_at""",
                (str(uuid4()), owner_user_id, name, normalized_name),
            ).fetchone()
            if row is None:
                raise CollectionNameConflict
            body = _collection_body(_collection(row))
            conn.execute(
                """UPDATE server_idempotency_records SET response_status=201, response_body=%s::jsonb
                   WHERE owner_user_id=%s AND operation=%s AND idempotency_key=%s""",
                (json.dumps(body), owner_user_id, self.OPERATION, key),
            )
        return _collection(row)

    def get_collection(self, owner_user_id: str, collection_id: str) -> CollectionItem:
        with _connection(self._pool) as conn:
            row = conn.execute(
                """SELECT c.collection_id, c.name, c.created_at, c.updated_at,
                          count(cp.paper_id) AS paper_count
                   FROM server_collections c LEFT JOIN server_collection_papers cp
                     ON cp.collection_id=c.collection_id AND cp.owner_user_id=c.owner_user_id
                   WHERE c.owner_user_id=%s AND c.collection_id=%s GROUP BY c.collection_id""",
                (owner_user_id, collection_id),
            ).fetchone()
        if row is None:
            raise CollectionNotFound
        return _collection(row)

    def rename_collection(self, owner_user_id: str, collection_id: str, name: str,
                          normalized_name: str) -> CollectionItem:
        from psycopg.errors import UniqueViolation

        with _connection(self._pool) as conn:
            try:
                row = conn.execute(
                    """UPDATE server_collections c SET name=%s, normalized_name=%s,
                              updated_at=CURRENT_TIMESTAMP
                       WHERE c.owner_user_id=%s AND c.collection_id=%s
                       RETURNING c.collection_id, c.name, c.created_at, c.updated_at,
                         (SELECT count(*) FROM server_collection_papers cp
                          WHERE cp.owner_user_id=c.owner_user_id AND cp.collection_id=c.collection_id) AS paper_count""",
                    (name, normalized_name, owner_user_id, collection_id),
                ).fetchone()
            except UniqueViolation:
                raise CollectionNameConflict from None
            if row is None:
                raise CollectionNotFound
        return _collection(row)

    def delete_collection(self, owner_user_id: str, collection_id: str) -> None:
        with _connection(self._pool) as conn:
            row = conn.execute(
                "DELETE FROM server_collections WHERE owner_user_id=%s AND collection_id=%s RETURNING collection_id",
                (owner_user_id, collection_id),
            ).fetchone()
        if row is None:
            raise CollectionNotFound

    def list_papers(self, owner_user_id: str, collection_id: str, *, page: int,
                    limit: int) -> CollectionPaperPage:
        with _connection(self._pool) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            exists = conn.execute(
                "SELECT 1 FROM server_collections WHERE owner_user_id=%s AND collection_id=%s",
                (owner_user_id, collection_id),
            ).fetchone()
            if exists is None:
                raise CollectionNotFound
            total = int(conn.execute(
                """SELECT count(*) AS total_count FROM server_collection_papers
                   WHERE owner_user_id=%s AND collection_id=%s""",
                (owner_user_id, collection_id),
            ).fetchone()["total_count"])
            pages = (total + limit - 1) // limit
            rows = [] if page > pages else conn.execute(
                """SELECT p.paper_id, p.title, p.authors, p.publication_year, cp.added_at,
                          (SELECT r.file_name FROM server_learning_records r
                           WHERE r.owner_user_id=p.owner_user_id AND r.paper_id=p.paper_id
                           ORDER BY r.last_studied_at DESC NULLS LAST, r.learning_record_id ASC LIMIT 1) AS file_name
                   FROM server_collection_papers cp JOIN server_papers p
                     ON p.paper_id=cp.paper_id AND p.owner_user_id=cp.owner_user_id
                   WHERE cp.owner_user_id=%s AND cp.collection_id=%s
                   ORDER BY cp.added_at DESC, cp.paper_id DESC LIMIT %s OFFSET %s""",
                (owner_user_id, collection_id, limit, (page - 1) * limit),
            ).fetchall()
        items = tuple(CollectionPaperItem(
            str(row["paper_id"]), row["title"], row["file_name"],
            tuple(row["authors"]) if row["authors"] is not None else None,
            row["publication_year"], row["added_at"],
        ) for row in rows)
        return CollectionPaperPage(items, page, limit, total, pages)

    def add_paper(self, owner_user_id: str, collection_id: str,
                  paper_id: str) -> CollectionMembership:
        with _connection(self._pool) as conn:
            if conn.execute(
                "SELECT 1 FROM server_collections WHERE owner_user_id=%s AND collection_id=%s FOR KEY SHARE",
                (owner_user_id, collection_id),
            ).fetchone() is None:
                raise CollectionNotFound
            row = conn.execute(
                """INSERT INTO server_collection_papers(paper_id, collection_id, owner_user_id)
                   SELECT p.paper_id, c.collection_id, c.owner_user_id
                   FROM server_papers p JOIN server_collections c ON c.owner_user_id=p.owner_user_id
                   WHERE p.owner_user_id=%s AND p.paper_id=%s AND c.collection_id=%s
                   ON CONFLICT(paper_id) DO NOTHING
                   RETURNING collection_id, paper_id""",
                (owner_user_id, paper_id, collection_id),
            ).fetchone()
            if row is None:
                current = conn.execute(
                    """SELECT collection_id, paper_id FROM server_collection_papers
                       WHERE owner_user_id=%s AND paper_id=%s""",
                    (owner_user_id, paper_id),
                ).fetchone()
                if current is not None:
                    if str(current["collection_id"]) != collection_id:
                        raise CollectionMembershipConflict
                    row = current
                else:
                    raise CollectionPaperNotFound
            count = conn.execute(
                """SELECT count(*) AS paper_count FROM server_collection_papers
                   WHERE owner_user_id=%s AND collection_id=%s""",
                (owner_user_id, collection_id),
            ).fetchone()["paper_count"]
        return CollectionMembership(str(row["collection_id"]), str(row["paper_id"]), int(count))

    def remove_paper(self, owner_user_id: str, collection_id: str,
                     paper_id: str) -> CollectionMembership:
        with _connection(self._pool) as conn:
            exists = conn.execute(
                "SELECT 1 FROM server_collections WHERE owner_user_id=%s AND collection_id=%s",
                (owner_user_id, collection_id),
            ).fetchone()
            if exists is None:
                raise CollectionNotFound
            row = conn.execute(
                """DELETE FROM server_collection_papers
                   WHERE owner_user_id=%s AND collection_id=%s AND paper_id=%s RETURNING paper_id""",
                (owner_user_id, collection_id, paper_id),
            ).fetchone()
            if row is None:
                raise CollectionPaperNotFound
            count = conn.execute(
                """SELECT count(*) AS paper_count FROM server_collection_papers
                   WHERE owner_user_id=%s AND collection_id=%s""",
                (owner_user_id, collection_id),
            ).fetchone()["paper_count"]
        return CollectionMembership(collection_id, paper_id, int(count))


def _collection(row) -> CollectionItem:
    return CollectionItem(str(row["collection_id"]), row["name"], int(row["paper_count"]),
                          _timestamp(row["created_at"]), _timestamp(row["updated_at"]))


def _timestamp(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _collection_body(item: CollectionItem) -> dict[str, Any]:
    return {"collection_id": item.collection_id, "name": item.name,
            "paper_count": item.paper_count, "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat()}


def _create_hash(name: str) -> bytes:
    canonical = json.dumps({"name": name}, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).digest()


@contextmanager
def _connection(pool: Any):
    try:
        with pool.connection() as conn:
            yield conn
    except (CollectionNotFound, CollectionPaperNotFound, CollectionMembershipConflict,
            CollectionNameConflict,
            CollectionIdempotencyConflict, CollectionStoreUnavailable):
        raise
    except Exception as exc:
        from psycopg import Error
        from psycopg_pool import PoolTimeout
        if isinstance(exc, (Error, PoolTimeout)):
            raise CollectionStoreUnavailable from None
        raise
