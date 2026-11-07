"""실제 MCP stdio에서 저장된 학습 맥락과 텍스트 폴백을 함께 검증한다."""

import asyncio
from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.application.annotations.service import AnnotationService
from solo_leveling.domain.models import Evidence, PaperSection
from solo_leveling.domain.paper_summary import SummaryItem
from tests.unit.test_learning_persistence import setup_db, publish


@pytest.mark.parametrize('with_catalog', [False, True])
def test_dashboard_reads_saved_context_over_stdio(setup_db, tmp_path, with_catalog):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.complete_step(db, context.context_id, 'preview')
    repo.create_learning_context(db, replace(context, context_id='other', goal='skim'))
    note = AnnotationService(db).create('ctx', 'note', '상세 조회 연결 확인').annotation
    if with_catalog:
        with closing(repo.get_connection(db)) as conn, conn:
            conn.execute("UPDATE chunks SET section_id='s1' WHERE chunk_id='c1'")
        repo.insert_sections(db, [PaperSection('s1', 'p1', '방법', 1, 1, 1, 1)])
        repo.save_evidences(db, 'ctx', [Evidence('e1', 'c1', '첫째.', 'First.')])
        repo.save_summary(db, 'ctx', 'paper', [SummaryItem('method', '저장된 방법', ('e1',))])
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=str(root / 'src'), DATA_ROOT=str(tmp_path),
        SQLITE_FILENAME=Path(db).name, LOCAL_ONLY='true', ALLOW_EXTERNAL_API='false',
        OPENAI_API_KEY='test-key', HF_HUB_OFFLINE='1', ANONYMIZED_TELEMETRY='false')

    async def run():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'solo_leveling.interfaces.mcp'], env=environment, cwd=root)
        with (tmp_path / 'stderr.log').open('w') as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    before = Path(db).read_bytes()
                    data = await session.call_tool('get_learning_dashboard', {'context_id': 'ctx'})
                    assert not data.isError
                    assert data.structuredContent['progress']['completion_ratio'] == .25
                    assert data.structuredContent['processing']['job_id'] == 'job'
                    assert json.loads(data.content[0].text) == data.structuredContent
                    # 대시보드에서 선택한 맥락을 그대로 상세 조회에 전달한다.
                    detail = await session.call_tool('get_paper_detail', {
                        'context_id': data.structuredContent['context_id'],
                        'scope': 'paper', 'offset': 0, 'limit': 20,
                    })
                    assert not detail.isError
                    for key in ('context_id', 'version_id', 'paper_id'):
                        assert detail.structuredContent[key] == data.structuredContent[key]
                    if with_catalog:
                        item = detail.structuredContent['summary']['items'][0]
                        assert item['text'] == '저장된 방법' and item['evidence_ids'] == ['e1']
                        evidence = detail.structuredContent['evidence'][0]
                        section, = detail.structuredContent['section_catalog']
                        assert evidence['section_id'] == section['section_id'] == 's1'
                        assert section['title'] == '방법' and section['parse_revision_id'] == 'p1'
                    else:
                        assert detail.structuredContent['summary'] is None
                        assert detail.structuredContent['section_catalog'] == []
                    assert detail.structuredContent['annotation_page']['context_id'] == 'ctx'
                    assert [item['annotation_id'] for item in detail.structuredContent['annotation_page']['items']] == [note.annotation_id]
                    assert json.loads(detail.content[0].text) == detail.structuredContent
                    other = await session.call_tool('get_learning_dashboard', {'context_id': 'other'})
                    assert not other.isError
                    assert other.structuredContent['progress']['recorded'] is False
                    assert other.structuredContent['summary'] is None
                    bad = await session.call_tool('get_learning_dashboard', {'context_id': 'missing'})
                    assert bad.isError and 'NOT_FOUND' in bad.content[0].text
                    assert Path(db).read_bytes() == before

    asyncio.run(asyncio.wait_for(run(), timeout=60))
