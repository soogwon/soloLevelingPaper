"""논문 상태 CAS와 승인 시점 고정 전송을 검증한다. 실제 PostgreSQL 검증과 구분한다."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import sqlite3

import pytest
import requests
from fastapi.testclient import TestClient

from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.papers import PostgresPaperStore, PaperNotFound
from solo_leveling.interfaces.http.reading_states import ReadingStateConflict
from solo_leveling.application.reading_state import ReadingStateService
from solo_leveling.application.server_sync.paper_metadata import PaperMetadataSyncService, SERVER_SYNC_NOTICE_VERSION, SyncPreviewChanged
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_paper_metadata_sync import database, Response, SERVER_PAPER_ID, NOW
from tests.unit.test_learning_persistence import setup_db

SOURCE = str(uuid4())


class SqlPool:
    """PostgreSQL 저장 로직의 SQL을 SQLite 트랜잭션 대역에서 실행한다. 운영 이관 검증은 아니다."""
    def __init__(self, path):
        self.path = str(path)
        conn = sqlite3.connect(self.path)
        conn.executescript('''CREATE TABLE server_papers(paper_id TEXT PRIMARY KEY,owner_user_id TEXT,source_instance_id TEXT);
            CREATE TABLE server_paper_reading_states(paper_id TEXT PRIMARY KEY,status TEXT,basis_version_id TEXT,
            revision INTEGER,source_revision INTEGER,action TEXT,operation_id TEXT,request_hash TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP);''')
        conn.execute('INSERT INTO server_papers VALUES (?,?,?)', (SERVER_PAPER_ID, 'owner', SOURCE))
        conn.commit()
        conn.close()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        def row_factory(cursor, values):
            row = dict(zip([c[0] for c in cursor.description], values))
            if row.get('updated_at'):
                row['updated_at'] = datetime.fromisoformat(row['updated_at']).replace(tzinfo=timezone.utc)
            return row
        conn.row_factory = row_factory
        conn.execute('BEGIN IMMEDIATE')
        class Adapter:
            def execute(self, sql, args=()):
                return conn.execute(sql.replace('%s','?').replace(' FOR UPDATE',''), args)
        try:
            yield Adapter()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


@pytest.fixture
def store(tmp_path):
    return PostgresPaperStore(SqlPool(tmp_path/'server.sqlite'))


def body(status='completed', action='complete', source_revision=2, expected_revision=0):
    return dict(source_instance_id=SOURCE, operation_id=str(uuid4()), status=status, action=action,
                source_revision=source_revision, expected_revision=expected_revision, basis_version_id='v1')


def test_cas_replay_stale_timestamp_and_explicit_cancel(store):
    request = body()
    first = store.put_reading_state('owner', SERVER_PAPER_ID, request)
    assert first.revision == 1 and first.basis_version_id == 'v1'
    assert store.put_reading_state('owner', SERVER_PAPER_ID, request) == first
    for change in [dict(status='reading'), dict(basis_version_id='v2')]:
        with pytest.raises(ReadingStateConflict):
            store.put_reading_state('owner', SERVER_PAPER_ID, request | change)
    for stale in [body('reading','start',3,0), body('reading','start',3,1), body('reading','reopen',1,1)]:
        with pytest.raises(ReadingStateConflict):
            store.put_reading_state('owner', SERVER_PAPER_ID, stale)
    cancelled = store.put_reading_state('owner', SERVER_PAPER_ID, body('reading','reopen',3,1))
    assert cancelled.revision == 2 and cancelled.status == 'reading'
    with pytest.raises(ReadingStateConflict):
        store.put_reading_state('owner', SERVER_PAPER_ID, request)


def test_simultaneous_same_base_only_one_wins(store):
    from concurrent.futures import ThreadPoolExecutor
    def write(_):
        try:
            return store.put_reading_state('owner', SERVER_PAPER_ID, body())
        except ReadingStateConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert sum(r is not None for r in results) == 1


def test_owner_and_registered_source_checked_before_replay(store):
    request = body()
    store.put_reading_state('owner', SERVER_PAPER_ID, request)
    with pytest.raises(PaperNotFound):
        store.put_reading_state('other', SERVER_PAPER_ID, request)
    with pytest.raises(PaperNotFound):
        store.put_reading_state('owner', SERVER_PAPER_ID, request | {'source_instance_id':str(uuid4())})


def test_http_state_contract_and_safe_errors(store):
    verifier = SimpleNamespace(verify=lambda token: AuthenticatedPrincipal(
        'other' if token == 'other' else 'owner', 'web_session' if token == 'web' else 'device', None if token == 'web' else 'device'))
    app = create_app(token_verifier=verifier, device_registry=SimpleNamespace(is_active=lambda *_:True), paper_store=store)
    path = f'/v1/mcp/papers/{SERVER_PAPER_ID}/reading-state'
    with TestClient(app) as client:
        request = body()
        assert client.put(path, json=request).status_code == 401
        assert client.put(path, json=request, headers={'Authorization':'Bearer web'}).status_code == 403
        assert client.put(path, json=request, headers={'Authorization':'Bearer other'}).status_code == 404
        headers = {'Authorization':'Bearer device'}
        assert client.get(path, params={'source_instance_id':SOURCE}, headers=headers).json()['revision'] == 0
        assert client.put(path, json=request | {'basis_version_id':None}, headers=headers).status_code == 422
        assert client.put(path, json=request, headers=headers).status_code == 200
        assert client.put(path, json=body('reading','start',3,1), headers=headers).status_code == 409
        assert client.get(path, params={'source_instance_id':SOURCE}, headers=headers).json()['status'] == 'completed'


class SyncClient:
    def __init__(self):
        self.calls = []
        self.fail_once = False
        self.conflict = False
    def connection_id(self):
        return 'owner'
    def request(self, method, path, **kwargs):
        self.calls.append((method,path,kwargs))
        if not path.endswith('/reading-state'):
            return Response()
        if self.conflict:
            return Response(409,{})
        if self.fail_once:
            self.fail_once = False
            raise requests.ConnectionError()
        data = kwargs['json']
        return Response(200,dict(paper_id=SERVER_PAPER_ID,status=data['status'],basis_version_id=data['basis_version_id'],
            source_revision=data['source_revision'],revision=data['expected_revision']+1,updated_at=NOW.isoformat()))


def test_initial_state_without_context_and_loss_retry_same_id(database):
    client = SyncClient()
    service = PaperMetadataSyncService(database,client)
    preview = service.preview('paper-local-1')
    assert client.calls == [] and preview.learning_record_count == 0
    operation = service.grant_and_queue('paper-local-1',SERVER_SYNC_NOTICE_VERSION,preview=preview).operation_id
    client.fail_once = True
    assert service.sync(operation).status == 'retryable_failure'
    first = client.calls[-1]
    assert first[2]['json']['status'] == 'not_started'
    assert first[2]['json']['expected_revision'] == 0
    assert PaperMetadataSyncService(database,client).sync(operation).status == 'synced'
    assert client.calls[-1] == first
    with get_connection(database) as conn:
        assert conn.execute('SELECT count(*) FROM learning_contexts').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM server_sync_consents').fetchone()[0] == 1
        assert conn.execute('SELECT server_revision FROM server_object_mappings WHERE object_type=?', ('reading_state',)).fetchone()[0] == 1


def test_old_consent_does_not_gain_state_transfer(database):
    client = SyncClient()
    service = PaperMetadataSyncService(database,client)
    operation = service.grant_and_queue('paper-local-1','paper-learning-summary-annotation-sync-v4').operation_id
    assert service.sync(operation).status == 'synced'
    assert len(client.calls) == 1


@pytest.mark.parametrize('condition',['changed','revoked','conflict'])
def test_state_change_approval_and_conflict_never_auto_rebase(database,condition):
    client = SyncClient()
    service = PaperMetadataSyncService(database,client)
    operation = service.grant_and_queue('paper-local-1',SERVER_SYNC_NOTICE_VERSION).operation_id
    with get_connection(database) as conn:
        if condition == 'changed':
            conn.execute("UPDATE paper_reading_states SET revision=revision+1")
        if condition == 'revoked':
            conn.execute("UPDATE server_sync_consents SET revoked_at=?", (NOW.isoformat(),))
    client.conflict = condition == 'conflict'
    result = service.sync(operation)
    assert result.status == ('action_required' if condition == 'revoked' else 'conflict')
    before = len(client.calls)
    assert service.retry_due() == []
    assert len(client.calls) == before
    if condition == 'conflict':
        assert client.calls[-1][2]['json']['expected_revision'] == 0
    else:
        assert not any(c[1].endswith('/reading-state') for c in client.calls)


def test_preview_detects_state_change(database):
    service = PaperMetadataSyncService(database,SyncClient())
    preview = service.preview('paper-local-1')
    with get_connection(database) as conn:
        conn.execute('UPDATE paper_reading_states SET revision=2')
    with pytest.raises(SyncPreviewChanged):
        service.grant_and_queue('paper-local-1',SERVER_SYNC_NOTICE_VERSION,preview=preview)


def test_local_basis_survives_new_version_and_rejects_other_paper(setup_db):
    db,_ = setup_db
    service = ReadingStateService(db)
    state = service.start('v1')
    completed = service.set_completed('paper',True,state.revision)
    with get_connection(db) as conn:
        conn.execute("INSERT INTO paper_versions SELECT 'v-new',paper_id,'new-hash',stored_path,original_filename,created_at FROM paper_versions WHERE version_id='v1'")
    assert service.start('v-new') == completed
    assert completed.basis_version_id == 'v1'
    from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
    with pytest.raises(InvalidArgumentError):
        service.set_completed('paper',True,completed.revision,'missing')
    updated = service.set_completed('paper',True,completed.revision,'v-new')
    assert updated.basis_version_id == 'v-new' and updated.revision == completed.revision+1


def test_committed_response_loss_replays_without_second_revision(database, store):
    class LostResponseClient(SyncClient):
        def __init__(self):
            super().__init__()
            self.lost = False
        def request(self, method, path, **kwargs):
            if not path.endswith('/reading-state'):
                return super().request(method, path, **kwargs)
            self.calls.append((method, path, kwargs))
            from dataclasses import asdict
            data = kwargs['json']
            with store._pool.connection() as conn:
                conn.execute('UPDATE server_papers SET source_instance_id=%s', (data['source_instance_id'],))
            result = asdict(store.put_reading_state('owner', SERVER_PAPER_ID, data))
            if not self.lost:
                self.lost = True
                raise requests.ConnectionError('응답 유실')
            result['updated_at'] = result['updated_at'].isoformat()
            return Response(200, result)
    client = LostResponseClient()
    service = PaperMetadataSyncService(database, client)
    operation = service.grant_and_queue('paper-local-1', SERVER_SYNC_NOTICE_VERSION).operation_id
    assert service.sync(operation).status == 'retryable_failure'
    first = client.calls[-1]
    assert PaperMetadataSyncService(database, client).sync(operation).status == 'synced'
    assert client.calls[-1] == first
    assert store.get_reading_state('owner', SERVER_PAPER_ID, first[2]['json']['source_instance_id']).revision == 1
    next_operation = service.grant_and_queue('paper-local-1', SERVER_SYNC_NOTICE_VERSION).operation_id
    assert service.sync(next_operation).status == 'synced'
    assert client.calls[-1][2]['json']['expected_revision'] == 1


def test_unconfigured_state_store_is_503():
    verifier = SimpleNamespace(verify=lambda token: AuthenticatedPrincipal('owner', 'device', 'device'))
    app = create_app(token_verifier=verifier, device_registry=SimpleNamespace(is_active=lambda *_:True))
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer device'}
        path = f'/v1/mcp/papers/{SERVER_PAPER_ID}/reading-state'
        assert client.get(path, params={'source_instance_id':SOURCE}, headers=headers).status_code == 503
        assert client.put(path, json=body(), headers=headers).status_code == 503
