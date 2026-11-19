"""실제 웹 라우트·DB를 제한 역할로 연결한다. 인증 제공자·객체 저장소만 대역이다."""

# ruff: noqa: F811
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_remote_processing_postgres import pool, db, DSN, new_id  # noqa: F401
from tests.fixtures.web_permissions import grant_web
from tests.integration.test_remote_annotations_postgres import context, stores  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="격리 PostgreSQL 필요")


@pytest.fixture(scope="module")
def web_pool(pool):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    role = "web_test_" + uuid4().hex[:12]
    with pool.connection() as conn:
        schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD 'local-web-test' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ).format(sql.Identifier(role))
        )
        conn.execute("CREATE TABLE server_schema_migrations(version text PRIMARY KEY, name text, checksum text)")
        from pathlib import Path
        from solo_leveling.interfaces.cli.server_migrations import discover_migrations

        for migration in discover_migrations(Path("migrations/server")):
            conn.execute(
                "INSERT INTO server_schema_migrations VALUES (%s,%s,%s)",
                (migration.version, migration.name, migration.checksum),
            )
        grant_web(conn, schema, role)
    runtime = ConnectionPool(
        make_conninfo(DSN, user=role, password="local-web-test"),
        kwargs=dict(row_factory=dict_row, options=f"-c search_path={schema}"),
        open=True,
    )
    try:
        yield runtime
    finally:
        runtime.close()
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.fixture
def web(web_pool, monkeypatch):
    from solo_leveling.interfaces.http import create_app
    from solo_leveling.interfaces.http.devices import PostgresDeviceStore
    from solo_leveling.interfaces.http.papers import PostgresPaperStore
    from solo_leveling.interfaces.http.collections import PostgresCollectionStore
    from solo_leveling.interfaces.http.resume_requests import PostgresResumeRequestStore
    from solo_leveling.interfaces.http.file_uploads import PostgresFileUploadStore
    from solo_leveling.interfaces.http.remote_uploads import PostgresRemoteUploadIntentStore
    from solo_leveling.interfaces.http.remote_upload_consents import PostgresRemoteUploadConsentStore
    from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake
    from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore
    from solo_leveling.interfaces.http.web_auth import PostgresWebSessionStore, WebIdentity, OidcWebAuthFlow
    from tests.unit.test_http_web_auth import SETTINGS, Http, Verifier
    from solo_leveling.interfaces.http.object_storage import StoredObject
    from types import SimpleNamespace as NS
    import requests

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("외부 HTTP 금지"))

    class Objects:
        def prepare_upload(self, pending, **kwargs):
            return NS(
                url="https://objects.invalid/put", method="PUT", headers={}, expires_at=datetime.now(timezone.utc)
            )

        def inspect_upload(self, pending):
            return StoredObject(pending.object_key, pending.file_size, "application/pdf", pending.sha256_hex, True)

        def finalize_upload(self, pending):
            return StoredObject(
                pending.object_key.replace("private/tmp/", "private/files/"),
                pending.file_size,
                "application/pdf",
                pending.sha256_hex,
                True,
            )

        def delete_upload(self, pending):
            pass

        def prepare_download(self, file):
            return NS(url="https://objects.invalid/get", expires_at=datetime.now(timezone.utc))

    sessions = PostgresWebSessionStore(web_pool)
    owner = "user-1"
    session = sessions.create_session(WebIdentity(owner, "합성", None), datetime.now(timezone.utc))
    app = create_app(
        device_store=PostgresDeviceStore(web_pool),
        paper_store=PostgresPaperStore(web_pool),
        collection_store=PostgresCollectionStore(web_pool),
        resume_store=PostgresResumeRequestStore(web_pool),
        file_upload_store=PostgresFileUploadStore(web_pool),
        remote_upload_store=PostgresRemoteUploadIntentStore(web_pool),
        remote_consent_store=PostgresRemoteUploadConsentStore(web_pool),
        remote_processing_intake=PostgresRemoteProcessingIntake(web_pool),
        deletion_store=PostgresRemoteDeletionStore(web_pool),
        web_session_store=sessions,
        web_auth_flow=OidcWebAuthFlow(SETTINGS, sessions, Verifier(), Http()),
        object_storage=Objects(),
    )
    with TestClient(app, base_url="https://app.example.com") as client:
        client.cookies.set("slp_session", session.token)
        client.cookies.set("csrf_token", session.csrf_token)
        client.headers["X-CSRF-Token"] = session.csrf_token
        client.headers["Origin"] = "https://app.example.com"
        yield client, sessions, owner


def call(client, method, path, body=None, expected=200):
    response = client.request(method, path, json=body, headers={"Idempotency-Key": new_id()})
    assert response.status_code == expected, (path, response.status_code, response.text)
    return response.json() if response.content else None


