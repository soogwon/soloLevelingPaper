"""저장된 번역 리비전과 인용의 일치 및 MCP 왕복을 검증한다."""
from contextlib import closing
import asyncio
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.domain.annotation import SegmentInput
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.application.server_sync.paper_metadata import _annotation_payload
from mcp.shared.memory import create_connected_server_and_client_session
from tests.unit.test_annotations import env
from tests.unit.test_learning_persistence import setup_db
from tests.unit.mcp.test_annotations import server, call


def test_translation_snapshot_and_safe_rejections(env):
    db, ctx = env
    service = AnnotationService(db)
    def create(quote='첫째.', revision='t1'):
        return service.create(ctx, 'highlight', '', segments=[
            SegmentInput('c1', 0, len(quote), quote, 'translation', revision)])
    saved = create().annotation
    detail = service.get(ctx, saved.annotation_id, source_position=0)
    assert detail.source.quote == '첫째.' and detail.segment_available == (True,)
    assert create(revision='missing').code == 'TRANSLATION_REVISION_MISMATCH'
    assert create('다르.').code == 'QUOTE_MISMATCH'
    assert create('너무 긴 인용문').code == 'OFFSET_OUT_OF_RANGE'
    with closing(get_connection(db)) as conn, conn:
        payload = _annotation_payload(conn, 'paper', saved.annotation_id, 'source')
        assert payload['segments'][0]['target'] == 'translation'
        assert payload['segments'][0]['translation_revision_id'] == 't1'
        conn.execute("UPDATE translation_results SET translated_text=NULL, failure_code='provider_unavailable' "
                     "WHERE chunk_id='c1' AND translation_revision_id='t1'")
    assert create().code == 'TRANSLATION_NOT_AVAILABLE'
    unavailable = service.get(ctx, saved.annotation_id, source_position=0)
    assert unavailable.source is None and unavailable.segment_available == (False,)
    assert unavailable.annotation.segments[0].quote_snapshot == '첫째.'


def test_translation_mcp_roundtrip(env):
    db, ctx = env
    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            request = {'context_id':ctx,'kind':'highlight','body':'','request_key':'translation',
                'segments':[{'chunk_id':'c1','start_offset':0,'end_offset':3,'quote_snapshot':'첫째.',
                             'target':'translation','translation_revision_id':'t1'}]}
            saved = await call(client, 'create_annotation', request)
            retry = await call(client, 'create_annotation', request)
            assert retry['reused'] and retry['annotation'] == saved['annotation']
            detail = await call(client, 'get_annotation', {'context_id':ctx,
                'annotation_id':saved['annotation']['annotation_id'],'source_position':0})
            assert detail['source']['quote'] == '첫째.'
    asyncio.run(scenario())


def test_unicode_crlf_exact_offsets_and_replay_after_translation_loss(env):
    db, ctx = env
    service = AnnotationService(db)
    text = '😀e\u0301\r\n반복 반복 <b>인용</b>'
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE translation_results SET translated_text=? WHERE chunk_id='c1' AND translation_revision_id='t1'", (text,))
    start = text.rindex('반복')
    segment = SegmentInput('c1', start, start+2, '반복', 'translation', 't1')
    saved = service.create(ctx, 'highlight', '', segments=[segment], request_key='unicode').annotation
    assert saved.segments[0].start_offset == start
    assert service.create(ctx, 'highlight', '', segments=[SegmentInput(
        'c1', start+1, start+3, '반복', 'translation', 't1')]).code == 'QUOTE_MISMATCH'
    full = service.create(ctx, 'highlight', '', segments=[SegmentInput('c1', 0, len(text), text, 'translation', 't1')]).annotation
    assert full.segments[0].quote_snapshot == text
    with closing(get_connection(db)) as conn, conn:
        conn.execute("DELETE FROM translation_results WHERE chunk_id='c1' AND translation_revision_id='t1'")
    replay = service.create(ctx, 'highlight', '', segments=[segment], request_key='unicode')
    assert replay.reused and replay.annotation == saved
    detail = service.get(ctx, saved.annotation_id, source_position=0)
    assert detail.location_status == 'unavailable'
    assert detail.annotation.segments[0].quote_snapshot == '반복'
