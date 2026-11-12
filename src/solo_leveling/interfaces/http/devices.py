"""중앙 서버 기기 등록 모델과 PostgreSQL 저장소."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from hashlib import sha256
from secrets import token_urlsafe
from typing import Any, Protocol
from uuid import UUID, uuid4

from .device_auth import DeviceRegistry
from .auth import AuthenticatedPrincipal
from .device_tokens import (
    ACCESS_TOKEN_PREFIX,
    REFRESH_TOKEN_PREFIX,
    DeviceCredentialRejected,
    DeviceTokenPair,
)


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
    def issue_credentials(self, owner_user_id: str, device_id: str) -> DeviceTokenPair: ...
    def rotate_credentials(self, refresh_token: str) -> DeviceTokenPair: ...
    def authenticate_access_token(self, access_token: str) -> AuthenticatedPrincipal | None: ...


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

    def issue_credentials(self, owner_user_id: str, device_id: str) -> DeviceTokenPair:
        raise DeviceStoreUnavailable

    def rotate_credentials(self, refresh_token: str) -> DeviceTokenPair:
        raise DeviceStoreUnavailable

    def authenticate_access_token(self, access_token: str) -> AuthenticatedPrincipal | None:
        return None


class PostgresDeviceStore:
    """모든 변경·조회에 owner_user_id를 포함하는 PostgreSQL adapter."""

    def __init__(self, pool: Any):
        self._pool = pool

    @property
    def connection_pool(self):
        return self._pool

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
        try:
            device_id = str(UUID(device_id))
        except ValueError:
            return False
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

    def issue_credentials(self, owner_user_id: str, device_id: str) -> DeviceTokenPair:
        with _connection(self._pool) as conn:
            active = conn.execute(
                """SELECT 1 FROM server_devices
                   WHERE owner_user_id = %s AND device_id = %s AND revoked_at IS NULL
                   FOR UPDATE""",
                (owner_user_id, device_id),
            ).fetchone()
            if active is None:
                raise DeviceCredentialRejected
            conn.execute(
                """UPDATE server_device_credentials SET revoked_at = CURRENT_TIMESTAMP
                   WHERE device_id = %s AND revoked_at IS NULL""",
                (device_id,),
            )
            now = datetime.now(timezone.utc)
            pair, hashes = _new_token_pair(device_id, now, now + timedelta(days=30))
            _insert_credential(conn, str(uuid4()), str(uuid4()), device_id, pair, hashes)
        return pair

    def rotate_credentials(self, refresh_token: str) -> DeviceTokenPair:
        if not refresh_token.startswith(REFRESH_TOKEN_PREFIX) or not refresh_token.isascii():
            raise DeviceCredentialRejected
        refresh_hash = _token_hash(refresh_token)
        replay_detected = False
        pair = None
        with _connection(self._pool) as conn:
            # 재발급·해제와 같은 순서로 기기를 잠근 뒤 최신 자격 상태를 읽는다.
            device = conn.execute(
                """SELECT d.device_id FROM server_devices d
                   JOIN server_device_credentials c ON c.device_id = d.device_id
                   WHERE c.refresh_token_hash = %s AND d.revoked_at IS NULL FOR UPDATE OF d""",
                (refresh_hash,),
            ).fetchone()
            if device is None:
                raise DeviceCredentialRejected
            row = conn.execute(
                """SELECT credential_id, family_id, device_id, refresh_expires_at, revoked_at
                   FROM server_device_credentials WHERE refresh_token_hash = %s""",
                (refresh_hash,),
            ).fetchone()
            now = datetime.now(timezone.utc)
            if row is None:
                raise DeviceCredentialRejected
            if row["revoked_at"] is not None:
                conn.execute(
                    """UPDATE server_device_credentials SET revoked_at = CURRENT_TIMESTAMP
                       WHERE family_id = %s AND revoked_at IS NULL""",
                    (row["family_id"],),
                )
                replay_detected = True
            elif row["refresh_expires_at"] <= now:
                raise DeviceCredentialRejected
            else:
                conn.execute(
                    "UPDATE server_device_credentials SET revoked_at = CURRENT_TIMESTAMP WHERE credential_id = %s",
                    (row["credential_id"],),
                )
                pair, hashes = _new_token_pair(str(row["device_id"]), now, row["refresh_expires_at"])
                _insert_credential(conn, str(uuid4()), str(row["family_id"]), pair.device_id, pair, hashes)
        if replay_detected or pair is None:
            raise DeviceCredentialRejected
        return pair

    def authenticate_access_token(self, access_token: str) -> AuthenticatedPrincipal | None:
        if not access_token.startswith(ACCESS_TOKEN_PREFIX) or not access_token.isascii():
            return None
        with _connection(self._pool) as conn:
            row = conn.execute(
                """SELECT d.owner_user_id, d.device_id
                   FROM server_device_credentials c
                   JOIN server_devices d ON d.device_id = c.device_id
                   WHERE c.access_token_hash = %s AND c.revoked_at IS NULL
                     AND c.access_expires_at > CURRENT_TIMESTAMP AND d.revoked_at IS NULL""",
                (_token_hash(access_token),),
            ).fetchone()
        if row is None:
            return None
        return AuthenticatedPrincipal(row["owner_user_id"], "device", str(row["device_id"]))


def _record(row: dict[str, Any]) -> DeviceRecord:
    return DeviceRecord(
        device_id=str(row["device_id"]),
        owner_user_id=row["owner_user_id"],
        display_name=row["display_name"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


def _new_token_pair(device_id: str, now: datetime, refresh_expires_at: datetime):
    access_token = ACCESS_TOKEN_PREFIX + token_urlsafe(32)
    refresh_token = REFRESH_TOKEN_PREFIX + token_urlsafe(32)
    access_expires_at = min(now + timedelta(minutes=15), refresh_expires_at)
    pair = DeviceTokenPair(access_token, access_expires_at, refresh_token, refresh_expires_at, device_id)
    return pair, (_token_hash(access_token), _token_hash(refresh_token))


def _token_hash(token: str) -> bytes:
    return sha256(token.encode("ascii")).digest()


def _insert_credential(conn, credential_id, family_id, device_id, pair, hashes) -> None:
    conn.execute(
        """INSERT INTO server_device_credentials
           (credential_id, family_id, device_id, access_token_hash, access_expires_at,
            refresh_token_hash, refresh_expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (credential_id, family_id, device_id, hashes[0], pair.access_expires_at, hashes[1], pair.refresh_expires_at),
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
