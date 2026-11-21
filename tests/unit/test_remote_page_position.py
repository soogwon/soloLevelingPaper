"""페이지 읽기 성공 시 시작된 학습 기록의 최근 위치를 자동 저장한다."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from solo_leveling.interfaces.http import remote_paper_pages
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries

CONTEXT = str(uuid4())


def queries(monkeypatch, page_result):
    value = RemoteProductQueries(Mock())
    monkeypatch.setattr(value, 'owner', lambda: 'alice')
    monkeypatch.setattr(value.contexts, 'resolve', lambda owner, context: SimpleNamespace(context_id=context))
    monkeypatch.setattr(remote_paper_pages, 'read_paper_page', lambda *args: dict(page_result))
    return value


def test_first_chunk_of_page_saves_position(monkeypatch):
    value = queries(monkeypatch, {'status': 'ok', 'chunks': []})
    value.page_viewed = Mock(return_value=True)
    result = value.read_page(CONTEXT, 3)
    assert result['position_saved'] is True
    value.page_viewed.assert_called_once_with('alice', CONTEXT, 3)


def test_continuation_failure_and_missing_hook_do_not_save(monkeypatch):
    value = queries(monkeypatch, {'status': 'page_out_of_range'})
    value.page_viewed = Mock(return_value=True)
    assert value.read_page(CONTEXT, 9) == {'status': 'page_out_of_range'}
    value.page_viewed.assert_not_called()

    value = queries(monkeypatch, {'status': 'ok', 'chunks': []})
    assert 'position_saved' not in value.read_page(CONTEXT, 3)


def test_save_error_never_blocks_reading(monkeypatch):
    value = queries(monkeypatch, {'status': 'ok', 'chunks': []})
    value.page_viewed = Mock(side_effect=RuntimeError('db'))
    assert value.read_page(CONTEXT, 3) == {'status': 'ok', 'chunks': [], 'position_saved': False}
