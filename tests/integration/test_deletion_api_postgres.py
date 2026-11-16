"""인증된 삭제 API를 실제 PostgreSQL 표식과 연결한다. 테스트 전용 스키마만 사용한다."""

# ruff: noqa: F811
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_remote_processing_postgres import pool, db, make_paper  # noqa: F401
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore

pytestmark = pytest.mark.skipif(not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="실제 PostgreSQL DSN 필요")


def test_two_clients_lost_ack_replay_and_tombstone_rejects_late_write(db, pool):
    import psycopg

    owner = "deletion-api-owner"
    paper = make_paper(db, owner)
    key = "deletion-api-fixed-key"
    path = f"/v1/papers/{paper}/deletion-requests/{key}"
    app = create_app(
        token_verifier=SimpleNamespace(
            verify=lambda token: AuthenticatedPrincipal(owner if token != "other" else "other", "device", token)
        ),
        device_registry=SimpleNamespace(is_active=lambda *_: True),
        deletion_store=PostgresRemoteDeletionStore(pool),
    )
    body = {"confirmed": True, "notice_version": "server-paper-deletion-v1"}
    with TestClient(app) as first, TestClient(app) as second:
        a, b = {"Authorization": "Bearer device-a"}, {"Authorization": "Bearer device-b"}
        assert first.get(path, headers=a).status_code == 404
        assert db("SELECT deleted_at FROM server_papers WHERE paper_id=%s", (paper,))[0]["deleted_at"] is None
        # 첫 응답을 사용하지 않고 동일 키를 두 번째 기기에서 재전송한다.
        assert first.put(path, headers=a, json=body).status_code == 200
        replay = second.put(path, headers=b, json=body)
        assert replay.status_code == 200
        assert second.get(path, headers=b).json() == replay.json()
        assert db("SELECT count(*) AS n FROM server_deletion_requests WHERE paper_id=%s", (paper,))[0]["n"] == 1
        assert second.put(path + "-new", headers=b, json=body).status_code == 409
        assert second.get(path, headers={"Authorization": "Bearer other"}).status_code == 404
        with pytest.raises(psycopg.errors.CheckViolation, match="paper_tombstoned"):
            db(
                "INSERT INTO server_processing_versions(owner_user_id,paper_id,server_version_id) VALUES (%s,%s,'late')",
                (owner, paper),
            )
