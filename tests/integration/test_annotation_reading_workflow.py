"""합성 데이터로 MCP→SQLite→동의/outbox→HTTP→웹 API 계약을 연결한다.

MCP 전송·SQLite·FastAPI는 실제 구현이며 인증/서버 저장소는 대역이다.
Claude Desktop 렌더링·PostgreSQL·배포 서버 검증을 대신하지 않는다.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid5, NAMESPACE_URL

import pytest
import requests
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.application.reading_state import ReadingStateService
from solo_leveling.application.server_sync.paper_metadata import PaperMetadataSyncService, SERVER_SYNC_NOTICE_VERSION
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.auth import AuthenticatedPrincipal
from solo_leveling.interfaces.http.papers import (
    PaperRegistration, LearningRecordRegistration, AnnotationRegistration, StoredAnnotation,
    PaperNotFound, PaperDetail, PaperListItem,
)
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_learning_persistence import setup_db, publish


class MemoryServerStore:
    """서버 저장 포트 대역. 소유 계정과 전송 출처가 다른 요청은 거부한다."""
    paper_id = str(uuid5(NAMESPACE_URL, 'issue375/paper'))
    record_id = str(uuid5(NAMESPACE_URL, 'issue375/context'))

    def __init__(self):
        self.owner = None
        self.source = None
        self.learning = None
        self.annotations = {}

    def register(self, owner, key, metadata):
        self.owner, self.source = owner, metadata.source_instance_id
        return PaperRegistration(self.paper_id, 1, 'stored', datetime.now(timezone.utc))

    def owned(self, owner, paper_id, source=None):
        if owner != self.owner or paper_id != self.paper_id or (source is not None and source != self.source):
            raise PaperNotFound()

    def upsert_learning_record(self, owner, paper_id, metadata):
        self.owned(owner, paper_id, metadata.source_instance_id)
        self.learning = metadata
        return LearningRecordRegistration(self.record_id, 1, 'stored', datetime.now(timezone.utc))

    def upsert_annotation(self, owner, paper_id, metadata):
        self.owned(owner, paper_id, metadata.source_instance_id)
        if self.learning is None or metadata.local_learning_record_id != self.learning.local_learning_record_id:
            raise PaperNotFound()
        key = str(uuid5(NAMESPACE_URL, metadata.local_annotation_id))
        self.annotations[key] = StoredAnnotation(key, paper_id, '합성 논문', self.record_id,
            metadata.selected_version_id, metadata.kind, metadata.body, metadata.step, metadata.segments,
            metadata.original_created_at, metadata.source_updated_at, metadata.creation_origin or 'unknown')
        return AnnotationRegistration(key, 1, 'stored', datetime.now(timezone.utc))

    def get_annotation(self, owner, key):
        self.owned(owner, self.paper_id)
        if key not in self.annotations:
            raise PaperNotFound()
        return self.annotations[key]

    def get_paper(self, owner, paper_id, **selection):
        self.owned(owner, paper_id)
        return PaperDetail(PaperListItem(paper_id, '합성 논문', None, None,
            selected_learning_record_id=self.record_id, selected_version_id=self.learning.selected_version_id,
            reading_status=self.learning.reading_status or 'unknown'))


class HttpTransport:
    """outbox가 만든 요청을 실제 FastAPI 라우트와 Pydantic 검증으로 전달한다."""
    def __init__(self, client, fail_annotations=False):
        self.client = client
        self.calls = []
        self.fail_annotations = fail_annotations

    def connection_id(self):
        return 'issue375-test-account'

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        assert kwargs.pop('expected_connection_id') == self.connection_id()
        if self.fail_annotations and '/annotations/' in path:
            raise requests.ConnectionError('합성 전송 실패')
        kwargs.pop('timeout', None)
        kwargs['headers'] = {**kwargs.get('headers', {}), 'Authorization':'Bearer device'}
        return self.client.request(method, path, **kwargs)


@pytest.mark.parametrize('target', ['original', 'translation'])
def test_mcp_to_web_preserves_annotation_and_explicit_state(setup_db, target):
    db, chunks = setup_db
    context = publish(db, chunks)
    annotations = AnnotationService(db)
    reading = ReadingStateService(db)
    contexts = Mock()
    contexts.get_or_create_learning_context.return_value = context
    store = MemoryServerStore()
    verifier = SimpleNamespace(verify=lambda token: AuthenticatedPrincipal(
        'other' if token == 'other' else 'owner', 'device' if token == 'device' else 'web_session',
        str(uuid5(NAMESPACE_URL, 'device')) if token == 'device' else None))
    app = create_app(token_verifier=verifier, device_registry=SimpleNamespace(is_active=lambda *_:True), paper_store=store)
    request = {'context_id':context.context_id, 'kind':'highlight','body':'합성 검증 메모',
        'creation_origin':'user','request_key':'issue375-retry','segments':[{
            'chunk_id':'c1','target':target,'start_offset':0,'end_offset':5 if target == 'original' else 3,
            'quote_snapshot':'First' if target == 'original' else '첫째.',
            **({'translation_revision_id':'t1'} if target == 'translation' else {}),
        }]}

    def server():
        return create_server(MCPServices(Mock(), Mock(), annotations=annotations, contexts=contexts, reading_state=reading))

    async def create_and_restart():
        async with create_connected_server_and_client_session(server()) as client:
            started = await client.call_tool('start_learning', {'version_id':'v1'})
            assert not started.isError
            first = await client.call_tool('create_annotation', {'request':request})
            assert not first.isError
        async with create_connected_server_and_client_session(server()) as client:
            retry = await client.call_tool('create_annotation', {'request':request})
            assert retry.structuredContent['reused']
            assert retry.structuredContent['annotation'] == first.structuredContent['annotation']
            state = reading.get('paper')
            done = await client.call_tool('set_reading_completed',
                {'paper_id':'paper','completed':True,'expected_revision':state.revision})
            assert not done.isError
            await client.call_tool('start_learning', {'version_id':'v1'})
            assert reading.get('paper').status == 'completed'
        return first.structuredContent['annotation']

    record = asyncio.run(create_and_restart())
    assert len(annotations.list(context.context_id).items) == 1
    with TestClient(app) as http:
        transport = HttpTransport(http)
        sync = PaperMetadataSyncService(db, transport)
        preview = sync.preview('paper')
        assert transport.calls == [] and store.annotations == {}
        operation = sync.grant_and_queue('paper', SERVER_SYNC_NOTICE_VERSION, preview=preview)
        assert transport.calls == []
        assert sync.sync(operation.operation_id).status == 'synced'
        assert len(store.annotations) == 1
        key = next(iter(store.annotations))
        response = http.get('/v1/annotations/' + key, headers={'Authorization':'Bearer web'})
        assert response.status_code == 200
        public = response.json()
        assert public['body'] == record['body'] and public['creation_origin'] == 'user'
        assert public['segments'][0]['target'] == target
        assert public['segments'][0]['quote'] == request['segments'][0]['quote_snapshot']
        assert datetime.fromisoformat(public['created_at'].replace('Z','+00:00')) == datetime.fromisoformat(record['created_at'])
        page = http.get('/v1/papers/' + store.paper_id, headers={'Authorization':'Bearer web'})
        assert page.status_code == 200 and page.json()['reading_status'] == 'completed'
        denied = http.get('/v1/annotations/' + key, headers={'Authorization':'Bearer other'})
        assert denied.status_code == 404
        state = reading.get('paper')
        reading.set_completed('paper', False, state.revision)
        again = sync.grant_and_queue('paper', SERVER_SYNC_NOTICE_VERSION, preview=sync.preview('paper'))
        assert sync.sync(again.operation_id).status == 'synced'
        assert http.get('/v1/papers/' + store.paper_id, headers={'Authorization':'Bearer web'}).json()['reading_status'] == 'reading'


def test_transport_failure_keeps_local_annotation(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    annotations = AnnotationService(db)
    saved = annotations.create(context.context_id, 'note', '합성 메모', request_key='failure').annotation
    store = MemoryServerStore()
    principal = AuthenticatedPrincipal('owner','device',str(uuid5(NAMESPACE_URL,'device')))
    app = create_app(token_verifier=SimpleNamespace(verify=lambda _:principal),
        device_registry=SimpleNamespace(is_active=lambda *_:True), paper_store=store)
    with TestClient(app) as http:
        sync = PaperMetadataSyncService(db, HttpTransport(http, fail_annotations=True))
        operation = sync.grant_and_queue('paper', SERVER_SYNC_NOTICE_VERSION, preview=sync.preview('paper'))
        sync.sync(operation.operation_id)
    assert annotations.get(context.context_id, saved.annotation_id).annotation == saved
    assert store.annotations == {}
