from dataclasses import replace
import json
from types import SimpleNamespace as NS

import pytest

from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteRetrievalContractError, RemoteSearchBinding, RemoteSearchFilters, RemoteSearchHit,
)
from solo_leveling.application.evidence_qa.remote_search import (
    FingerprintCheckedEmbedder, QueryEmbedding, RemoteContextSearchService,
)


@pytest.fixture
def binding():
    fp = dict(model_id='model', model_revision='rev', tokenizer_id='tokenizer', tokenizer_revision='rev',
              pooling='mean', normalization='l2', max_length=128, dimension=2, distance='cosine',
              runtime='pytorch', runtime_version='v', precision='float32', artifact_sha256='a' * 64)
    return RemoteSearchBinding('alice', 'ctx', 'paper', 'v', 'past-publication', 'parse', 'tr', 'set', json.dumps(fp))


def test_full_fingerprint_and_valid_embedding_are_passed_without_default_model(binding):
    seen = []
    def embed(question, scope):
        seen.append((question, scope))
        return QueryEmbedding((1., 0.), scope.model_fingerprint)
    assert FingerprintCheckedEmbedder(NS(embed=embed))('synthetic', binding) == (1., 0.)
    assert seen == [('synthetic', binding)]


@pytest.mark.parametrize('field', ['model_revision', 'tokenizer_revision', 'runtime', 'precision', 'pooling'])
def test_same_dimension_different_model_is_not_accepted(binding, field):
    fp = json.loads(binding.model_fingerprint)
    fp[field] = 'other'
    runtime = NS(embed=lambda *_: QueryEmbedding((1., 0.), json.dumps(fp)))
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_MODEL_MISMATCH'):
        FingerprintCheckedEmbedder(runtime)('q', binding)


@pytest.mark.parametrize('vector', [(0., 0.), (1.,), (float('nan'), 0.), (True, 0.), (2., 0.)])
def test_invalid_vectors_fail_before_sql(binding, vector):
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_EMBEDDING_INVALID'):
        FingerprintCheckedEmbedder(NS(embed=lambda *_: QueryEmbedding(vector, binding.model_fingerprint)))('q', binding)


def test_invalid_source_fingerprint_does_not_call_runtime(binding):
    def never(*_): pytest.fail('runtime must not run')
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_FINGERPRINT_INVALID'):
        FingerprintCheckedEmbedder(NS(embed=never))('q', replace(binding, model_fingerprint='{}'))


def test_context_scope_filters_order_and_score_survive_composition(binding):
    seen = []
    filters = RemoteSearchFilters((6,), ('s4',), True)
    def search(question, scope, focus, top_k):
        seen.append((scope, focus, top_k))
        return (RemoteSearchHit(scope, 'chunk', 6, 's4', 0.25),)
    service = RemoteContextSearchService(lambda _: binding, NS(search=search))
    result = service.search('q', 'ctx', filters=filters, top_k=2)
    assert result.binding.publication_id == 'past-publication'
    assert result.filters == filters and result.hits[0].score == 0.8
    assert seen == [(binding, filters, 2)]


def test_missing_context_never_calls_retriever():
    def never(*a): pytest.fail('search must not run')
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_CONTEXT_NOT_FOUND'):
        RemoteContextSearchService(lambda _: None, NS(search=never)).search('q', 'missing')


def test_foreign_result_is_rejected_after_search(binding):
    retriever = NS(search=lambda *_: (RemoteSearchHit(replace(binding, owner_user_id='bob'), 'c', 6, 's4', 0),))
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_SCOPE_MISMATCH'):
        RemoteContextSearchService(lambda _: binding, retriever).search('q', 'ctx')


def test_factory_reuses_authentication_context_and_rechecks_each_request(monkeypatch, binding):
    from solo_leveling.infrastructure.retrieval import remote_factory as factory
    seen = []
    def resolve(context_id, owners, contexts):
        seen.append((context_id, owners, contexts))
        return binding if len(seen) == 1 else None
    monkeypatch.setattr(factory, 'resolve_authenticated_context', resolve)
    monkeypatch.setattr(factory.PostgresRemoteScopedSearch, 'search', lambda *_: ())
    service = factory.create_remote_search_service(object(), object())
    assert service.search('q', 'ctx').hits == ()
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_CONTEXT_NOT_FOUND'):
        service.search('q', 'ctx')
    assert len(seen) == 2
