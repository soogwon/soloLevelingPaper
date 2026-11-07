"""상세 조회의 읽기 전용·범위·저장 리비전 연결을 검증한다."""
from contextlib import closing
from pathlib import Path
import sqlite3
import pytest
from tests.unit.test_learning_persistence import setup_db, publish
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.paper_summary import SummaryItem
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.retrieval.paper_detail import SQLitePaperDetailReader
from solo_leveling.application.paper_detail.service import PaperDetailService
from solo_leveling.application.evidence_qa.errors import DataIntegrityError, InvalidArgumentError, ResourceNotFoundError
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.domain.annotation import SegmentInput
from solo_leveling.domain.models import LearningContext

@pytest.fixture
def detail(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE chunks SET section_id='s1' WHERE chunk_id='c1'")
        conn.execute("UPDATE chunks SET section_id='s2' WHERE chunk_id='c2'")
    repo.save_evidences(db, 'ctx', [Evidence('e1','c1','첫째.','First.'), Evidence('e2','c2','둘째.','Second.')])
    return db, PaperDetailService(SQLitePaperDetailReader(db))

def save(db, scope='paper'):
    return repo.save_summary(db,'ctx',scope,[SummaryItem('claim','첫째 주장',('e1',)),SummaryItem('result','둘째 결과',('e2',))])

def test_missing_summary_does_not_generate_or_write(detail):
    db, service = detail
    before=Path(db).read_bytes()
    result=service.get('ctx')
    assert result['summary'] is None and result['evidence']==[]
    assert 'summary_not_available' in result['limitations']
    assert result['section_ids']==['s1','s2']
    assert result['file_display_name']=='paper.pdf'
    assert Path(db).read_bytes()==before

def test_pagination_preserves_summary_and_quotes_without_writes(detail):
    db, service=detail
    saved=save(db)
    before=Path(db).read_bytes()
    first=service.get('ctx',limit=1)
    assert first['summary']['next_offset']==1
    assert first['summary']['verification_level']=='structural_only'
    assert [e['evidence_id'] for e in first['evidence']]==['e1']
    second=service.get('ctx',limit=1,offset=1,summary_id=saved.summary_id)
    assert second['summary']['next_offset'] is None
    assert second['evidence'][0]['pdf_page']==2
    assert second['evidence'][0]['section_id']=='s2'
    assert second['evidence'][0]['quote_original']=='Second.'
    assert Path(db).read_bytes()==before
    save(db)
    with pytest.raises(InvalidArgumentError):
        service.get('ctx',offset=1,summary_id=saved.summary_id)

@pytest.mark.parametrize('args',[{'limit':True},{'limit':0},{'limit':51},{'offset':-1},{'offset':True},{'offset':1},{'scope':''}])
def test_invalid_arguments(detail,args):
    with pytest.raises(InvalidArgumentError): detail[1].get('ctx',**args)

def test_missing_context_section_and_invalid_offset(detail):
    db, service=detail
    for ctx,scope in [('missing','paper'),('ctx','missing')]:
        with pytest.raises(ResourceNotFoundError): service.get(ctx,scope=scope)
    saved=save(db)
    with pytest.raises(InvalidArgumentError): service.get('ctx',offset=3,summary_id=saved.summary_id)

def test_section_scope_does_not_leak_other_section(detail):
    db,service=detail
    repo.save_summary(db,'ctx','s1',[SummaryItem('claim','첫째',('e1',))])
    assert service.get('ctx',scope='s1')['evidence'][0]['section_id']=='s1'
    save(db,'s1')
    with pytest.raises(DataIntegrityError): service.get('ctx',scope='s1')

@pytest.mark.parametrize('sql',[
    "UPDATE evidences SET quote_original='다른 원문' WHERE evidence_id='e1'",
    "UPDATE evidences SET quote_ko='다른 번역' WHERE evidence_id='e1'",
    "UPDATE translation_results SET translation_revision_id='wrong' WHERE chunk_id='c1'",
    "UPDATE learning_contexts SET embedding_set_id='wrong'",
    "UPDATE evidences SET context_id='other' WHERE evidence_id='e1'",
    "UPDATE summary_items SET evidence_ids='[\"missing\"]' WHERE position=0",
])
def test_invalid_provenance_or_quote_fails_closed(detail,sql):
    db,service=detail;save(db)
    with closing(sqlite3.connect(db)) as conn,conn: conn.execute(sql)
    with pytest.raises(DataIntegrityError):service.get('ctx')

def test_missing_database_not_created(tmp_path):
    path=tmp_path/'absent.sqlite'
    with pytest.raises(sqlite3.OperationalError):PaperDetailService(SQLitePaperDetailReader(str(path))).get('ctx')
    assert not path.exists()


def test_saved_summary_through_mcp_has_nested_contract(detail):
    import asyncio
    import json
    from unittest.mock import Mock
    from solo_leveling.interfaces.mcp.server import MCPServices, create_server
    db, service=detail
    save(db)
    server=create_server(MCPServices(Mock(),Mock(),detail=service))
    content, data=asyncio.run(server.call_tool('get_paper_detail',{'context_id':'ctx','limit':1}))
    assert json.loads(content[0].text)==data
    assert data['summary']['items'][0]['evidence_ids']==['e1']
    assert data['evidence'][0]['section_id']=='s1'
    assert data['evidence'][0]['quote_original']=='First.'


def test_annotations_follow_context_and_exact_original_range(detail):
    from solo_leveling.domain.annotation import SegmentInput
    from solo_leveling.domain.models import LearningContext
    from solo_leveling.infrastructure.database.annotations import create_annotation

    db, service = detail
    save(db)
    repo.create_learning_context(db, LearningContext('other', 'v1', 'p1', 't1', embedding_set_id='idx'))
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='😀 First.' WHERE chunk_id='c1'")
    segment = SegmentInput('c1', 2, 8, 'First.')
    for context, kind, body in [('ctx', 'note', '메모'), ('ctx', 'highlight', ''), ('other', 'note', '다른 맥락')]:
        create_annotation(db, context, kind, body, [segment])
    first, second = service.get('ctx')['evidence']
    assert first['annotation_start'] == 2
    assert first['annotation_count'] == 2
    rest = service.annotation_page('ctx', 'e1', first['annotation_cursor'])
    assert {(item['kind'], item['body']) for item in first['annotations'] + rest['annotations']} == {('note', '메모'), ('highlight', '')}
    assert second['annotations'] == []
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='😀 First. First.' WHERE chunk_id='c1'")
    ambiguous = service.get('ctx')['evidence'][0]
    assert ambiguous['annotation_start'] is None
    assert ambiguous['annotations'] == []


def test_many_long_notes_keep_detail_small_and_pages_complete(detail):
    import json
    db, service = detail
    save(db)
    with sqlite3.connect(db) as conn:
        for i in range(100):
            key = f'long-{i:03}'
            conn.execute("INSERT INTO annotations VALUES (?, 'ctx','note',?,NULL,1,'2026','2026')", (key, 'x'*5000))
            conn.execute("INSERT INTO annotation_segments VALUES (?,0,'c1','p1','original',NULL,0,6,'First.')", (key,))
    result = service.get('ctx', limit=1)
    assert len(json.dumps(result).encode()) < 8000
    first = result['evidence'][0]
    assert first['annotation_count'] == 100 and len(first['annotations']) == 1
    records = list(first['annotations'])
    cursor = first['annotation_cursor']
    while cursor:
        page = service.annotation_page('ctx', 'e1', cursor)
        assert 1 <= len(page['annotations']) <= 3
        records.extend(page['annotations'])
        cursor = page['annotation_cursor']
    assert [r['annotation_id'] for r in records] == [f'long-{i:03}' for i in reversed(range(100))]
    assert all(r['body'] == 'x'*5000 for r in records)
    assert service.annotation_page('ctx', 'e2')['annotations'] == []
    from solo_leveling.domain.annotation import encode_cursor
    for context, evidence, token in [
        ('other', 'e1', first['annotation_cursor']),
        ('ctx', 'e2', first['annotation_cursor']),
        ('ctx', 'e1', '_w'),
        ('ctx', 'e1', encode_cursor('ctx', 'evidence', 'e1', '\ud800', 'a')),
    ]:
        with pytest.raises(InvalidArgumentError):
            service.annotation_page(context, evidence, token)


def test_detail_annotations_are_context_scoped_readonly_and_independently_paged(detail, monkeypatch):
    db, _ = detail
    annotations = AnnotationService(db)
    repo.create_learning_context(db, LearningContext('other', 'v1', 'p1', 't1', embedding_set_id='idx'))
    foreign = annotations.create('other', 'note', '다른 맥락').annotation
    for i in range(21):
        annotations.create('ctx', 'note', f'기록 {i}')
    highlight = annotations.create('ctx', 'highlight', '', segments=[SegmentInput('c2', 0, 6, 'Second')]).annotation
    service = PaperDetailService(SQLitePaperDetailReader(db), annotations)
    # 목록에는 모든 기록의 원문 위치를 검사하는 N+1 조회나 쓰기 호출이 없다.
    def forbidden(*args, **kwargs):
        pytest.fail('상세 조회에서 위치 조회 또는 저장을 호출함')
    for name in ('get', 'create', 'update', 'delete'):
        monkeypatch.setattr(annotations, name, forbidden)
    before = Path(db).read_bytes()
    result = service.get('ctx', scope='s1', limit=1)
    assert result['summary'] is None
    page = result['annotation_page']
    assert page['context_id'] == 'ctx'
    assert len(page['items']) == 20  # 요약 limit=1과 무관한 기본 20건
    assert 'annotations_not_connected' not in result['limitations']
    tail = annotations.list('ctx', cursor=page['next_cursor'])
    ids = [x['annotation_id'] for x in page['items']] + [x.annotation_id for x in tail.items]
    assert len(ids) == len(set(ids)) == 22
    assert highlight.annotation_id in ids
    assert foreign.annotation_id not in ids
    assert tail.next_cursor is None
    assert Path(db).read_bytes() == before


def test_empty_annotation_page_is_not_an_unconnected_service(detail):
    db, _ = detail
    service = PaperDetailService(SQLitePaperDetailReader(db), AnnotationService(db))
    assert service.get('ctx')['annotation_page'] == {'context_id': 'ctx', 'items': (), 'next_cursor': None}
    assert 'annotation_page' not in PaperDetailService(SQLitePaperDetailReader(db)).get('ctx')


def test_invalid_detail_does_not_read_annotations(detail):
    from unittest.mock import Mock
    db, _ = detail
    annotations = Mock()
    service = PaperDetailService(SQLitePaperDetailReader(db), annotations)
    with pytest.raises(ResourceNotFoundError):
        service.get('missing')
    with pytest.raises(InvalidArgumentError):
        service.get('ctx', limit=True)
    with pytest.raises(ResourceNotFoundError):
        service.get('ctx', scope='missing')
    annotations.list.assert_not_called()


def test_stored_annotation_quote_is_not_reported_as_current_location(detail):
    db, _ = detail
    annotations = AnnotationService(db)
    item = annotations.create('ctx', 'highlight', '', segments=[SegmentInput('c1', 0, 5, 'First')]).annotation
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='changed' WHERE chunk_id='c1'")
    before = Path(db).read_bytes()
    page = PaperDetailService(SQLitePaperDetailReader(db), annotations).get('ctx')['annotation_page']
    assert page['items'][0]['segments'][0]['quote_snapshot'] == 'First'
    assert 'location_status' not in page['items'][0]
    checked = annotations.get('ctx', item.annotation_id, source_position=0)
    assert checked.segment_available == (False,)
    assert checked.source is None
    assert Path(db).read_bytes() == before


def test_detail_annotation_page_survives_mcp_output_and_existing_tools(detail):
    import asyncio
    import json
    from unittest.mock import Mock
    from solo_leveling.interfaces.mcp.server import MCPServices, create_server
    db, _ = detail
    annotations = AnnotationService(db)
    key = annotations.create('ctx', 'highlight', '', segments=[SegmentInput('c1', 0, 5, 'First')]).annotation.annotation_id
    service = PaperDetailService(SQLitePaperDetailReader(db), annotations)
    server = create_server(MCPServices(Mock(), Mock(), detail=service, annotations=annotations))
    before = Path(db).read_bytes()
    async def scenario():
        content, data = await server.call_tool('get_paper_detail', {'context_id': 'ctx'})
        assert json.loads(content[0].text) == data
        page = data['annotation_page']
        assert page['items'][0]['annotation_id'] == key
        assert page['items'][0]['revision'] == 1
        assert page['items'][0]['segments'][0]['parse_revision_id'] == 'p1'
        _, listed = await server.call_tool('list_annotations', {'request': {'context_id': 'ctx'}})
        assert page['items'] == listed['items']
        _, checked = await server.call_tool('get_annotation', {'request': {
            'context_id': 'ctx', 'annotation_id': key, 'source_position': 0}})
        assert checked['segment_available'] == [True]
        assert checked['source']['pdf_page'] == 1
        assert checked['source']['quote'] == 'First'
    asyncio.run(scenario())
    assert Path(db).read_bytes() == before


def test_saved_summary_pagination_is_unchanged_when_annotations_are_connected(detail):
    db, original = detail
    saved = save(db)
    annotations = AnnotationService(db)
    annotations.create('ctx', 'note', '요약과 별도로 저장한 기록')
    connected = PaperDetailService(SQLitePaperDetailReader(db), annotations)
    before = Path(db).read_bytes()
    for offset in (0, 1):
        args = {'limit': 1, 'offset': offset, 'summary_id': saved.summary_id}
        expected = original.get('ctx', **args)
        actual = connected.get('ctx', **args)
        page = actual.pop('annotation_page')
        assert actual == expected
        assert page['items'][0]['body'] == '요약과 별도로 저장한 기록'
    assert Path(db).read_bytes() == before
    save(db)  # 요약이 교체되면 메모 연결 여부와 무관하게 기존 커서를 거부한다.
    after_save = Path(db).read_bytes()
    assert after_save != before
    with pytest.raises(InvalidArgumentError):
        connected.get('ctx', limit=1, offset=1, summary_id=saved.summary_id)
    assert Path(db).read_bytes() == after_save


def test_detail_annotations_over_mcp_preserve_cursor_errors_and_recovery(detail):
    import asyncio
    import json
    from unittest.mock import Mock
    from mcp.shared.memory import create_connected_server_and_client_session
    from solo_leveling.interfaces.mcp.server import MCPServices, create_server
    db, _ = detail
    save(db)
    annotations = AnnotationService(db)
    for i in range(21):
        annotations.create('ctx', 'note', f'메모 {i}')
    reader = Mock(wraps=annotations)
    service = PaperDetailService(SQLitePaperDetailReader(db), reader)
    server = create_server(MCPServices(Mock(), Mock(), detail=service, annotations=annotations))
    before = Path(db).read_bytes()

    async def scenario():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            reader.list.side_effect = RuntimeError('private-note secret /private/db.sqlite')
            failed = await client.call_tool('get_paper_detail', {'context_id': 'ctx'})
            assert failed.isError
            assert failed.structuredContent is None
            message = failed.content[0].text
            assert message.endswith('INTERNAL_ERROR: 요청을 처리하지 못했습니다.')
            assert all(value not in message for value in ('private-note', 'secret', '/private/db.sqlite'))
            reader.list.side_effect = None
            recovered = await client.call_tool('get_paper_detail', {'context_id': 'ctx', 'limit': 1})
            assert not recovered.isError
            assert json.loads(recovered.content[0].text) == recovered.structuredContent
            page = recovered.structuredContent['annotation_page']
            assert len(page['items']) == 20
            assert len(recovered.structuredContent['summary']['items']) == 1
            next_page = await client.call_tool('list_annotations', {'request': {
                'context_id': 'ctx', 'cursor': page['next_cursor']}})
            assert not next_page.isError
            assert json.loads(next_page.content[0].text) == next_page.structuredContent
            tail = next_page.structuredContent
            ids = [x['annotation_id'] for x in page['items'] + tail['items']]
            assert len(ids) == len(set(ids)) == 21
            assert tail['next_cursor'] is None
            wrong_filter = await client.call_tool('list_annotations', {'request': {
                'context_id': 'ctx', 'kind': 'highlight', 'cursor': page['next_cursor']}})
            assert wrong_filter.isError
            assert 'INVALID_CURSOR' in wrong_filter.content[0].text
            reader.list.assert_called_with('ctx', limit=20)
    asyncio.run(scenario())
    assert Path(db).read_bytes() == before


def test_evidence_page_mcp_rejects_invalid_cursor(detail):
    import asyncio
    from unittest.mock import Mock
    from mcp.server.fastmcp.exceptions import ToolError
    from solo_leveling.domain.annotation import encode_cursor
    from solo_leveling.interfaces.mcp.server import MCPServices, create_server
    server = create_server(MCPServices(Mock(), Mock(), detail=detail[1]))
    token = encode_cursor('ctx', 'evidence', 'e1', '\ud800', 'a')
    with pytest.raises(ToolError, match='INVALID_ARGUMENT'):
        asyncio.run(server.call_tool('list_evidence_annotations',
                                     {'context_id': 'ctx', 'evidence_id': 'e1', 'cursor': token}))
