"""원격 상세 조회는 미지원 자료를 생성하거나 조회 오류를 노출하지 않는다."""
from unittest.mock import Mock, MagicMock
from uuid import uuid4
import pytest
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries


def test_invalid_input_and_missing_owner_do_not_query():
    pool = Mock()
    queries = RemoteProductQueries(pool)
    queries.owner = Mock(return_value=None)
    for context, offset in [('bad', 0), (str(uuid4()), -1), (str(uuid4()), True)]:
        assert queries.detail(context, offset) == {'status': 'invalid_argument'}
    queries.owner.assert_not_called()
    assert queries.detail(str(uuid4())) == {'status': 'not_found'}
    pool.connection.assert_not_called()


@pytest.mark.parametrize('sections', [None, [{}], [dict(section_id='s', parse_revision_id='wrong')]])
def test_invalid_saved_sections_fail_closed(sections):
    pool = MagicMock()
    queries = RemoteProductQueries(pool)
    queries.owner = Mock(return_value='alice')
    queries.contexts.resolve = Mock(return_value=Mock())
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {
        'title': '합성 논문', 'document_metadata': {'sections': sections}}
    assert queries.detail(str(uuid4())) == {'status': 'unavailable'}


def test_database_failure_is_redacted():
    queries = RemoteProductQueries(Mock())
    queries.owner = Mock(side_effect=RuntimeError('비밀 DB 정보'))
    assert queries.detail(str(uuid4())) == {'status': 'unavailable'}


@pytest.mark.parametrize('parent,level,expected', [
    ('missing', 2, 'unavailable'), ('child', 2, 'unavailable'),
    ('root', 1, 'unavailable'), ('root', 2, 'ok'),
])
def test_section_hierarchy_rejects_missing_parent_and_cycles(parent, level, expected):
    pool = MagicMock()
    queries = RemoteProductQueries(pool)
    queries.owner = Mock(return_value='alice')
    binding = Mock(parse_revision_id='parse')
    queries.contexts.resolve = Mock(return_value=binding)
    root = dict(section_id='root', parse_revision_id='parse', title='부모', ordinal=0,
                level=1, start_pdf_page=1, end_pdf_page=2)
    child = dict(root, section_id='child', title='자식', ordinal=1,
                 level=level, parent_section_id=parent)
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {
        'title': '합성 논문', 'document_metadata': {'sections': [root, child]}}
    assert queries.detail(str(uuid4()))['status'] == expected
