"""#290: 실제 stdio MCP 서버의 오류가 도구와 관계없이 한 가지 형식을 따르는지 확인한다."""

import asyncio
import os
from pathlib import Path
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# CODE[: REASON][: field=이름]: 안내 문장. 도구 이름 접두어나 메시지 없는 코드는 허용하지 않는다.
ERROR_FORMAT = re.compile(r'[A-Z][A-Z_]+(: [A-Z][A-Z_]+)?(: field=[a-z_.]+)?: \S.*')
UNKNOWN = '00000000-0000-4000-8000-000000000000'
INVALID_CONTEXT = 'INVALID_ARGUMENT: INVALID_INPUT: field=context_id: 입력값을 확인해주세요.'
NOT_FOUND = 'NOT_FOUND: 요청한 자료를 찾을 수 없습니다.'

CASES = [
    # 형식이 틀린 학습 맥락 ID는 도구마다 INVALID_ARGUMENT와 필드 이름으로 답한다.
    ('get_learning_dashboard', {'context_id': 'not-a-uuid'}, INVALID_CONTEXT),
    ('get_quiz_scores', {'context_id': 'not-a-uuid'}, INVALID_CONTEXT),
    ('get_quiz_attempt', {'context_id': 'not-a-uuid', 'attempt_id': 'a'}, INVALID_CONTEXT),
    ('get_evidence', {'context_id': 'not-a-uuid', 'evidence_ids': ['e']}, INVALID_CONTEXT),
    # 형식은 맞지만 없는 자료는 같은 NOT_FOUND 문장으로 답한다.
    ('get_learning_dashboard', {'context_id': UNKNOWN}, NOT_FOUND),
    ('get_quiz_scores', {'context_id': UNKNOWN}, NOT_FOUND),
    ('get_quiz_attempt', {'context_id': UNKNOWN, 'attempt_id': 'a'}, NOT_FOUND),
    ('get_evidence', {'context_id': UNKNOWN, 'evidence_ids': ['e']}, NOT_FOUND),
    ('get_translation_recovery', {'version_id': 'v', 'base_embedding_set_id': 'e',
                                  'request_key': 'never-used'}, NOT_FOUND),
    # 입력 오류는 문제가 된 공개 필드 이름을 함께 알려 준다.
    ('get_quiz_scores', {'context_id': '   '}, INVALID_CONTEXT),
    ('get_quiz_scores', {}, INVALID_CONTEXT),
    ('save_reading_answer', {'context_id': UNKNOWN, 'template_id': 'before.background'},
     'INVALID_ARGUMENT: INVALID_INPUT: field=text: 입력값을 확인해주세요.'),
    # 재파싱 오류도 코드만 보내지 않고 안내 문장을 붙인다.
    ('get_paper_reparse', {'context_id': UNKNOWN, 'request_key': 'never-used'},
     'REPARSE_CONTEXT_UNAVAILABLE: 재파싱할 수 있는 학습 맥락을 찾을 수 없습니다.'),
    # 미등록 도구 이름은 사용자 입력이므로 오류에 되돌려 넣지 않는다.
    ('no_such_tool', {}, 'INVALID_ARGUMENT: INVALID_INPUT: 입력값을 확인해주세요.'),
]


def test_tool_errors_share_one_format(tmp_path):
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
        results = []
        with (tmp_path / 'stderr.log').open('w', encoding='utf-8') as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for name, arguments, _ in CASES:
                        result = await session.call_tool(name, arguments)
                        results.append((result.isError, result.content[0].text))
        return results

    results = asyncio.run(scenario())
    for (name, _, expected), (is_error, text) in zip(CASES, results):
        assert is_error, name
        assert ERROR_FORMAT.fullmatch(text), (name, text)
        assert not text.startswith('Error executing tool'), name
        assert text == expected, (name, text)
