"""웹과 원격 MCP의 일회용 연결 확인을 실제 PostgreSQL에서 검증한다."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os

import pytest

from solo_leveling.interfaces.http.remote_connection_verification import check_status, confirm_check, issue_check
from tests.integration.test_remote_processing_postgres import db, new_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="격리 PostgreSQL 필요")


def test_connection_check_is_owner_scoped_expires_and_consumes_once(pool, db):
    owner, other = new_id(), new_id()
    issuer = "https://issuer.example/"
    for subject in (owner, other):
        db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,%s)", (subject, "검증"))
        db("INSERT INTO server_remote_identities(issuer,subject,owner_user_id) VALUES (%s,%s,%s)",
           (issuer, subject, subject))
    with pool.connection() as conn:
        issued = issue_check(conn, owner)
        code = issued["code"]
        check_id = issued["check_id"]
        assert check_status(conn, owner, check_id)["status"] == "pending"
        assert check_status(conn, other, check_id) is None
        assert confirm_check(conn, issuer, other, code) == {"status": "different_account"}
        assert confirm_check(conn, "https://other.example/", owner, code) == {"status": "different_account"}
        assert confirm_check(conn, issuer, owner, "A" * 32) == {"status": "not_found"}
        assert confirm_check(conn, issuer, owner, code[:-1]) == {"status": "not_found"}
    digest, = db("SELECT code_hash FROM server_remote_connection_checks WHERE owner_user_id=%s", (owner,))
    assert digest["code_hash"] == sha256(code.encode()).hexdigest()

    def use_code():
        with pool.connection() as conn:
            return confirm_check(conn, issuer, owner, code)["status"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: use_code(), range(2))) == ["already_used", "ok"]
    with pool.connection() as conn:
        status = check_status(conn, owner, check_id)
        assert status["status"] == "verified" and status["verified_at"] is not None
        expired = issue_check(conn, owner)
        assert check_status(conn, owner, check_id) is None
        conn.execute("UPDATE server_remote_connection_checks SET expires_at=clock_timestamp() WHERE owner_user_id=%s",
                     (owner,))
        assert check_status(conn, owner, expired["check_id"])["status"] == "expired"
        assert confirm_check(conn, issuer, owner, expired["code"]) == {"status": "expired"}
        revoked = issue_check(conn, owner)
        conn.execute("UPDATE server_remote_identities SET revoked_at=clock_timestamp() WHERE subject=%s", (owner,))
        assert confirm_check(conn, issuer, owner, revoked["code"]) == {"status": "different_account"}
