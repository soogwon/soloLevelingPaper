"""중앙 서버 기기 등록 모델과 PostgreSQL 저장소."""

from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager
from typing import Any, Protocol
from uuid import uuid4

from .device_auth import DeviceRegistry


@dataclass(frozen=True)
class DeviceRecord:
    device_id: str
    owner_user_id: str
    display_name: str
    created_at: datetime
    revoked_at: datetime | None = None


class DeviceStore(DeviceRegistry, Protocol):
    def register(self, owner_user_id: str, display_name: str) -> DeviceRecord: ...
    def list_active(self, owner_user_id: str) -> tuple[DeviceRecord, ...]: ...
    def revoke(self, owner_user_id: str, device_id: str) -> None: ...


class DeviceStoreUnavailable(Exception):
    pass


class UnavailableDeviceStore:
    def is_active(self, user_id: str, device_id: str) -> bool:
        return False

    def register(self, owner_user_id: str, display_name: str) -> DeviceRecord:
        raise DeviceStoreUnavailable

    def list_active(self, owner_user_id: str) -> tuple[DeviceRecord, ...]:
        raise DeviceStoreUnavailable

    def revoke(self, owner_user_id: str, device_id: str) -> None:
        raise DeviceStoreUnavailable


class PostgresDeviceStore:
    """모든 변경·조회에 owner_user_id를 포함하는 PostgreSQL adapter."""

    def __init__(self, pool: Any):
        self._pool = pool

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresDeviceStore":
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        return cls(ConnectionPool(conninfo=dsn, kwargs={"row_factory": dict_row}, open=False))

    def open(self) -> None:
        try:
            self._pool.open(wait=True)
        except Exception as exc:
            if _is_database_error(exc):
                raise DeviceStoreUnavailable from None
            raise

    def close(self) -> None:
        self._pool.close()

    def is_active(self, user_id: str, device_id: str) -> bool:
        with _connection(self._pool) as conn:
            row = conn.execute(
                """SELECT 1 FROM server_devices
                   WHERE owner_user_id = %s AND device_id = %s AND revoked_at IS NULL""",
                (user_id, device_id),
            ).fetchone()
        return row is not None

    def register(self, owner_user_id: str, display_name: str) -> DeviceRecord:
        device_id = str(uuid4())
        with _connection(self._pool) as conn:
            row = conn.execute(
                """INSERT INTO server_devices (device_id, owner_user_id, display_name)
                   VALUES (%s, %s, %s)
                   RETURNING device_id, owner_user_id, display_name, created_at, revoked_at""",
                (device_id, owner_user_id, display_name),
            ).fetchone()
        if row is None:
            raise DeviceStoreUnavailable
        return _record(row)

    def list_active(self, owner_user_id: str) -> tuple[DeviceRecord, ...]:
        with _connection(self._pool) as conn:
            rows = conn.execute(
                """SELECT device_id, owner_user_id, display_name, created_at, revoked_at
                   FROM server_devices
                   WHERE owner_user_id = %s AND revoked_at IS NULL
                   ORDER BY created_at DESC, device_id DESC""",
                (owner_user_id,),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def revoke(self, owner_user_id: str, device_id: str) -> None:
        with _connection(self._pool) as conn:
            conn.execute(
                """UPDATE server_devices SET revoked_at = CURRENT_TIMESTAMP
                   WHERE owner_user_id = %s AND device_id = %s AND revoked_at IS NULL""",
                (owner_user_id, device_id),
            )


def _record(row: dict[str, Any]) -> DeviceRecord:
    return DeviceRecord(
        device_id=str(row["device_id"]),
        owner_user_id=row["owner_user_id"],
        display_name=row["display_name"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


@contextmanager
def _connection(pool: Any):
    try:
        with pool.connection() as conn:
            yield conn
    except Exception as exc:
        if _is_database_error(exc):
            raise DeviceStoreUnavailable from None
        raise


def _is_database_error(exc: Exception) -> bool:
    from psycopg import Error
    from psycopg_pool import PoolTimeout

    return isinstance(exc, (Error, PoolTimeout))
