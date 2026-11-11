"""실제 자식 프로세스의 stdio MCP 초기화와 도구 목록을 검증한다."""

import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_lists_tools(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.update({
        'PYTHONPATH': str(root / 'src'),
        'DATA_ROOT': str(tmp_path / 'runtime'),
        'OPENAI_API_KEY': 'test-key',
        'ALLOW_EXTERNAL_API': 'false',
        'LOCAL_ONLY': 'true',
    })

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'solo_leveling.interfaces.mcp'], env=environment, cwd=root)
        with (tmp_path / 'stderr.log').open('w', encoding='utf-8') as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == {
                        'prepare_paper_registration', 'cancel_paper_registration',
                        'add_paper', 'get_paper_status', 'start_learning',
                        'ask_paper', 'get_evidence', 'submit_paper_question', 'get_paper_question_status',
                        'open_paper_question_card', 'reserve_card_question_key', 'list_card_question_keys',
                        'list_paper_jobs', 'get_paper_detail', 'list_evidence_annotations',
                        'get_learning_dashboard',
                        'get_reading_questions', 'save_reading_answer', 'get_reading_comparison',
                        'get_quiz', 'submit_quiz', 'get_quiz_attempt', 'list_quiz_attempts',
                        'create_annotation', 'get_annotation', 'list_annotations',
                        'update_annotation', 'delete_annotation',
                        'get_quiz_scores', 'review_quiz_attempt',
                        # 번역 복구(#223)와 재파싱(#116) 도구
                        'prepare_translation_recovery', 'cancel_translation_recovery',
                        'recover_translation', 'get_translation_recovery', 'select_recovery_context',
                        'prepare_paper_reparse', 'cancel_paper_reparse', 'submit_paper_reparse',
                        'get_paper_reparse', 'select_reparsed_context',
                    }
                    ask = next(tool for tool in tools.tools if tool.name == 'ask_paper')
                    assert ask.outputSchema is not None
                    assert ask.annotations.openWorldHint is True
                    status_tool = next(tool for tool in tools.tools if tool.name == 'get_paper_status')
                    assert status_tool.meta['ui']['resourceUri'] == 'ui://solo-leveling/paper-status.html'
                    resource = await session.read_resource(status_tool.meta['ui']['resourceUri'])
                    assert resource.contents[0].mimeType == 'text/html;profile=mcp-app'
                    assert '논문 처리 상태' in resource.contents[0].text
                    assert resource.contents[0].meta['ui']['csp']['connectDomains'] == []
                    listing = await session.call_tool('list_paper_jobs', {})
                    assert listing.isError is False
                    assert listing.structuredContent == {'jobs': [], 'next_cursor': None}
                    # 임시 DB에 합성 작업을 넣고 목록에서 찾은 ID로 상태를 조회한다.
                    import sqlite3
                    from contextlib import closing
                    with closing(sqlite3.connect(tmp_path / 'runtime/solo_leveling.sqlite3')) as conn, conn:
                        conn.execute("INSERT INTO papers VALUES ('p','테스트 논문','local_file','2026-01-01')")
                        conn.execute("INSERT INTO paper_versions VALUES ('v','p','h','/private/p.pdf','paper.pdf','2026-01-01')")
                        conn.execute("INSERT INTO processing_jobs VALUES ('j','v','failed','translate','[]','2026-01-01','2026-01-01')")
                    listing = await session.call_tool('list_paper_jobs', {'query': '테스트'})
                    assert listing.isError is False
                    found = listing.structuredContent['jobs'][0]
                    status = await session.call_tool('get_paper_status', {'job_id': found['job_id']})
                    assert status.isError is False
                    assert status.structuredContent['status'] == 'failed'
                    assert status.structuredContent['version_id'] == found['version_id']
                    failure = await session.call_tool('get_evidence',
                        {'context_id': 'missing', 'evidence_ids': ['missing']})
                    assert failure.isError is True
                    message = failure.content[0].text
                    assert 'NOT_FOUND' in message
                    assert str(tmp_path) not in message

    asyncio.run(scenario())
    assert (tmp_path / 'runtime/solo_leveling.sqlite3').is_file()


def test_stdio_success_contract_with_fake_services(tmp_path):
    """모델·DB 대신 테스트 대역을 연결하되 실제 MCP 전송 응답을 검사한다."""
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment['PYTHONPATH'] = os.pathsep.join((str(root / 'src'), str(root)))
    program = '''
from tests.integration.test_mcp_workflow import (
    WorkflowAnswer, WorkflowEvidence, WorkflowIngestion, WorkflowContexts,
)
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
create_server(MCPServices(
    WorkflowAnswer(), WorkflowEvidence(), WorkflowIngestion(), WorkflowContexts(),
)).run(transport='stdio')
'''

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-B', '-c', program], env=environment, cwd=root)
        with (tmp_path / 'stderr.log').open('w', encoding='utf-8') as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(name, arguments):
                        result = await session.call_tool(name, arguments)
                        assert result.isError is False
                        assert isinstance(result.structuredContent, dict)
                        assert json.loads(result.content[0].text) == result.structuredContent
                        return result.structuredContent

                    args = {
                        'source': {'kind': 'local_file', 'relative_path': 'paper.pdf'},
                        'request_key': 'request-1',
                    }
                    prepared = await session.call_tool('prepare_paper_registration', args)
                    assert prepared.structuredContent['status'] == 'confirmation_required'
                    added = await call('add_paper', {**args, 'confirmation_token': prepared.meta['confirmation_token']})
                    listing = await call('list_paper_jobs', {})
                    assert listing['jobs'][0]['job_id'] == added['job_id']
                    status = await call('get_paper_status', {'job_id': listing['jobs'][0]['job_id']})
                    context = await call('start_learning', {'version_id': status['version_id']})
                    repeated = await call('start_learning', {'version_id': status['version_id']})
                    assert repeated == context
                    response = await call('ask_paper', {
                        'context_id': context['context_id'],
                        'question': '구현할 때 핵심 구성 요소는 무엇인가요?',
                    })
                    assert response['status'] == 'ok'
                    assert response['verification_level'] == 'structural_only'
                    assert response['reason_code'] is None
                    assert 'answer' not in response
                    evidence_ids = response['claims'][0]['evidence_ids']
                    details = await call('get_evidence', {
                        'context_id': context['context_id'], 'evidence_ids': evidence_ids,
                    })
                    assert details['evidence'][0]['evidence_id'] == evidence_ids[0]
                    assert details['evidence'][0]['printed_page_label'] is None
                    assert details['evidence'][0]['file_display_name'] == 'paper.pdf'
                    invalid = await session.call_tool('ask_paper', {
                        'context_id': context['context_id'], 'question': '질문', 'top_k': True,
                    })
                    assert invalid.isError is True
                    assert invalid.structuredContent is None
                    assert invalid.content[0].text

    asyncio.run(scenario())