def test_web_login_papers_learning_annotations_collections_resume_devices(web, web_pool):
    client, sessions, owner = web
    call(client, "GET", "/health")
    call(client, "GET", "/v1/session")
    source = new_id()
    now = datetime.now(timezone.utc)
    # 동기화 입력은 같은 제한 DB 역할의 제품 저장소로 넣고 웹에서 조회한다.
    from solo_leveling.interfaces.http.papers import PostgresPaperStore, PaperMetadata, LearningRecordMetadata

    papers = PostgresPaperStore(web_pool)
    paper = papers.register(
        owner, new_id(), PaperMetadata(source, new_id(), "합성 논문", ("테스터",), 2026, datetime.now(timezone.utc))
    ).paper_id
    call(client, "GET", "/v1/papers")
    call(client, "GET", f"/v1/papers/{paper}")
    local_record = new_id()
    record = papers.upsert_learning_record(
        owner,
        paper,
        LearningRecordMetadata(source, local_record, "v1", "synthetic.pdf", {"pdf_page": 1}, now, "reading", now),
    )
    from solo_leveling.interfaces.http.papers import SummaryMetadata, AnnotationMetadata

    papers.upsert_summary(
        owner,
        paper,
        SummaryMetadata(
            source,
            local_record,
            new_id(),
            "paper",
            "user",
            ({"kind": "claim", "text": "합성 요약", "evidence_ids": ["e1"]},),
            now,
            now,
            "v1",
        ),
    )
    for kind in ("note", "highlight"):
        annotation = papers.upsert_annotation(
            owner,
            paper,
            AnnotationMetadata(
                source,
                local_record,
                new_id(),
                "v1",
                kind,
                "합성 메모",
                None,
                (
                    {
                        "chunk_id": "c1",
                        "parse_revision_id": "p1",
                        "target": "original",
                        "start_offset": 0,
                        "end_offset": 4,
                        "quote_snapshot": "Test",
                    },
                ),
                now,
                now,
                "user",
            ),
        )
        call(client, "GET", f"/v1/annotations/{annotation.annotation_id}")
    call(client, "GET", "/v1/annotations")
    from tests.integration.test_reading_state_postgres import request

    papers.put_reading_state(owner, paper, request(source))
    assert papers.get_reading_state(owner, paper, source).status == "completed"
    detail = call(client, "GET", f"/v1/papers/{paper}")
    assert "합성 요약" in str(detail)
    call(
        client,
        "POST",
        "/v1/resume-requests",
        dict(paper_id=paper, version_id="v1", learning_record_id=record.learning_record_id),
    )
    from solo_leveling.interfaces.http.file_uploads import PostgresFileUploadStore

    files = PostgresFileUploadStore(web_pool)
    reservation = files.reserve(owner, paper, "v1", "v1", "synthetic.pdf", 10, "application/pdf", "a" * 64)
    files.mark_ready(owner, reservation.upload_id, "private/files/synthetic.pdf")
    assert call(client, "GET", f"/v1/papers/{paper}/versions/v1/file-access")["status"] == "ready"
    call(client, "GET", f"/v1/papers/{paper}/versions/v1/upload-status", expected=403)
    device = call(client, "POST", "/v1/devices", {"display_name": "합성 기기"}, 201)["device_id"]
    call(client, "GET", "/v1/devices")
    call(client, "POST", f"/v1/devices/{device}/credentials")
    call(client, "DELETE", f"/v1/devices/{device}", expected=204)
    collection = call(client, "POST", "/v1/collections", {"name": new_id()}, 201)["collection_id"]
    call(client, "GET", "/v1/collections")
    call(client, "GET", f"/v1/collections/{collection}")
    call(client, "PATCH", f"/v1/collections/{collection}", {"name": new_id()})
    call(client, "PUT", f"/v1/collections/{collection}/papers/{paper}")
    call(client, "GET", f"/v1/collections/{collection}/papers")
    call(client, "DELETE", f"/v1/collections/{collection}/papers/{paper}")
    call(client, "DELETE", f"/v1/collections/{collection}")
    # 세션을 새로 만든 다른 사용자에게는 논문이 보이지 않는다.
    from solo_leveling.interfaces.http.web_auth import WebIdentity

    other = sessions.create_session(WebIdentity(new_id(), "다른 합성", None), datetime.now(timezone.utc))
    client.cookies.set("slp_session", other.token)
    call(client, "GET", f"/v1/papers/{paper}", expected=404)


