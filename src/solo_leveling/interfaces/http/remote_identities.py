"""검증된 원격 인증 주체를 서버 owner에 명시적으로 연결한다."""

from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity


class RemoteIdentityConflict(ValueError):
    """동일 issuer·subject가 다른 owner 또는 폐기된 연결과 충돌한다."""


def _validated_identity(identity: VerifiedRemoteIdentity) -> tuple[str, str]:
    if not isinstance(identity, VerifiedRemoteIdentity):
        raise ValueError("remote_identity_invalid")
    issuer, subject = identity.issuer, identity.subject
    if (
        issuer != issuer.strip()
        or subject != subject.strip()
        or len(issuer) > 2048
        or len(subject) > 512
    ):
        raise ValueError("remote_identity_invalid")
    return issuer, subject


class PostgresRemoteOwnerResolver:
    """issuer와 불투명 raw subject의 저장된 연결만 사용한다."""

    def __init__(self, connection_pool, *, auto_link_issuer=None):
        self._pool = connection_pool
        self._auto_link_issuer = auto_link_issuer

    def resolve_owner(self, identity: VerifiedRemoteIdentity) -> str | None:
        issuer, subject = _validated_identity(identity)
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT owner_user_id
                   FROM server_remote_identities
                   WHERE issuer=%s AND subject=%s AND revoked_at IS NULL""",
                (issuer, subject),
            ).fetchone()
            if row is None and issuer == self._auto_link_issuer:
                row = conn.execute(
                    "SELECT server_link_verified_mcp_account(%s,%s) AS owner_user_id",
                    (issuer,subject),
                ).fetchone()
        return None if row is None or row["owner_user_id"] is None else str(row["owner_user_id"])

    def link_identity(
        self,
        owner_user_id: str,
        identity: VerifiedRemoteIdentity,
    ) -> str:
        """인증된 계정 연결 흐름에서 호출한다. 자동 계정 생성이나 재연결은 하지 않는다."""
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise ValueError("owner_user_id_invalid")
        issuer, subject = _validated_identity(identity)
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO server_remote_identities(issuer, subject, owner_user_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (issuer, subject) DO UPDATE
                     SET owner_user_id=server_remote_identities.owner_user_id
                   RETURNING owner_user_id, revoked_at""",
                (issuer, subject, owner_user_id),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or str(row["owner_user_id"]) != owner_user_id:
                raise RemoteIdentityConflict("remote_identity_conflict")
        return owner_user_id

    def revoke_identity(
        self,
        owner_user_id: str,
        identity: VerifiedRemoteIdentity,
    ) -> bool:
        """소유자가 일치하는 활성 연결만 폐기하며 자동 재활성화하지 않는다."""
        issuer, subject = _validated_identity(identity)
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_remote_identities
                   SET revoked_at=CURRENT_TIMESTAMP
                   WHERE issuer=%s AND subject=%s AND owner_user_id=%s AND revoked_at IS NULL
                   RETURNING owner_user_id""",
                (issuer, subject, owner_user_id),
            ).fetchone()
        return row is not None
