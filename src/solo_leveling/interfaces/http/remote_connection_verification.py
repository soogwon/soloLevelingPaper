"""웹 계정과 현재 원격 MCP 요청을 한 번 대조한다."""

from hashlib import sha256
import re
from secrets import token_urlsafe
from uuid import uuid4


def issue_check(conn, owner_user_id: str) -> dict:
    code = token_urlsafe(24)
    check_id = uuid4()
    row = conn.execute(
        """INSERT INTO server_remote_connection_checks(owner_user_id, check_id, code_hash, expires_at)
           VALUES (%s, %s, %s, clock_timestamp() + interval '5 minutes')
           ON CONFLICT (owner_user_id) DO UPDATE SET check_id=excluded.check_id,
               code_hash=excluded.code_hash, expires_at=excluded.expires_at, verified_at=NULL
           RETURNING expires_at""",
        (owner_user_id, check_id, sha256(code.encode()).hexdigest()),
    ).fetchone()
    return {"check_id": check_id, "code": code, "expires_at": row["expires_at"]}


def check_status(conn, owner_user_id: str, check_id) -> dict | None:
    return conn.execute(
        """SELECT CASE WHEN verified_at IS NOT NULL THEN 'verified'
                        WHEN expires_at <= clock_timestamp() THEN 'expired'
                        ELSE 'pending' END AS status, verified_at
           FROM server_remote_connection_checks WHERE owner_user_id=%s AND check_id=%s""",
        (owner_user_id, check_id),
    ).fetchone()


def confirm_check(conn, issuer: str, subject: str, code: str) -> dict:
    """코드를 한 번 소비하고, 실패하면 사용자가 고칠 수 있는 이유만 구분해 돌려준다."""
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", code):
        return {"status": "not_found"}
    code_hash = sha256(code.encode()).hexdigest()
    row = conn.execute(
        """UPDATE server_remote_connection_checks AS c SET verified_at=clock_timestamp()
           WHERE code_hash=%s AND verified_at IS NULL AND expires_at > clock_timestamp()
             AND EXISTS (
                 SELECT 1 FROM server_remote_identities AS i
                 WHERE i.owner_user_id=c.owner_user_id AND i.issuer=%s AND i.subject=%s
                   AND i.revoked_at IS NULL
             )
           RETURNING verified_at""",
        (code_hash, issuer, subject),
    ).fetchone()
    if row:
        return {"status": "ok", "verified_at": row["verified_at"]}
    # 코드는 192비트 난수라 다른 계정의 유효 코드임을 알려도 추측 공격에 쓸 정보가 없다.
    row = conn.execute(
        """SELECT verified_at IS NOT NULL AS used, expires_at <= clock_timestamp() AS expired,
                  EXISTS (
                      SELECT 1 FROM server_remote_identities AS i
                      WHERE i.owner_user_id=c.owner_user_id AND i.issuer=%s AND i.subject=%s
                        AND i.revoked_at IS NULL
                  ) AS same_account
           FROM server_remote_connection_checks AS c WHERE code_hash=%s""",
        (issuer, subject, code_hash),
    ).fetchone()
    if row is None:
        return {"status": "not_found"}
    if not row["same_account"]:
        return {"status": "different_account"}
    return {"status": "already_used" if row["used"] else "expired"}