def test_web_remote_annotation_is_owner_scoped_with_restricted_role(
    web, web_pool, pool, context,
):
    from solo_leveling.interfaces.http.remote_annotations import PostgresRemoteAnnotationStore
    from solo_leveling.interfaces.http.web_auth import WebIdentity
    from tests.integration.test_remote_annotations_postgres import annotation

    client, sessions, other_owner = web
    owner, context_id, _ = context
    assert owner != other_owner
    remote = PostgresRemoteAnnotationStore(pool).create(
        owner, "paper-detail", context_id, "web-library", annotation(context),
    ).annotation
    path = f"/v1/annotations/{remote.annotation_id}"
    assert all(
        item["annotation_id"] != remote.annotation_id
        for item in call(client, "GET", "/v1/annotations")["items"]
    )
    call(client, "GET", path, expected=404)

    session = sessions.create_session(WebIdentity(owner, "합성 소유자", None), datetime.now(timezone.utc))
    client.cookies.set("slp_session", session.token)
    detail = call(client, "GET", path)
    assert detail["context_id"] == context_id
    assert detail["storage_origin"] == "remote"
    assert detail in call(client, "GET", "/v1/annotations")["items"]

    with web_pool.connection() as conn:
        for table in (
            "server_remote_learning_records", "server_context_annotations", "server_execution_contexts",
        ):
            privileges = conn.execute(
                "SELECT has_table_privilege(current_user, %s, 'SELECT') AS readable, "
                "has_table_privilege(current_user, %s, 'INSERT, UPDATE, DELETE') AS writable",
                (table, table),
            ).fetchone()
            assert privileges == {"readable": True, "writable": False}


def test_web_remote_upload_consent_admission_deletion(web, db):
    client, _, _ = web
    from solo_leveling.interfaces.http.remote_upload_consents import NOTICE_VERSION

    db(
        "INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled,intake_enabled) VALUES ('provider_dispatch',1,true,true) ON CONFLICT(control_id) DO UPDATE SET intake_enabled=true,dispatch_enabled=true"
    )
    intent = call(
        client,
        "POST",
        "/v1/remote-uploads/intents",
        dict(file_name="synthetic.pdf", file_size=10, content_type="application/pdf", sha256="a" * 64),
        201,
    )
    path = "/v1/remote-uploads/intents/" + intent["intent_id"]
    call(client, "GET", path)
    call(client, "GET", path + "/consents")
    storage = call(client, "POST", path + "/consents/storage", {"notice_version": NOTICE_VERSION})
    call(client, "POST", path + "/prepare")
    call(client, "POST", path + "/complete")
    access = call(client, "GET", f"/v1/papers/{intent['paper_id']}/versions/{intent['version_id']}/file-access")
    assert access["status"] == "ready" and access["file_name"] == "synthetic.pdf"
    detail = call(client, "GET", f"/v1/papers/{intent['paper_id']}")
    assert detail["file_access"]["available"] and detail["selected_learning_record_id"] is None
    call(client, "GET", f"/v1/papers/{intent['paper_id']}/versions/{intent['version_id']}/upload-status", expected=403)
    ai = call(client, "POST", path + "/consents/ai_provider", {"notice_version": NOTICE_VERSION})
    call(
        client,
        "POST",
        path + "/processing",
        dict(storage_consent_id=storage["consent_id"], ai_consent_id=ai["consent_id"]),
    )
    call(client, "GET", path + "/processing")
    call(client, "POST", path + "/consents/ai_provider/revoke", {"expected_revision": ai["revision"]})
    deletion = f"/v1/papers/{intent['paper_id']}/deletion-requests/{new_id()}"
    call(client, "PUT", deletion, {"confirmed": True, "notice_version": "server-paper-deletion-v1"})
    call(client, "GET", deletion)


def test_web_role_cannot_change_worker_outputs_budget_or_schema(web_pool):
    import psycopg

    with web_pool.connection() as conn:
        for statement in (
            "CREATE TABLE forbidden(id int)",
            "UPDATE server_dispatch_budget_limits SET max_input_tokens=1",
            "DELETE FROM server_processing_result_staging",
            "DELETE FROM server_schema_migrations",
            "UPDATE server_processing_versions SET active_publication_id=NULL",
            "UPDATE server_dispatch_controls SET dispatch_enabled=true",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(statement)


def test_real_session_login_callback_logout_and_csrf(web):
    from urllib.parse import urlsplit, parse_qs

    client, _, _ = web
    client.cookies.clear()
    response = client.get("/v1/auth/login", params={"return_to": "https://app.example.com/"}, follow_redirects=False)
    assert response.status_code == 302
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    response = client.get("/v1/auth/callback", params={"state": state, "code": "synthetic"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    call(client, "GET", "/v1/me")
    client.headers["X-CSRF-Token"] = "wrong"
    call(client, "POST", "/v1/auth/logout", expected=403)
    client.headers["X-CSRF-Token"] = client.cookies.get("csrf_token")
    call(client, "POST", "/v1/auth/logout", expected=204)
    call(client, "GET", "/v1/me", expected=401)


def test_web_can_only_check_migration_history(web_pool):
    import psycopg
    from psycopg.conninfo import make_conninfo
    from pathlib import Path
    from solo_leveling.interfaces.cli.server_migrations import migrate, discover_migrations

    with web_pool.connection() as conn:
        identity = conn.execute("SELECT current_user AS role,current_schema() AS schema").fetchone()
    dsn = make_conninfo(
        DSN, user=identity["role"], password="local-web-test", options="-c search_path=" + identity["schema"]
    )
    migrations = discover_migrations(Path("migrations/server"))
    assert not migrate(dsn, migrations, check=True).pending
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrate(dsn, migrations)
