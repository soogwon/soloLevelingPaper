"""수집 결과 보존과 Desktop·진단 실행기의 실제 MCP 로그 계약을 검증한다."""

import asyncio
import io
import json
import os
from pathlib import Path
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
from solo_leveling.infrastructure.storage.vector_store import ChromaCloseResult


@pytest.mark.parametrize('detailed', [False, True])
@pytest.mark.parametrize('code', [ChromaCloseResult.FAILED, ChromaCloseResult.UNSUPPORTED])
@pytest.mark.parametrize('fail_ingestion', [False, True])
def test_ingestion_outcome_survives_filtered_close_failure(
        tmp_path, monkeypatch, chroma_client_factory, capsys, detailed, code, fail_ingestion):
    from tests.integration.test_ingestion_chroma_ownership import _run
    from tests.integration.test_ingestion import _fake_embed_texts, _fake_embedding_dimension
    from solo_leveling.workers import ingestion

    monkeypatch.setattr(ingestion, 'embed_texts', _fake_embed_texts)
    monkeypatch.setattr(ingestion, 'embedding_dimension', _fake_embedding_dimension)
    monkeypatch.setattr(ingestion, 'get_client', chroma_client_factory)
    monkeypatch.setattr(ingestion, 'close_client', lambda client: code)
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stderr', SafeDiagnosticStream(output, detailed=detailed))
    original = ValueError('SYNTHETIC_PRIVATE_ERROR')
    if fail_ingestion:
        def fail(*args, **kwargs):
            raise original
        monkeypatch.setattr(ingestion, 'verify_chunk_embeddings', fail)
        with pytest.raises(ValueError) as caught:
            _run(tmp_path)
        assert caught.value is original
    else:
        assert _run(tmp_path)['status'] == 'ready'
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row for row in rows if row['event'] == 'chroma_close'] == [
        {'event': 'chroma_close', 'code': code.value}]
    assert 'SYNTHETIC_PRIVATE' not in output.getvalue()
    assert capsys.readouterr().out == ''


PROBE = r'''
import json, runpy, sys
from types import SimpleNamespace
from mcp.server.fastmcp import FastMCP
from solo_leveling.workers.ingestion import _release_owned_client

def server(*args, **kwargs):
    app = FastMCP('close-log-probe', log_level='ERROR')

    @app.tool()
    def probe(mode: str) -> dict:
        def fail():
            raise ValueError('SYNTHETIC_PRIVATE_CLOSE_ERROR')
        client = SimpleNamespace(close=fail) if mode == 'failed' else object()
        _release_owned_client(client)
        print(json.dumps({'event': 'chroma_close', 'code': 'CHROMA_CLOSE_FAILED',
                          'path': 'SYNTHETIC_PRIVATE_PATH'}), file=sys.stderr, flush=True)
        return {'ok': True, 'mode': mode}

    app.run(transport='stdio')

runpy.run_module = server
entry, env_file, detailed = sys.argv[1:]
options = ['--detailed-diagnostics'] if detailed == '1' else []
if entry == 'desktop':
    from solo_leveling.interfaces.cli.desktop import main
    result = main(['serve', '--env-file', env_file, *options])
else:
    from solo_leveling.interfaces.cli.mcp_diagnostics import main
    result = main(options)
raise SystemExit(result)
'''


@pytest.mark.parametrize('entry', ['desktop', 'diagnostics'])
@pytest.mark.parametrize('detailed', [False, True])
def test_close_failure_logs_preserve_real_mcp_stdio(tmp_path, entry, detailed):
    root = Path(__file__).resolve().parents[2]
    data = tmp_path / 'data'
    env_file = tmp_path / 'settings.env'
    env_file.write_text(f'DATA_ROOT={data.as_posix()}\nOPENAI_API_KEY=synthetic\n'
                        'LOCAL_ONLY=true\nALLOW_EXTERNAL_API=false\n', encoding='utf-8')
    env = dict(os.environ)
    for key in ('DATA_ROOT', 'SOLO_LEVELING_COLLECT_RAW_LOGS', 'HF_HOME', 'HF_HUB_CACHE'):
        env.pop(key, None)
    env.update(PYTHONPATH=str(root / 'src'), SOLO_LEVELING_ENV_FILE=str(env_file))

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-X', 'utf8', '-c', PROBE, entry, str(env_file), str(int(detailed))],
            env=env, cwd=tmp_path)
        with (tmp_path / 'host-stderr.log').open('w', encoding='utf-8') as host:
            async with stdio_client(parameters, errlog=host) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    assert [tool.name for tool in (await session.list_tools()).tools] == ['probe']
                    for mode in ('failed', 'unsupported'):
                        result = await session.call_tool('probe', {'mode': mode})
                        assert not result.isError
                        assert json.loads(result.content[0].text) == {'ok': True, 'mode': mode}

    asyncio.run(asyncio.wait_for(scenario(), timeout=25))
    text = next((data / 'diagnostics').glob('mcp-*.log')).read_text(encoding='utf-8')
    rows = [json.loads(line) for line in text.splitlines()]
    assert [row for row in rows if row['event'] == 'chroma_close'] == [
        {'event': 'chroma_close', 'code': 'CHROMA_CLOSE_FAILED'},
        {'event': 'chroma_close', 'code': 'CHROMA_CLOSE_UNSUPPORTED'}]
    assert 'SYNTHETIC_PRIVATE' not in text
    assert (tmp_path / 'host-stderr.log').read_text(encoding='utf-8') == ''
