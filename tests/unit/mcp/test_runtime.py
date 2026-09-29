"""MCP 런타임의 설정 우선순위와 저장 경로 경계를 검증한다."""

from pathlib import Path

import pytest

from solo_leveling.interfaces.mcp.runtime import load_environment, runtime_paths


def test_process_environment_wins_without_mutation(tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('DATA_ROOT=from-file\nOPENAI_API_KEY=file-key\n', encoding='utf-8')
    process = {'DATA_ROOT': 'from-process', 'OPENAI_API_KEY': 'process-key'}
    result = load_environment(env_file, process)
    assert result['DATA_ROOT'] == 'from-process'
    assert result['OPENAI_API_KEY'] == 'process-key'
    assert process == {'DATA_ROOT': 'from-process', 'OPENAI_API_KEY': 'process-key'}


def test_missing_env_file_uses_process_environment(tmp_path):
    result = load_environment(tmp_path / 'missing', {'DATA_ROOT': str(tmp_path)})
    assert result == {'DATA_ROOT': str(tmp_path)}


def test_paths_are_resolved_below_data_root(tmp_path):
    paths = runtime_paths({'DATA_ROOT': str(tmp_path), 'SQLITE_FILENAME': 'db/data.sqlite',
                           'CHROMA_SUBDIR': 'vectors'})
    assert paths.db_path == (tmp_path / 'db/data.sqlite').resolve()
    assert paths.chroma_dir == (tmp_path / 'vectors').resolve()
    assert paths.import_dir == (tmp_path / 'imports').resolve()
    assert paths.pdf_dir == (tmp_path / 'pdfs').resolve()


@pytest.mark.parametrize(('key', 'value'), [
    ('SQLITE_FILENAME', '../outside.sqlite'),
    ('CHROMA_SUBDIR', '../outside'),
    ('IMPORT_SUBDIR', '../outside'),
    ('PDF_SUBDIR', '../outside'),
    ('SQLITE_FILENAME', 'D:/outside.sqlite'),
])
def test_paths_cannot_escape_data_root(tmp_path, key, value):
    values = {'DATA_ROOT': str(tmp_path), key: value}
    with pytest.raises(ValueError):
        runtime_paths(values)


def test_runtime_shares_embedder_and_closes_it(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    from solo_leveling.interfaces.mcp import runtime

    embedder = Mock()
    factory = Mock(return_value=embedder)
    monkeypatch.setattr(runtime, 'ProcessEmbedder', factory)
    services = SimpleNamespace(answer=object(), evidence=object(),
        search=SimpleNamespace(contexts=object()), translation=object(), translation_settings=object())
    build = Mock(return_value=services)
    monkeypatch.setattr(runtime, 'build_services', build)
    monkeypatch.setattr(runtime, 'get_client', Mock(return_value=object()))
    manager = Mock()
    manager_factory = Mock(return_value=manager)
    monkeypatch.setattr(runtime, 'LocalIngestionManager', manager_factory)
    monkeypatch.setattr(runtime, 'create_server', lambda services, *, lifespan: lifespan)
    lifespan = runtime.build_runtime_server({'DATA_ROOT': str(tmp_path), 'OPENAI_API_KEY': 'fixture'})
    assert build.call_args.kwargs['embedder'] is embedder
    assert manager_factory.call_args.args[3].embedder is embedder

    async def check():
        async with lifespan(None):
            embedder.close.assert_not_called()

    asyncio.run(check())
    embedder.close.assert_called_once()
    manager.executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
