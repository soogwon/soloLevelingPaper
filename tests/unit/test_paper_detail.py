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
    "UPDATE chunks SET translation_revision_id='wrong' WHERE chunk_id='c1'",
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
