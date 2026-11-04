"""#119 계약 검토용 MCP 실험. 제품 저장소·Desktop 보존 기능을 구현하지 않는다."""

import asyncio
import json
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session


def test_lookup_without_first_response(tmp_path):
    async def scenario():
        server = FastMCP('합성 요청 키 실험')
        records = {}
        accepted, release = asyncio.Event(), asyncio.Event()
        executions = []

        @server.tool(structured_output=True)
        async def probe_submit(context_id: str, request_key: str) -> dict[str, str]:
            # 작업 결과 대신 합성 접수 기록만 둔다. DB 원자성 검증이 아니다.
            executions.append(request_key)
            records[(context_id, request_key)] = {
                'execution_id': 'synthetic-execution', 'state': 'accepted'}
            accepted.set()
            await release.wait()
            return records[(context_id, request_key)]

        @server.tool(structured_output=True)
        async def probe_lookup(context_id: str, request_key: str) -> dict[str, str]:
            return records.get((context_id, request_key), {'state': 'NOT_FOUND'})

        # 호출자가 전송 전에 키를 보존하는 조건을 명시적으로 만든다.
        journal = tmp_path / 'synthetic-caller.json'
        journal.write_text(json.dumps({'context_id': 'synthetic-context',
                                      'request_key': str(uuid4())}))
        args = json.loads(journal.read_text())
        async with create_connected_server_and_client_session(server) as client:
            pending = asyncio.create_task(client.call_tool('probe_submit', args))
            try:
                await asyncio.wait_for(accepted.wait(), 5)
                assert not pending.done()
                # 최초 접수 응답도 execution_id도 없는 상태에서 키만으로 조회한다.
                found = await client.call_tool('probe_lookup', args)
                assert found.structuredContent['state'] == 'accepted'
                assert found.structuredContent['execution_id'] == 'synthetic-execution'
                wrong = await client.call_tool('probe_lookup', dict(args, context_id='other'))
                missing = await client.call_tool('probe_lookup', dict(args, request_key='unknown'))
                assert wrong.structuredContent == missing.structuredContent == {'state': 'NOT_FOUND'}
            finally:
                release.set()
                # 늦은 응답은 호출자의 복구 정보로 사용하지 않고 버린다.
                await pending

        # 새 클라이언트는 저장된 키로 복귀한다. 서버는 같은 메모리 대역이다.
        async with create_connected_server_and_client_session(server) as client:
            restored = json.loads(journal.read_text())
            found = await client.call_tool('probe_lookup', restored)
            assert found.structuredContent['state'] == 'accepted'
            assert len(executions) == 1

    asyncio.run(asyncio.wait_for(scenario(), 15))
