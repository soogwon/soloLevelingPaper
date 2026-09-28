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
