"""KG 학습 경로 MCP 도구의 계약과 오류 처리를 확인한다."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.learning_path.service import LearningPathService
from solo_leveling.domain.knowledge_graph import build_graph
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_knowledge_graph import HASH, seed
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401


@pytest.fixture
def service(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    with get_connection(db) as conn:
        conn.execute('UPDATE paper_versions SET file_hash=?', (HASH,))
    return LearningPathService(db, build_graph(seed(), 'kg-test'))


def call(server, name, args):
    contents, structured = asyncio.run(server.call_tool(name, args))
    assert json.loads(contents[0].text) == structured
    return structured


def test_tools_are_read_only_and_return_the_path(service):
    server = create_server(MCPServices(Mock(), Mock(), learning_path=service))
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    for name in ('get_learning_path', 'get_prerequisites'):
        hints = tools[name].annotations
        assert hints.readOnlyHint and hints.idempotentHint and not hints.openWorldHint
    assert tools['get_learning_path'].inputSchema['required'] == ['context_id']
    result = call(server, 'get_learning_path', {'context_id': 'ctx'})
    assert result['graph_status'] == 'ready' and result['graph_revision_id'] == 'kg-test'
    assert result['goal'] == 'understand' and result['mode'] == 'compact'
    # 맥락에 저장된 known_concepts('어텐션')는 그래프에 없으므로 경로를 줄이지 않는다.
    assert [s['concept_id'] for s in result['steps']] == ['vector', 'dot', 'softmax', 'attention', 'transformer']
    assert result['steps'][1]['needed_for'] == {'concept_id': 'attention', 'name_ko': 'attention 한글'}
    assert result['steps'][-1]['needed_for'] is None
    assert result['unresolved_concepts'] == ['어텐션']
    assert result['steps'][3]['paper_locations'] == [
        {'pdf_page': 1, 'section_hint': None, 'search_phrase': None}, {'pdf_page': 2, 'section_hint': None, 'search_phrase': None}]


def test_known_concepts_override_and_expanded_mode(service):
    server = create_server(MCPServices(Mock(), Mock(), learning_path=service))
    result = call(server, 'get_learning_path',
                  {'context_id': 'ctx', 'known_concepts': ['내적'], 'mode': 'expanded'})
    assert [s['concept_id'] for s in result['steps']] == ['softmax', 'attention', 'transformer', 'extra']
    assert result['skipped'] == [
        {'concept_id': 'dot', 'name_ko': 'dot 한글', 'reason': 'known'},
        {'concept_id': 'vector', 'name_ko': 'vector 한글', 'reason': 'only_needed_by_known'}]


def test_prerequisites_by_alias(service):
    server = create_server(MCPServices(Mock(), Mock(), learning_path=service))
    result = call(server, 'get_prerequisites', {'context_id': 'ctx', 'concept': 'ATTENTION'})
    assert result['concept']['concept_id'] == 'attention'
    assert [p['concept_id'] for p in result['prerequisites']] == ['dot', 'softmax']
    assert result['required_by'] == [{'concept_id': 'transformer', 'name_ko': 'transformer 한글'}]


def test_paper_without_graph_is_not_an_error(service):
    with get_connection(service.db_path) as conn:
        conn.execute("UPDATE paper_versions SET file_hash='other'")
    server = create_server(MCPServices(Mock(), Mock(), learning_path=service))
    path = call(server, 'get_learning_path', {'context_id': 'ctx'})
    assert path['graph_status'] == 'no_graph' and path['steps'] == [] and path['graph_revision_id'] is None
    prereq = call(server, 'get_prerequisites', {'context_id': 'ctx', 'concept': 'anything'})
    assert prereq['concept'] is None and prereq['prerequisites'] == []


@pytest.mark.parametrize('name, args, code', [
    ('get_learning_path', {'context_id': '00000000-0000-4000-8000-000000000001'}, 'NOT_FOUND'),
    ('get_learning_path', {'context_id': 'not-a-uuid'}, 'INVALID_ARGUMENT'),
    ('get_learning_path', {'context_id': 'ctx', 'known_concepts': ['a', 'a']}, 'INVALID_ARGUMENT'),
    ('get_learning_path', {'context_id': 'ctx', 'known_concepts': [' ']}, 'INVALID_ARGUMENT'),
    ('get_prerequisites', {'context_id': 'ctx', 'concept': '없는 개념'}, 'NOT_FOUND'),
])
def test_errors_use_existing_codes(service, name, args, code):
    server = create_server(MCPServices(Mock(), Mock(), learning_path=service))
    with pytest.raises(ToolError, match=code):
        asyncio.run(server.call_tool(name, args))


def test_invalid_mode_is_rejected_before_service():
    reader = Mock()
    server = create_server(MCPServices(Mock(), Mock(), learning_path=reader))
    with pytest.raises(Exception):
        asyncio.run(server.call_tool('get_learning_path', {'context_id': 'ctx', 'mode': 'full'}))
    reader.get_learning_path.assert_not_called()


def test_start_learning_lists_tools_only_when_paper_has_graph(service):
    contexts = Mock()
    contexts.get_or_create_learning_context.return_value = Mock(
        context_id='ctx', version_id='v1', goal='understand', known_concepts=[])
    server = create_server(MCPServices(Mock(), Mock(), contexts=contexts, learning_path=service))
    result = call(server, 'start_learning', {'version_id': 'v1'})
    assert {'get_learning_path', 'get_prerequisites'} <= set(result['capabilities'])
    with get_connection(service.db_path) as conn:
        conn.execute("UPDATE paper_versions SET file_hash='other'")
    result = call(server, 'start_learning', {'version_id': 'v1'})
    assert 'get_learning_path' not in result['capabilities']


def related_service(service, arxiv_id):
    """검수 판본과 파일 해시는 다르지만 arXiv 번호가 같은 PDF를 등록한 상태."""
    data = seed()
    data['papers'][0]['source_url'] = 'https://arxiv.org/pdf/1706.03762v7'
    with get_connection(service.db_path) as conn:
        conn.execute("UPDATE paper_versions SET file_hash='other-version'")
    reads = []

    def reader(path):
        reads.append(path)
        return arxiv_id

    return LearningPathService(service.db_path, build_graph(data, 'kg-test'), arxiv_reader=reader), reads


def test_other_version_of_reviewed_paper_shares_path_without_pages(service):
    """#343: 같은 논문의 다른 판본은 개념 순서를 쓰되 페이지·찾을 말은 숨긴다."""
    related, reads = related_service(service, '1706.03762')
    server = create_server(MCPServices(Mock(), Mock(), learning_path=related))
    path = call(server, 'get_learning_path', {'context_id': 'ctx'})
    assert path['graph_status'] == 'related_version' and path['graph_revision_id'] == 'kg-test'
    assert path['reviewed_version'] == {'label': 'Paper', 'source_url': 'https://arxiv.org/pdf/1706.03762v7'}
    assert [s['concept_id'] for s in path['steps']] == ['vector', 'dot', 'softmax', 'attention', 'transformer']
    assert all(step['paper_locations'] == [] for step in path['steps'])
    prereq = call(server, 'get_prerequisites', {'context_id': 'ctx', 'concept': 'attention'})
    assert prereq['graph_status'] == 'related_version'
    assert [p['concept_id'] for p in prereq['prerequisites']] == ['dot', 'softmax']
    assert prereq['paper_locations'] == []
    assert related.has_graph('v1') is True
    # 같은 파일의 arXiv 표기는 한 번만 읽는다.
    assert len(reads) == 1


def test_reviewed_version_reports_its_label_only_when_related(service):
    server = create_server(MCPServices(Mock(), Mock(), learning_path=service))
    assert call(server, 'get_learning_path', {'context_id': 'ctx'})['reviewed_version'] is None


@pytest.mark.parametrize('arxiv_id', [None, '2010.11929'])
def test_unrelated_or_unstamped_pdf_stays_without_graph(service, arxiv_id):
    related, _ = related_service(service, arxiv_id)
    server = create_server(MCPServices(Mock(), Mock(), learning_path=related))
    path = call(server, 'get_learning_path', {'context_id': 'ctx'})
    assert path['graph_status'] == 'no_graph' and path['steps'] == [] and path['reviewed_version'] is None
    assert related.has_graph('v1') is False
