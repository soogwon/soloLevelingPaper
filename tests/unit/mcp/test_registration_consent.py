"""카드 확인 전 부작용, 토큰 바꿔치기와 재사용을 차단한다."""
import asyncio
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.interfaces.mcp.registration_consent import RegistrationGate, REGISTRATION_APP_URI
from solo_leveling.interfaces.mcp.server import MCPServices, create_server


def test_registration_card_then_confirmation():
    ingestion = Mock()
    ingestion.add_local_pdf.return_value = dict(paper_id='p', version_id='v', job_id='j', status='processing')
    server = create_server(MCPServices(Mock(), Mock(), ingestion))
    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        for name in ('add_paper', 'cancel_paper_registration'):
            assert tools[name].meta['ui']['visibility'] == ['app']
        assert tools['prepare_paper_registration'].meta['ui']['resourceUri'] == REGISTRATION_APP_URI
        args = {'source': {'kind': 'local_file', 'relative_path': 'paper.pdf'}, 'request_key': 'r'}
        prepared = await server.call_tool('prepare_paper_registration', args)
        ingestion.add_local_pdf.assert_not_called()
        assert 'confirmation_token' not in prepared.structuredContent
        assert json.loads(prepared.content[0].text) == prepared.structuredContent
        assert prepared.meta['confirmation_token'] not in prepared.content[0].text
        for text in ('OpenAI', '원문 청크', '대상 언어', '등록 후', '백그라운드 번역', '논문 전체 원문', '비용'):
            assert text in prepared.structuredContent['notice']
        with pytest.raises(Exception):
            await server.call_tool('add_paper', args)
        with pytest.raises(ToolError):
            await server.call_tool('add_paper', {**args, 'confirmation_token': 'forged'})
        ingestion.add_local_pdf.assert_not_called()
        confirmed = {**args, 'confirmation_token': prepared.meta['confirmation_token']}
        assert (await server.call_tool('add_paper', confirmed))[1]['job_id'] == 'j'
        with pytest.raises(ToolError):
            await server.call_tool('add_paper', confirmed)
    asyncio.run(check())
    ingestion.add_local_pdf.assert_called_once_with('paper.pdf', 'r')


def test_token_is_bound_to_source_and_request():
    gate = RegistrationGate()
    token = gate.prepare('paper.pdf', 'r')
    for path, key in [('other.pdf', 'r'), ('paper.pdf', 'other')]:
        with pytest.raises(ToolError):
            gate.consume(token, path, key)
    gate.consume(token, 'paper.pdf', 'r')


def test_expiry_cancel_restart_and_pending_limit():
    now = [0]
    gate = RegistrationGate(clock=lambda: now[0])
    token = gate.prepare('paper.pdf', 'r')
    now[0] = 600
    with pytest.raises(ToolError):
        gate.consume(token, 'paper.pdf', 'r')
    token = gate.prepare('paper.pdf', 'r')
    with pytest.raises(ToolError):
        RegistrationGate().consume(token, 'paper.pdf', 'r')
    gate.cancel(token)
    with pytest.raises(ToolError):
        gate.consume(token, 'paper.pdf', 'r')
    for i in range(128):
        gate.prepare('paper.pdf', str(i))
    with pytest.raises(ToolError):
        gate.prepare('paper.pdf', 'overflow')
    now[0] += 600
    gate.prepare('paper.pdf', 'after-expiry')
    assert len(gate.pending) == 1


def test_cancel_card_cannot_start_job():
    ingestion = Mock()
    server = create_server(MCPServices(Mock(), Mock(), ingestion))
    async def check():
        args = {'source': {'kind': 'local_file', 'relative_path': 'paper.pdf'}, 'request_key': 'r'}
        prepared = await server.call_tool('prepare_paper_registration', args)
        token = prepared.meta['confirmation_token']
        await server.call_tool('cancel_paper_registration', {'confirmation_token': token})
        with pytest.raises(ToolError):
            await server.call_tool('add_paper', {**args, 'confirmation_token': token})
    asyncio.run(check())
    ingestion.add_local_pdf.assert_not_called()
