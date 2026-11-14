"""명시적 읽기 상태와 메모·학습 기록의 독립성 및 MCP 공개 전이를 검증한다."""
import asyncio
from contextlib import closing
from unittest.mock import Mock
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from solo_leveling.application.reading_state import ReadingStateService, ReadingStateConflict
from solo_leveling.application.server_sync.paper_metadata import _learning_payload
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_learning_persistence import setup_db, publish


def test_transitions_persist_and_do_not_infer_completion(setup_db):
    db, chunks = setup_db
    ctx = publish(db, chunks)
    service = ReadingStateService(db)
    assert service.get('paper').status == 'not_started'
    started = service.start('v1')
    assert started.status == 'reading'
    done = service.set_completed('paper', True, started.revision)
    assert done.status == 'completed'
    assert service.start('v1') == done
    init_db(db)
    assert ReadingStateService(db).get('paper') == done
    with pytest.raises(ReadingStateConflict):
        service.set_completed('paper', False, started.revision)
    reading = service.set_completed('paper', False, done.revision)
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_contexts').fetchone()[0] == 1
        payload = _learning_payload(conn, 'paper', ctx.context_id, 'source')
    assert payload['reading_status'] == 'reading'
    assert payload['source_updated_at'] == reading.updated_at


def test_existing_database_is_unknown_not_inferred(setup_db):
    db, _ = setup_db
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DROP TRIGGER initialize_paper_reading_state')
        conn.execute('DROP TABLE paper_reading_states')
    init_db(db)
    assert ReadingStateService(db).get('paper').status == 'unknown'
    assert ReadingStateService(db).start('v1').status == 'reading'


def test_mcp_explicit_start_complete_and_conflict(setup_db):
    db, chunks = setup_db
    ctx = publish(db, chunks)
    contexts = Mock()
    contexts.get_or_create_learning_context.return_value = ctx
    service = ReadingStateService(db)
    server = create_server(MCPServices(Mock(), Mock(), contexts=contexts, reading_state=service))
    async def run():
        async with create_connected_server_and_client_session(server) as client:
            started = await client.call_tool('start_learning', {'version_id':'v1'})
            assert not started.isError
            state = service.get('paper')
            done = await client.call_tool('set_reading_completed',
                {'paper_id':'paper','completed':True,'expected_revision':state.revision})
            assert not done.isError and done.structuredContent['status'] == 'completed'
            await client.call_tool('start_learning', {'version_id':'v1'})
            assert service.get('paper').status == 'completed'
            stale = await client.call_tool('set_reading_completed',
                {'paper_id':'paper','completed':False,'expected_revision':state.revision})
            assert stale.isError and 'READING_STATE_CONFLICT' in stale.content[0].text
    asyncio.run(run())
