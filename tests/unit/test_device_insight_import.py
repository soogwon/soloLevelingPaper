"""저장된 SQLite 항목을 기기 카드에서 선택해 같은 요청 키로 제출한다."""
import sqlite3
from uuid import uuid4

import pytest
from mcp.server.fastmcp import FastMCP

from solo_leveling.application.server_sync.paper_insights import PaperInsightImportService
from solo_leveling.infrastructure.database import repository
from solo_leveling.interfaces.mcp.confirmations import ConfirmationStore
from solo_leveling.interfaces.mcp.insight_import_tools import register_insight_import
from tests.unit.test_learning_persistence import publish, setup_db  # noqa: F401
from tests.unit.test_paper_insights import item


class Response:
    status_code = 200

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def raise_for_status(self):
        pass

    def json(self):
        return self.value


class Client:
    server_url = 'https://example.invalid'

    def __init__(self):
        self.device = 'device-a'
        self.calls = []
        self.binding = {key: str(uuid4()) for key in
                        ('publication_id', 'parse_revision_id', 'translation_revision_id', 'embedding_set_id')}
        self.chunk = str(uuid4())

    def connection_id(self):
        return self.device

    def request(self, method, path, *, json, expected_connection_id):
        if self.device != expected_connection_id:
            raise RuntimeError('연결 변경')
        self.calls.append((method, path, json))
        if path.endswith('/map-sources'):
            return Response(dict(self.binding, section_id=None, server_version_id='v9',
                                 remote_chunk_ids=[self.chunk] * len(json['sources'])))
        return Response({'source_collection_id': json['source_collection_id'], 'revision': 1})


@pytest.mark.anyio
async def test_card_preview_submits_only_selected_and_reuses_key(setup_db, tmp_path):
    db, chunks = setup_db
    publish(db, chunks)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_versions SET file_hash=?", ('a' * 64,))
    repository.save_paper_insights(db, 'ctx', 'paper', [
        item(), item('important_sentence', 'Second.', chunk_id='c2', end=7, quote='Second.'),
    ])
    client = Client()
    card_db = tmp_path / 'confirm.sqlite3'
    chat = FastMCP('chat')
    card = FastMCP('card')
    for server in (chat, card):
        register_insight_import(server, PaperInsightImportService(db, client), ConfirmationStore(card_db))
    remote = str(uuid4())
    opened = await chat.call_tool('open_paper_insight_import', {'context_id': 'ctx', 'remote_context_id': remote})
    assert not client.calls
    assert 'First' not in str(opened.content)
    selection = opened.meta['selection']
    args = {'context_id': 'ctx', 'selection_token': opened.meta['selection_token'],
            'insight_ids': [selection['items'][1]['insight_id']]}
    reviewed = await card.call_tool('preview_paper_insight_import', args)
    assert reviewed.meta['review']['items'][0]['text'] == 'Second.'
    assert client.calls[0][1].endswith('/map-sources')
    assert set(client.calls[0][2]['sources'][0]) == {'original_sha256', 'translated_sha256', 'pdf_page'}
    assert 'Second.' not in str(client.calls)
    token = reviewed.meta['submission_token']
    client.device = 'device-b'
    failed = await card.call_tool('submit_paper_insight_import', {'context_id': 'ctx', 'submission_token': token})
    assert not failed.meta and len(client.calls) == 1
    client.device = 'device-a'
    for _ in range(2):
        result = await card.call_tool('submit_paper_insight_import', {'context_id': 'ctx', 'submission_token': token})
        assert result.meta['receipt'] == {'status': 'saved', 'revision': 1}
    assert client.calls[1] == client.calls[2]
    assert [i['text'] for i in client.calls[1][2]['items']] == ['Second.']
    reviewed_again = await card.call_tool('preview_paper_insight_import', args)
    cancelled = reviewed_again.meta['submission_token']
    await card.call_tool('cancel_paper_insight_import', {'submission_token': cancelled})
    await card.call_tool('submit_paper_insight_import', {'context_id': 'ctx', 'submission_token': cancelled})
    assert len(client.calls) == 4
