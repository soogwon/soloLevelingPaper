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


class IdempotencyConflict(Exception):
    pass


class PaperStoreUnavailable(Exception):
    pass


class PaperStore(Protocol):
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration: ...


class UnavailablePaperStore:
    def register(self, owner_user_id: str, key: str, metadata: PaperMetadata) -> PaperRegistration:
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


def _payload_hash(metadata: PaperMetadata) -> bytes:
    payload = asdict(metadata)
    payload["authors"] = list(metadata.authors) if metadata.authors is not None else None
    payload["original_created_at"] = metadata.original_created_at.astimezone(timezone.utc).isoformat()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).digest()


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
