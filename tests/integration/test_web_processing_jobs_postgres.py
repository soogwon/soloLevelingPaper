"""웹 처리 조회의 계정·판본 격리와 MCP 상태 일치를 검증한다."""
# ruff: noqa: F811
import os
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.integration.test_remote_processing_postgres import pool, db, stores, make_paper, HASH  # noqa: F401
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.remote_job_routes import create_web_job_router
from solo_leveling.interfaces.http.remote_job_queries import RemoteJobQueries

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='로컬 PostgreSQL 필요')


def test_web_owner_version_pagination_and_cancel_state(db, stores, pool):
    owner = 'webjobs-' + uuid4().hex
    paper = make_paper(db, owner)
    jobs = [stores[0].enqueue(owner, paper, 'v1', 'parse', str(uuid4()), HASH) for _ in range(3)]
    db('INSERT INTO server_processing_versions(owner_user_id,paper_id,server_version_id) VALUES (%s,%s,%s)', (owner, paper, 'v2'))
    stores[0].enqueue(owner, paper, 'v2', 'parse', str(uuid4()), HASH)
    principal = AuthenticatedPrincipal(owner, 'web_session')
    app = FastAPI()
    app.include_router(create_web_job_router(lambda: principal, pool))
    path = f'/v1/papers/{paper}/processing-jobs'
    with TestClient(app) as client:
        response = client.get(path, params={'version_id': 'v1', 'limit': 2})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        first = response.json()
        second = client.get(path, params={'version_id': 'v1', 'limit': 2, 'after': first['next_after']}).json()
        assert {j['job_id'] for j in first['jobs'] + second['jobs']} == {j.job_id for j in jobs}
        stores[0].cancel(owner, jobs[0].job_id)
        listed = client.get(path, params={'version_id': 'v1'}).json()['jobs']
        actual = next(j for j in listed if j['job_id'] == jobs[0].job_id)
        assert actual == RemoteJobQueries(pool, lambda: owner).get(jobs[0].job_id)['result']
        assert actual['status'] == 'cancelled' and actual['revision'] == 2
        assert all(j['attempt'] == 0 for j in listed)
        assert client.get(path, params={'version_id': 'missing'}).status_code == 404
        principal = AuthenticatedPrincipal('other', 'web_session')
        assert client.get(path, params={'version_id': 'v1'}).status_code == 404
        principal = AuthenticatedPrincipal(owner, 'device', 'device')
        assert client.get(path, params={'version_id': 'v1'}).status_code == 403
        principal = AuthenticatedPrincipal(owner, 'web_session')
        db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s', (paper,))
        assert client.get(path, params={'version_id': 'v1'}).status_code == 404
