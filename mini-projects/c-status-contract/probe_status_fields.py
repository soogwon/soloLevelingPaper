"""#60의 현재 MCP 필드 전달 여부를 출력한다. 제안 계약을 제품에 추가하지 않는다."""

import asyncio
import json
from unittest.mock import Mock, call

from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.interfaces.mcp.server import MCPServices, create_server


async def main():
    ingestion = Mock()
    server = create_server(MCPServices(Mock(), Mock(), ingestion))
    observations = []
    async with create_connected_server_and_client_session(server) as client:
        tools = await client.list_tools()
        tool = next(t for t in tools.tools if t.name == 'get_paper_status')
        for code in ('provider_unavailable', 'empty_translation', 'translation_failed'):
            partial = code != 'translation_failed'
            proposed = {
                'failed_stage': 'translate', 'failure_code': code,
                # B 제안: 저장된 코드만으로 재시도 가능성을 단정하지 않는다.
                'retryable': None, 'next_action': None,
                'outcome': 'partial' if partial else 'failed',
            }
            ingestion.get_status.return_value = {
                'job_id': 'synthetic-job', 'version_id': 'synthetic-version',
                'status': 'ready' if partial else 'failed',
                'stage': 'index' if partial else 'translate', 'result_available': partial,
                'limitations': [f'translation_failed:synthetic-chunk:{code}' if partial else code],
                **proposed,
            }
            ingestion.reset_mock()
            response = await client.call_tool('get_paper_status', {'job_id': 'synthetic-job'})
            assert not response.isError
            result = response.structuredContent
            assert json.loads(response.content[0].text) == result
            assert ingestion.mock_calls == [call.get_status('synthetic-job')]
            assert result['status'] == ('ready' if partial else 'failed')
            assert result['stage'] == ('index' if partial else 'translate')
            assert ('start_learning' in result['capabilities']) == partial
            # 추후 필드가 연결되면 값 변형도 확인하되, 누락 자체를 통과 기준으로 삼지 않는다.
            for name, value in proposed.items():
                if name in result:
                    assert result[name] == value
            observations.append({
                'case': code,
                'missing_in_schema': sorted(set(proposed) - set(tool.outputSchema['properties'])),
                'missing_in_response': sorted(set(proposed) - set(result)),
                'status': result['status'], 'stage': result['stage'],
                'capabilities': result['capabilities'], 'text_matches_structured': True,
                'status_queries': 1, 'other_ingestion_calls': 0,
            })
    print(json.dumps(observations, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
