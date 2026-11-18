"""원격 입력 경계와 로컬 검수 계산의 일치 여부를 검사한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from solo_leveling.interfaces.mcp.remote_learning_path import RemoteLearningPath
from solo_leveling.application.learning_path.views import path_view
from solo_leveling.domain.knowledge_graph import learning_path


def reader():
    r = RemoteLearningPath(Mock())
    b = NS(context_id=str(uuid4()), paper_id=str(uuid4()), server_version_id='v1', publication_id=str(uuid4()))
    r._context = Mock(return_value=(b, r.graph.papers[0].file_sha256))
    return r, b


def test_same_reviewed_calculation_and_prerequisites():
    r, b = reader()
    result = r.read(b.context_id, known=['attention'], mode='expanded')['result']
    expected = path_view(learning_path(r.graph, r.graph.papers[0], 'understand', ['attention'], 'expanded'))
    assert result['steps'] == expected['steps']
    assert result['skipped'] == expected['skipped']
    assert result['graph_status'] == 'ready'
    concept = next(iter(r.graph.papers[0].concepts))
    assert r.read(b.context_id, concept=concept)['result']['concept']['concept_id'] == concept
    assert r.read(b.context_id, concept='존재하지 않는 개념')['error_code'] == 'NOT_FOUND'


@pytest.mark.parametrize('kw', [dict(known=['x']*51),dict(known=['x'*201]),dict(known=['Attention',' attention ']),dict(known=['']),dict(known='abc'),dict(mode='bad'),dict(concept='')])
def test_invalid_inputs_never_query(kw):
    r, b = reader()
    assert r.read(b.context_id, **kw)['error_code'] == 'INVALID_ARGUMENT'
    r._context.assert_not_called()


def test_unknown_pdf_and_seed_failure_never_invent_locations():
    r, b = reader()
    r._context.return_value = (b, 'a'*64)
    result = r.read(b.context_id)['result']
    assert result['graph_status'] == 'no_graph' and result['steps'] == []
    assert result['graph_revision_id'] == r.graph.revision_id
    r.graph = None
    assert r.read(b.context_id)['error_code'] == 'KNOWLEDGE_GRAPH_UNAVAILABLE'
    r._context.return_value = None
    assert r.read(b.context_id)['error_code'] == 'NOT_FOUND'
