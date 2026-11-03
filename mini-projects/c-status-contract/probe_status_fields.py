"""#60의 현재 MCP 필드 전달 여부를 출력한다. 제안 계약을 제품에 추가하지 않는다."""

import asyncio
import json
from unittest.mock import Mock

from solo_leveling.interfaces.mcp.server import MCPServices, create_server


async def main():
    ingestion = Mock()
    proposed = {
        'failed_stage': 'translate', 'failure_code': 'provider_unavailable',
        'retryable': True, 'next_action': 'run_translation_recovery', 'outcome': 'partial',
    }
    ingestion.get_status.return_value = {
        'job_id': 'synthetic-job', 'version_id': 'synthetic-version',
        'status': 'ready', 'stage': 'index', 'result_available': True,
        'limitations': ['translation_failed:synthetic-chunk:provider_unavailable'],
        **proposed,
    }
    server = create_server(MCPServices(Mock(), Mock(), ingestion))
    tool = next(t for t in await server.list_tools() if t.name == 'get_paper_status')
    content, result = await server.call_tool('get_paper_status', {'job_id': 'synthetic-job'})
    assert json.loads(content[0].text) == result
    ingestion.get_status.assert_called_once_with('synthetic-job')
    # 누락을 성공 조건으로 고정하지 않고 현재 구현의 관찰 결과로 출력한다.
    print(json.dumps({
        'missing_in_schema': sorted(set(proposed) - set(tool.outputSchema['properties'])),
        'missing_in_response': sorted(set(proposed) - set(result)),
        'status': result['status'], 'stage': result['stage'],
        'capabilities': result['capabilities'],
        'text_matches_structured': True,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
