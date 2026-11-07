"""별도 가상환경에 설치한 wheel을 저장소 밖에서 검증한다."""

import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(not os.environ.get('SOLO_INSTALLED_PYTHON'),
                    reason='새 가상환경에 wheel[local] 설치 후 SOLO_INSTALLED_PYTHON 지정 필요')
def test_installed_distribution_outside_repository(tmp_path):
    # 심볼릭 링크를 유지해 설치한 가상환경의 Python으로 검증한다.
    python = Path(os.path.abspath(os.environ['SOLO_INSTALLED_PYTHON']))
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(('PYTHON', 'OPENAI', 'GENERATION_', 'TRANSLATION_', 'HF_')):
            environment.pop(key)
    environment.update(DATA_ROOT=str(tmp_path / 'data'), LOCAL_ONLY='true',
                       ALLOW_EXTERNAL_API='false', OPENAI_API_KEY='test-only')
    env_file = tmp_path / 'settings.env'
    env_file.write_text(f'DATA_ROOT={tmp_path.as_posix()}/data\nOPENAI_API_KEY=test-only\n',
                        encoding='utf-8')
    args = [str(python), '-I', '-X', 'utf8', '-m', 'solo_leveling.interfaces.cli.desktop']
    for action in ('config', 'check'):
        result = subprocess.run([*args, action, '--env-file', str(env_file)],
                                cwd=tmp_path, env=environment, capture_output=True,
                                text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'data').exists()
    # 실제 설치된 SDK와 서버만 사용한다. 소스 경로·모델·유료 API는 사용하지 않는다.
    program = '''
import asyncio, os, sys
from pathlib import Path
from importlib.metadata import distribution
from importlib.resources import files
import solo_leveling
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
assert Path(solo_leveling.__file__).is_relative_to(Path(sys.prefix))
assert sys.prefix != sys.base_prefix
assert 'include-system-site-packages = false' in (
    Path(sys.prefix) / 'pyvenv.cfg').read_text(encoding='utf-8').lower()
assert {
    'solo-leveling-mcp', 'solo-leveling-desktop',
    'solo-leveling-prepare-model', 'solo-leveling-mcp-diagnostics'
} <= {entry.name for entry in distribution('solo-leveling-paper').entry_points}
assert files('solo_leveling.interfaces.mcp').joinpath('assets/paper-status.LICENSE.txt').is_file()
async def run():
    params = StdioServerParameters(command=sys.executable,
        args=['-I', '-X', 'utf8', '-m', 'solo_leveling.interfaces.cli.desktop', 'serve',
              '--env-file', sys.argv[1]], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                'prepare_paper_registration', 'cancel_paper_registration',
                'add_paper', 'get_paper_status', 'list_paper_jobs',
                'start_learning', 'ask_paper', 'get_evidence', 'get_learning_dashboard',
                'submit_paper_question', 'get_paper_question_status',
                'get_paper_detail', 'get_reading_questions', 'save_reading_answer', 'get_reading_comparison',
                'get_quiz', 'submit_quiz', 'get_quiz_attempt', 'list_quiz_attempts',
                'create_annotation', 'get_annotation', 'list_annotations',
                        'update_annotation', 'delete_annotation',
                        'get_quiz_scores', 'review_quiz_attempt'}
            # 설치본에서도 목록 조회 계약과 빈 DB·오류 응답을 실제 stdio로 확인한다.
            listing = next(t for t in tools.tools if t.name == 'list_paper_jobs')
            assert set(listing.inputSchema['properties']) == {'query', 'limit', 'cursor'}
            assert not listing.inputSchema.get('required')
            assert set(listing.outputSchema['required']) == {'jobs', 'next_cursor'}
            assert listing.annotations.readOnlyHint is True
            assert listing.annotations.openWorldHint is False
            for arguments in ({}, {'query': '없는 논문', 'limit': 1}):
                result = await session.call_tool('list_paper_jobs', arguments)
                assert not result.isError
                assert result.structuredContent == {'jobs': [], 'next_cursor': None}
            for arguments in ({'limit': 0}, {'cursor': 'invalid-cursor'}):
                result = await session.call_tool('list_paper_jobs', arguments)
                assert result.isError
            # 잘못된 커서는 안전한 서비스 오류이며 다음 정상 호출을 막지 않는다.
            assert 'INVALID_ARGUMENT' in result.content[0].text
            assert 'invalid-cursor' not in result.content[0].text
            result = await session.call_tool('list_paper_jobs', {})
            assert not result.isError
            assert result.structuredContent == {'jobs': [], 'next_cursor': None}
            dashboard = next(t for t in tools.tools if t.name == 'get_learning_dashboard')
            assert dashboard.annotations.readOnlyHint is True
            dashboard_resource = await session.read_resource('ui://solo-leveling/learning-dashboard.html')
            assert '학습 대시보드' in dashboard_resource.contents[0].text
            reading = next(t for t in tools.tools if t.name == 'get_reading_questions')
            reading_resource = await session.read_resource(reading.meta['ui']['resourceUri'])
            assert reading_resource.contents[0].mimeType == 'text/html;profile=mcp-app'
            assert '<title>읽기 전 질문</title>' in reading_resource.contents[0].text
            comparison = next(t for t in tools.tools if t.name == 'get_reading_comparison')
            comparison_resource = await session.read_resource(comparison.meta['ui']['resourceUri'])
            assert '<title>읽기 전후 비교</title>' in comparison_resource.contents[0].text
            assert files('solo_leveling.interfaces.mcp').joinpath('assets/reading-comparison.LICENSE.txt').is_file()
            missing = await session.call_tool('get_learning_dashboard', {'context_id': 'missing'})
            assert missing.isError and 'NOT_FOUND' in missing.content[0].text
            resource = await session.read_resource('ui://solo-leveling/paper-status.html')
            assert '논문 처리 상태' in resource.contents[0].text
            result = await session.call_tool('get_paper_status', {'job_id': 'missing'})
            assert result.isError
asyncio.run(asyncio.wait_for(run(), timeout=60))
'''
    result = subprocess.run([str(python), '-I', '-X', 'utf8', '-c', program, str(env_file)],
                            cwd=tmp_path, env=environment, capture_output=True,
                            text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'data/solo_leveling.sqlite3').is_file()
    logs = list((tmp_path / 'data/diagnostics').glob('*.log'))
    assert logs
    assert all('test-only' not in path.read_text(encoding='utf-8') for path in logs)
    if os.name == 'posix':
        # 설치본의 실제 stdio 서버가 정상 종료된 뒤에만 같은 로그의 종료 기록이 남는다.
        directory = tmp_path / 'data/diagnostics'
        records = list(directory.glob('slp-log-v1-mcp-*.closed.json'))
        assert len(records) == 1
        record = json.loads(records[0].read_text(encoding='utf-8'))
        assert record['owner'] == 'mcp'
        run_id = record['run_id']
        assert records[0].name == f'slp-log-v1-mcp-{run_id}.closed.json'
        entry, = record['files']
        assert entry['kind'] == 'safe'
        assert entry['name'] == f'slp-log-v1-mcp-safe-{run_id}.log'
        info = (directory / entry['name']).stat()
        assert entry['size_bytes'] == info.st_size
        assert entry['identity'] == {
            'platform': 'posix', 'st_dev': info.st_dev, 'st_ino': info.st_ino, 'st_size': info.st_size,
        }
